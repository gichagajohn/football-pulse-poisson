"""Fetch finished matches from football-data.org (free tier, paced)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime

import httpx

from backend.config import FD_MIN_INTERVAL_SECONDS, env
from backend.model.ratings import MatchResult, european_season_start_year

logger = logging.getLogger(__name__)

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"


_SHARED: FootballDataClient | None = None


def shared_client() -> "FootballDataClient":
    global _SHARED
    if _SHARED is None:
        _SHARED = FootballDataClient()
    return _SHARED


class FootballDataClient:
    """Serialises calls so we stay under 10 req/min on the free tier."""

    def __init__(self, min_interval: float = FD_MIN_INTERVAL_SECONDS):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def get(self, http: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
        async with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await http.get(
                f"{FOOTBALL_DATA_BASE}{path}",
                headers={"X-Auth-Token": env("FOOTBALL_DATA_KEY")},
                params=params or {},
                timeout=30,
            )
            self._last = time.monotonic()
            resp.raise_for_status()
            return resp.json()


def _parse_finished(payload: dict, competition_code: str, season: int) -> list[MatchResult]:
    out: list[MatchResult] = []
    for m in payload.get("matches") or []:
        status = (m.get("status") or "").upper()
        if status not in {"FINISHED", "AWARDED"}:
            continue
        score = (m.get("score") or {}).get("fullTime") or {}
        hg, ag = score.get("home"), score.get("away")
        if hg is None or ag is None:
            continue
        raw_date = m.get("utcDate") or ""
        try:
            played = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        if not home.get("id") or not away.get("id"):
            continue
        code = competition_code
        if m.get("competition") and m["competition"].get("code"):
            code = m["competition"]["code"]
        out.append(
            MatchResult(
                home_id=int(home["id"]),
                away_id=int(away["id"]),
                home_goals=int(hg),
                away_goals=int(ag),
                played_on=played,
                competition=code,
                home_name=home.get("name") or "Unknown",
                away_name=away.get("name") or "Unknown",
                season=season,
            )
        )
    return out


async def fetch_history(
    codes: set[str],
    as_of: date,
    client: FootballDataClient | None = None,
) -> list[MatchResult]:
    """Current season + previous season for every competition with fixtures today."""
    if not codes:
        return []
    fd = client or shared_client()
    current = european_season_start_year(as_of)
    seasons = [current, current - 1]
    results: list[MatchResult] = []

    async with httpx.AsyncClient(timeout=30) as http:
        for code in sorted(codes):
            for season in seasons:
                try:
                    payload = await fd.get(
                        http,
                        f"/competitions/{code}/matches",
                        params={"season": season, "status": "FINISHED"},
                    )
                    parsed = _parse_finished(payload, code, season)
                    logger.info("[HISTORY] %s season %s: %s finished matches.", code, season, len(parsed))
                    results.extend(parsed)
                except Exception as exc:
                    logger.warning("[HISTORY] Failed %s season %s: %s", code, season, exc)
    return results
