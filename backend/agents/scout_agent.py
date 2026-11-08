"""
SCOUT — fixtures, odds, injuries, kickoff weather. No LLM.

Completeness is computed in Python from which fields actually arrived.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import httpx

from backend.cities import get_venue_city
from backend.config import (
    FIXTURE_MATCH_TOLERANCE_MINUTES,
    ODDS_FETCH_BTTS,
    ODDS_MIN_CREDITS_REMAINING,
    ODDS_REGIONS,
    ODDS_SPORT_KEYS,
    env,
    load_league_ids,
)
from backend.model.history import FootballDataClient, shared_client

logger = logging.getLogger(__name__)


class OddsQuotaExhausted(RuntimeError):
    """Raised when The Odds API refuses to serve more requests.

    Auth failures (401/403) and rate/credit exhaustion (429, or
    x-requests-remaining at or below the configured floor) are terminal for
    the run: continuing would silently produce an odds-free ticket that looks
    exactly like a genuine NO BET.
    """


# Credits left on the plan, refreshed from the response headers. None = unknown.
_CREDITS_REMAINING: int | None = None


def credits_remaining() -> int | None:
    return _CREDITS_REMAINING


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

# Club-name tokens that carry no identifying information.
_LEGAL_TOKENS = {
    "fc", "cf", "afc", "cd", "sd", "ac", "sc", "ss", "as", "sv", "vfb", "vfl",
    "bk", "if", "ff", "calcio", "club", "de", "futbol", "football", "soccer",
    "the",
}


def _fold(name: str) -> str:
    """Lowercase, strip accents, drop punctuation: 'Mönchengladbach' → 'monchengladbach'."""
    cleaned = unicodedata.normalize("NFKD", name or "")
    cleaned = "".join(c for c in cleaned if not unicodedata.combining(c))
    cleaned = cleaned.lower().replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _tokens(name: str) -> frozenset[str]:
    parts = [p for p in _fold(name).split(" ") if p]
    core = [p for p in parts if p not in _LEGAL_TOKENS]
    return frozenset(core or parts)


def _normalize_team_name(name: str) -> str:
    """Legacy flat string form, kept for stability in logs and FPL lookups."""
    return _fold(name)


def _teams_match(left: str, right: str) -> bool:
    """Token-set comparison instead of substring 'in'.

    Substrings bind the wrong club ('Sporting' in 'Sporting CP' and
    'Sporting Lisbon'; 'Inter' in 'Inter Milan' and 'Internazionale'), and a
    wrong bind writes the wrong fixture_id, which then grades the bet against
    somebody else's match.
    """
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if not short.issubset(long):
        return False
    # Require enough signal in the shorter name: 'real' must not match
    # 'real madrid', but 'inter' may match 'inter milan'.
    return len("".join(sorted(short))) >= 5


def _kickoff_delta_minutes(left: str | None, right: str | None) -> float | None:
    def parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            return None

    a, b = parse(left), parse(right)
    if a is None or b is None:
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return abs((a - b).total_seconds()) / 60.0


def _find_match_odds(odds_events: list[dict], home_name: str, away_name: str) -> dict:
    for event in odds_events:
        if _teams_match(event.get("home_team", ""), home_name) and _teams_match(
            event.get("away_team", ""), away_name
        ):
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


def _kickoff_on_day(utc_iso: str, target_date: date) -> datetime | None:
    try:
        kickoff = datetime.fromisoformat((utc_iso or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if kickoff.date() != target_date:
        return None
    return kickoff


def _odds_event_to_fixture(event: dict, code: str) -> dict:
    return {
        "id": event.get("id"),
        "utcDate": event.get("commence_time"),
        "status": "TIMED",
        "homeTeam": {"id": None, "name": event.get("home_team")},
        "awayTeam": {"id": None, "name": event.get("away_team")},
        "competition": {"code": code},
        "_competition_code": code,
        "_odds_event": event,
    }


async def fetch_fixtures_from_odds(target_date: date) -> list[dict]:
    """Free fixture list from The Odds API (same key you already have)."""
    leagues = load_league_ids()
    now = datetime.now(timezone.utc)
    kept: list[dict] = []
    for code in leagues:
        sport_key = ODDS_SPORT_KEYS.get(code)
        if not sport_key:
            continue
        events = await fetch_league_odds(sport_key)
        await asyncio.sleep(0.3)
        n_today = 0
        for event in events:
            kickoff = _kickoff_on_day(event.get("commence_time") or "", target_date)
            if kickoff is None or kickoff <= now:
                continue
            n_today += 1
            kept.append(_odds_event_to_fixture(event, code))
        logger.info("[SCOUT] Odds API %s: %s event(s) on %s.", code, n_today, target_date)
    return kept


async def fetch_fixtures_from_football_data(target_date: date, fd: FootballDataClient | None = None) -> list[dict]:
    leagues = load_league_ids()
    fd = fd or shared_client()
    now = datetime.now(timezone.utc)
    date_from = target_date.isoformat()
    date_to = (target_date + timedelta(days=1)).isoformat()
    matches: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as http:
        for code in leagues:
            try:
                payload = await fd.get(
                    http,
                    f"/competitions/{code}/matches",
                    params={"dateFrom": date_from, "dateTo": date_to},
                )
                chunk = payload.get("matches") or []
                logger.info("[SCOUT] football-data %s raw=%s", code, len(chunk))
                for m in chunk:
                    m.setdefault("competition", {})["code"] = code
                matches.extend(chunk)
            except Exception as exc:
                logger.warning("[SCOUT] football-data %s failed: %s", code, exc)
    kept: list[dict] = []
    for m in matches:
        code = (m.get("competition") or {}).get("code")
        if code not in leagues:
            continue
        if (m.get("status") or "").upper() not in PLAYABLE_STATUS:
            continue
        kickoff = _kickoff_on_day(m.get("utcDate") or "", target_date)
        if kickoff is None or kickoff <= now:
            continue
        m["_competition_code"] = code
        kept.append(m)
    return kept


def _merge_fd_ids(odds_fixtures: list[dict], fd_fixtures: list[dict]) -> list[dict]:
    """Attach football-data.org IDs to Odds API fixtures — or drop the fixture.

    A candidate only binds when ALL of these hold:

      1. home and away both match on token sets (not substrings)
      2. same competition code
      3. kickoff times within FIXTURE_MATCH_TOLERANCE_MINUTES
      4. exactly one fixture competes for the bind (ambiguity → drop)
      5. the matched football-data record actually carries an id

    Rule 4 is the important one: a 50/50 guess here silently grades the bet
    against a different match, which corrupts the weekly ROI rather than
    raising anything.
    """
    index: list[tuple[str, str, str, dict]] = []
    for fd in fd_fixtures:
        index.append(
            (
                (fd.get("homeTeam") or {}).get("name") or "",
                (fd.get("awayTeam") or {}).get("name") or "",
                (fd.get("competition") or {}).get("code") or "",
                fd,
            )
        )

    matched: list[dict] = []
    used_ids: set = set()
    unmatched = 0
    ambiguous = 0

    for fx in odds_fixtures:
        home = (fx.get("homeTeam") or {}).get("name") or ""
        away = (fx.get("awayTeam") or {}).get("name") or ""
        code = fx.get("_competition_code") or ""
        kickoff = fx.get("utcDate")

        candidates: list[dict] = []
        for fd_home, fd_away, fd_code, fd in index:
            if not _teams_match(fd_home, home) or not _teams_match(fd_away, away):
                continue
            if code and fd_code and code != fd_code:
                continue
            delta = _kickoff_delta_minutes(kickoff, fd.get("utcDate"))
            if delta is None or delta > FIXTURE_MATCH_TOLERANCE_MINUTES:
                continue
            if fd.get("id") is None:
                continue
            if fd["id"] in used_ids:
                continue
            candidates.append(fd)

        if not candidates:
            unmatched += 1
            logger.warning(
                "[SCOUT] No football-data match for %s vs %s (%s, %s) — fixture dropped.",
                home,
                away,
                code or "?",
                kickoff or "?",
            )
            continue
        if len(candidates) > 1:
            ambiguous += 1
            logger.warning(
                "[SCOUT] %s vs %s matched %s football-data fixtures — dropped as ambiguous.",
                home,
                away,
                len(candidates),
            )
            continue

        fd = candidates[0]
        fx["id"] = fd["id"]
        fx["homeTeam"]["id"] = (fd.get("homeTeam") or {}).get("id")
        fx["awayTeam"]["id"] = (fd.get("awayTeam") or {}).get("id")
        used_ids.add(fd["id"])
        matched.append(fx)

    logger.info(
        "[SCOUT] Matched %s/%s Odds API fixture(s) to football-data IDs "
        "(%s unmatched, %s ambiguous, dropped).",
        len(matched),
        len(odds_fixtures),
        unmatched,
        ambiguous,
    )
    return matched


async def fetch_fixtures(target_date: date, fd: FootballDataClient | None = None) -> list[dict]:
    leagues = load_league_ids()
    odds_fx = await fetch_fixtures_from_odds(target_date)
    fd_fx: list[dict] = []
    try:
        fd_fx = await fetch_fixtures_from_football_data(target_date, fd=fd)
    except Exception as exc:
        logger.warning("[SCOUT] football-data enrich failed: %s", exc)
    if odds_fx:
        kept = _merge_fd_ids(odds_fx, fd_fx)
        if kept:
            logger.info("[SCOUT] %s upcoming fixture(s) from Odds API (free).", len(kept))
            return kept
        logger.warning(
            "[SCOUT] Odds fixtures could not be matched to football-data IDs; "
            "falling back to football-data fixtures without odds."
        )
    if fd_fx:
        logger.info("[SCOUT] %s upcoming fixture(s) from football-data.org (free).", len(fd_fx))
        return fd_fx
    logger.info("[SCOUT] 0 upcoming fixture(s) in %s.", list(leagues.values()))
    return []


def _merge_btts_into_featured(featured: list[dict], btts: list[dict]) -> list[dict]:
    """Merge a separate btts odds response into the featured (h2h/totals) response, by event id."""
    btts_by_id = {ev.get("id"): ev for ev in btts if ev.get("id")}
    for event in featured:
        btts_event = btts_by_id.get(event.get("id"))
        if not btts_event:
            continue
        btts_books_by_key = {b.get("key"): b for b in btts_event.get("bookmakers") or []}
        for book in event.get("bookmakers") or []:
            btts_book = btts_books_by_key.get(book.get("key"))
            if btts_book:
                book.setdefault("markets", []).extend(btts_book.get("markets") or [])
    return featured


async def _fetch_odds_market(sport_key: str, markets: str) -> list[dict]:
    global _CREDITS_REMAINING
    async with httpx.AsyncClient(timeout=20) as http:
        try:
            resp = await http.get(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                params={
                    "apiKey": env("ODDS_API_KEY"),
                    "regions": ODDS_REGIONS,
                    "markets": markets,
                    "oddsFormat": "decimal",
                },
            )
        except Exception as exc:
            logger.warning("[SCOUT] Odds fetch failed for %s (%s): %s", sport_key, markets, exc)
            return []

    # Credit accounting first: this header is free and decides whether we may
    # keep spending. It arrives even on otherwise-unhelpful responses.
    raw_remaining = resp.headers.get("x-requests-remaining")
    if raw_remaining is not None:
        try:
            _CREDITS_REMAINING = int(float(raw_remaining))
            logger.info("[SCOUT] Odds API credits remaining: %s", _CREDITS_REMAINING)
        except ValueError:
            pass

    if resp.status_code in {401, 403}:
        raise OddsQuotaExhausted(
            f"Odds API rejected the key (HTTP {resp.status_code}). Check ODDS_API_KEY."
        )
    if resp.status_code == 429:
        raise OddsQuotaExhausted("Odds API rate/credit limit hit (HTTP 429).")
    if _CREDITS_REMAINING is not None and _CREDITS_REMAINING <= ODDS_MIN_CREDITS_REMAINING:
        raise OddsQuotaExhausted(
            f"Odds API credits remaining ({_CREDITS_REMAINING}) at or below the "
            f"floor ({ODDS_MIN_CREDITS_REMAINING})."
        )
    if resp.status_code == 422:
        # BTTS is not a featured market for every league/plan. This is an
        # expected capability response, not a pipeline failure.
        logger.info(
            "[SCOUT] Odds market unavailable for %s (%s); continuing without it.",
            sport_key,
            markets,
        )
        return []
    if resp.status_code != 200:
        logger.warning(
            "[SCOUT] Odds fetch for %s (%s) returned HTTP %s — skipping.",
            sport_key,
            markets,
            resp.status_code,
        )
        return []
    data = resp.json()
    return data if isinstance(data, list) else []


async def fetch_league_odds(sport_key: str) -> list[dict]:
    """
    The Odds API's /odds endpoint only accepts "featured" markets, and will
    reject the ENTIRE request (422) if any requested market isn't featured
    for that endpoint — even if the other markets are fine.

    Cost: one credit per market per region. h2h + totals = 2 credits per
    league per run. BTTS is a THIRD credit per league per day, which on the
    500-credit free plan is the difference between ~360/month (fits) and
    ~540/month (dies mid-month). It is therefore off unless ODDS_FETCH_BTTS=1;
    when it is on, it is fetched separately and merged by event id so a 422
    cannot take down the core odds as well.
    """
    featured = await _fetch_odds_market(sport_key, "h2h,totals")
    if not featured:
        return []
    if ODDS_FETCH_BTTS:
        btts = await _fetch_odds_market(sport_key, "btts")
        if btts:
            featured = _merge_btts_into_featured(featured, btts)
    return featured


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

    fd = fd or shared_client()
    fixtures = await fetch_fixtures(target_date, fd=fd)
    if not fixtures:
        logger.warning("[SCOUT] No upcoming fixtures.")
        return []

    codes_present = {f["_competition_code"] for f in fixtures}

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
        matched = fixture.get("_odds_event") or {}
        odds_snapshot = _extract_odds_snapshot(matched) if matched else {}

        venue_city = get_venue_city(home_name)
        weather = await fetch_weather_at_kickoff(venue_city, fixture.get("utcDate"))

        home_injuries = _lookup_epl_injuries(epl_injuries, home_name) if code == "PL" else []
        away_injuries = _lookup_epl_injuries(epl_injuries, away_name) if code == "PL" else []

        raw_id = fixture.get("id")
        if isinstance(raw_id, int):
            fixture_id = raw_id
        elif raw_id:
            fixture_id = int(hashlib.sha256(str(raw_id).encode()).hexdigest()[:15], 16)
        else:
            fixture_id = int(
                hashlib.sha256(f"{home_name}|{away_name}|{fixture.get('utcDate')}".encode()).hexdigest()[:15],
                16,
            )
        structured = {
            "fixture_id": fixture_id,
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
