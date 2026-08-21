"""
SCOUT — fixtures, odds, injuries, kickoff weather. No LLM.

Completeness is computed in Python from which fields actually arrived.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone

import httpx

from backend.cities import get_venue_city
from backend.config import ODDS_SPORT_KEYS, env, load_league_ids
from backend.model.history import FootballDataClient, shared_client

logger = logging.getLogger(__name__)

FPL_NAME_ALIASES = {
    "Nott'm Forest": "Nottingham Forest",
    "Spurs": "Tottenham Hotspur",
    "Man Utd": "Manchester United",
    "Man City": "Manchester City",
    "Newcastle": "Newcastle United",
    "Leeds": "Leeds United",
    "Wolves": "Wolverhampton Wanderers",
}

PLAYABLE_STATUS = {"TIMED", "SCHEDULED"}


def _normalize_team_name(name: str) -> str:
    name = (name or "").strip()
    for suffix in (" FC", " CF", " AFC", " CD", " SD", " AC"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    name = name.replace("&", "and")
    name = re.sub(r"\s+", " ", name)
    return name.strip().lower()


def _find_match_odds(odds_events: list[dict], home_name: str, away_name: str) -> dict:
    home_norm = _normalize_team_name(home_name)
    away_norm = _normalize_team_name(away_name)
    for event in odds_events:
        eh = _normalize_team_name(event.get("home_team", ""))
        ea = _normalize_team_name(event.get("away_team", ""))
        if (eh in home_norm or home_norm in eh) and (ea in away_norm or away_norm in ea):
            return event
    return {}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _extract_odds_snapshot(odds_event: dict) -> dict:
    """Median price across books — more stable than bookmakers[0]."""
    books = odds_event.get("bookmakers") or []
    home_team = odds_event.get("home_team")
    away_team = odds_event.get("away_team")
    buckets: dict[str, list[float]] = {
        "home_win": [],
        "draw": [],
        "away_win": [],
        "over25": [],
        "btts_yes": [],
    }
    for book in books:
        for market in book.get("markets") or []:
            key = market.get("key")
            for outcome in market.get("outcomes") or []:
                price = outcome.get("price")
                try:
                    price_f = float(price)
                except (TypeError, ValueError):
                    continue
                name = outcome.get("name") or ""
                if key == "h2h":
                    if name == home_team:
                        buckets["home_win"].append(price_f)
                    elif name == away_team:
                        buckets["away_win"].append(price_f)
                    elif name.lower() == "draw":
                        buckets["draw"].append(price_f)
                elif key == "totals" and outcome.get("point") == 2.5 and name.lower() == "over":
                    buckets["over25"].append(price_f)
                elif key == "btts" and name.lower() == "yes":
                    buckets["btts_yes"].append(price_f)
    snapshot = {}
    for k, vals in buckets.items():
        med = _median(vals)
        if med is not None:
            snapshot[k] = round(med, 3)
    return snapshot


async def fetch_fixtures(target_date: date, fd: FootballDataClient | None = None) -> list[dict]:
    leagues = load_league_ids()
    date_str = target_date.isoformat()
    fd = fd or shared_client()
    now = datetime.now(timezone.utc)
    kept: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as http:
        try:
            payload = await fd.get(
                http,
                "/matches",
                params={
                    "dateFrom": date_str,
                    "dateTo": date_str,
                    "competitions": ",".join(leagues.keys()),
                },
            )
            matches = payload.get("matches") or []
        except Exception as exc:
            logger.warning("[SCOUT] Combined fixture fetch failed (%s) — falling back per league.", exc)
            matches = []
            for code in leagues:
                try:
                    payload = await fd.get(
                        http,
                        f"/competitions/{code}/matches",
                        params={"dateFrom": date_str, "dateTo": date_str},
                    )
                    chunk = payload.get("matches") or []
                    for m in chunk:
                        m.setdefault("competition", {})["code"] = code
                    matches.extend(chunk)
                except Exception as exc2:
                    logger.warning("[SCOUT] Fixture fetch failed for %s: %s", code, exc2)

    for m in matches:
        code = (m.get("competition") or {}).get("code")
        if code not in leagues:
            continue
        status = (m.get("status") or "").upper()
        if status not in PLAYABLE_STATUS:
            continue
        utc = m.get("utcDate") or ""
        try:
            kickoff = datetime.fromisoformat(utc.replace("Z", "+00:00"))
            if kickoff <= now:
                continue
        except ValueError:
            continue
        m["_competition_code"] = code
        kept.append(m)

    logger.info("[SCOUT] %s upcoming fixture(s) in %s.", len(kept), list(leagues.values()))
    return kept


async def fetch_league_odds(sport_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as http:
        try:
            resp = await http.get(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                params={
                    "apiKey": env("ODDS_API_KEY"),
                    "regions": "eu",
                    "markets": "h2h,totals,btts",
                    "oddsFormat": "decimal",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("[SCOUT] Odds fetch failed for %s: %s", sport_key, exc)
            return []


async def fetch_epl_injuries() -> dict[str, list[dict]]:
    async with httpx.AsyncClient(timeout=15) as http:
        try:
            resp = await http.get("https://fantasy.premierleague.com/api/bootstrap-static/")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("[SCOUT] FPL injury fetch failed: %s", exc)
            return {}

    teams_by_id = {t["id"]: t["name"] for t in data.get("teams", [])}
    injuries_by_team: dict[str, list[dict]] = {}
    for player in data.get("elements", []):
        if player.get("status") == "a":
            continue
        team_name = teams_by_id.get(player.get("team"))
        if not team_name:
            continue
        injuries_by_team.setdefault(team_name, []).append(
            {
                "player": player.get("web_name"),
                "status": player.get("status"),
                "news": player.get("news") or "No details provided",
                "chance_of_playing_next_round": player.get("chance_of_playing_next_round"),
            }
        )
    return injuries_by_team


def _lookup_epl_injuries(injuries_by_team: dict, fd_team_name: str) -> list[dict]:
    target = _normalize_team_name(fd_team_name)
    for fpl_name, injuries in injuries_by_team.items():
        candidate = _normalize_team_name(FPL_NAME_ALIASES.get(fpl_name, fpl_name))
        if candidate == target or candidate in target or target in candidate:
            return injuries
    return []


async def fetch_weather_at_kickoff(venue_city: str | None, kickoff_utc: str | None) -> dict:
    unknown = {"temp_c": None, "wind_kmh": None, "rain_mm": 0, "conditions": "unknown"}
    if not venue_city:
        return unknown
    async with httpx.AsyncClient(timeout=10) as http:
        try:
            resp = await http.get(
                "https://api.openweathermap.org/data/2.5/forecast",
                params={"q": venue_city, "appid": env("OPENWEATHER_KEY"), "units": "metric"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("[SCOUT] Weather forecast failed for %s: %s", venue_city, exc)
            return unknown

    target = None
    if kickoff_utc:
        try:
            target = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
        except ValueError:
            target = None

    slots = data.get("list") or []
    if not slots:
        return unknown

    def slot_dt(slot: dict) -> datetime:
        return datetime.fromtimestamp(slot.get("dt", 0), tz=timezone.utc)

    if target:
        chosen = min(slots, key=lambda s: abs((slot_dt(s) - target).total_seconds()))
    else:
        chosen = slots[0]

    wind_ms = (chosen.get("wind") or {}).get("speed") or 0
    weather = {
        "temp_c": (chosen.get("main") or {}).get("temp"),
        "wind_kmh": round(float(wind_ms) * 3.6, 1),
        "rain_mm": (chosen.get("rain") or {}).get("3h", 0) or 0,
        "conditions": ((chosen.get("weather") or [{}])[0].get("description") or "unknown"),
    }
    logger.info(
        "[SCOUT] Weather %s @ kickoff: %s, %s°C, wind %s km/h",
        venue_city,
        weather["conditions"],
        weather["temp_c"],
        weather["wind_kmh"],
    )
    return weather


def _completeness(odds_snapshot: dict, weather: dict, has_history_hook: bool = False) -> float:
    score = 0.4  # we always have team names if we got here
    if odds_snapshot.get("home_win") and odds_snapshot.get("away_win"):
        score += 0.4
    elif odds_snapshot:
        score += 0.2
    if weather.get("temp_c") is not None:
        score += 0.1
    if has_history_hook:
        score += 0.1
    return round(min(score, 1.0), 2)


def _weather_risk(weather: dict) -> dict:
    wind = weather.get("wind_kmh") or 0
    rain = weather.get("rain_mm") or 0
    temp = weather.get("temp_c")
    if wind > 60:
        return {"level": "high", "reason": f"wind {wind} km/h"}
    if rain >= 8 or (temp is not None and temp <= 0):
        return {"level": "medium", "reason": weather.get("conditions") or "adverse"}
    if weather.get("conditions") == "unknown":
        return {"level": "unknown", "reason": "no forecast"}
    return {"level": "low", "reason": weather.get("conditions") or "ok"}


async def run(target_date: date | None = None, fd: FootballDataClient | None = None) -> list[dict]:
    target_date = target_date or date.today()
    leagues = load_league_ids()
    logger.info("[SCOUT] %s — leagues %s", target_date, list(leagues.values()))

    fd = fd or FootballDataClient()
    fixtures = await fetch_fixtures(target_date, fd=fd)
    if not fixtures:
        logger.warning("[SCOUT] No upcoming fixtures.")
        return []

    codes_present = {f["_competition_code"] for f in fixtures}
    odds_by_league: dict[str, list[dict]] = {}
    for code in codes_present:
        sport_key = ODDS_SPORT_KEYS.get(code)
        if not sport_key:
            continue
        odds_by_league[code] = await fetch_league_odds(sport_key)
        await asyncio.sleep(0.4)

    epl_injuries: dict[str, list[dict]] = {}
    if "PL" in codes_present:
        epl_injuries = await fetch_epl_injuries()

    results: list[dict] = []
    for fixture in fixtures:
        code = fixture["_competition_code"]
        home = fixture["homeTeam"]
        away = fixture["awayTeam"]
        home_name = home.get("name") or "?"
        away_name = away.get("name") or "?"
        events = odds_by_league.get(code, [])
        matched = _find_match_odds(events, home_name, away_name)
        odds_snapshot = _extract_odds_snapshot(matched) if matched else {}

        venue_city = get_venue_city(home_name)
        weather = await fetch_weather_at_kickoff(venue_city, fixture.get("utcDate"))

        home_injuries = _lookup_epl_injuries(epl_injuries, home_name) if code == "PL" else []
        away_injuries = _lookup_epl_injuries(epl_injuries, away_name) if code == "PL" else []

        structured = {
            "fixture_id": fixture.get("id"),
            "home_team_id": home.get("id"),
            "away_team_id": away.get("id"),
            "home_team": home_name,
            "away_team": away_name,
            "league": leagues.get(code, code),
            "competition_code": code,
            "kickoff_utc": fixture.get("utcDate"),
            "status": fixture.get("status"),
            "odds_snapshot": odds_snapshot,
            "injuries": {"home": home_injuries, "away": away_injuries},
            "weather": weather,
            "weather_risk": _weather_risk(weather),
            "venue_city": venue_city,
            "data_completeness": _completeness(odds_snapshot, weather),
        }
        logger.info(
            "[SCOUT] %s vs %s (%s) odds=%s completeness=%s",
            home_name,
            away_name,
            code,
            "yes" if odds_snapshot else "no",
            structured["data_completeness"],
        )
        results.append(structured)

    return results
