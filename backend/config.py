"""
Single config for Football Pulse AI.

Every threshold lives here so Scout / model / pipeline cannot drift.
All of them can be overridden with env vars (blank = use default).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("football_pulse")


def env(name: str, default: str = "") -> str:
    raw = os.environ.get(name, "")
    raw = raw.strip() if raw else ""
    return raw if raw else default


def float_env(name: str, default: float) -> float:
    raw = env(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Env var %s=%r is not a float — using %s.", name, raw, default)
        return default


def int_env(name: str, default: int) -> int:
    raw = env(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Env var %s=%r is not an int — using %s.", name, raw, default)
        return default


# ── Product: independent singles, not an accumulator ────────────────────────
# A pick must be BOTH likely (model_p) AND +EV vs the book. That is what
# "sure" means here: we will happily email NO BET rather than invent confidence.
MAX_SELECTIONS = int_env("MAX_SELECTIONS", 3)
MIN_WEIGHTED_GAMES = float_env("MIN_WEIGHTED_GAMES", 6.0)
HALF_LIFE_DAYS = float_env("HALF_LIFE_DAYS", 120.0)
PREVIOUS_SEASON_FACTOR = float_env("PREVIOUS_SEASON_FACTOR", 0.45)
DIXON_COLES_RHO = float_env("DIXON_COLES_RHO", -0.10)
MAX_GOALS = 8

# Hard weather reject (km/h). Unknown weather does NOT reject.
WIND_REJECT_KMH = float_env("WIND_REJECT_KMH", 60.0)

# football-data.org free tier is 10 requests / minute.
FD_MIN_INTERVAL_SECONDS = float_env("FD_MIN_INTERVAL_SECONDS", 6.5)

DEFAULT_LEAGUE_IDS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "BL1": "Bundesliga",
    "SA": "Serie A",
    "FL1": "Ligue 1",
    "CL": "UEFA Champions League",
}

KNOWN_LEAGUE_NAMES = {
    **DEFAULT_LEAGUE_IDS,
    "DED": "Eredivisie",
    "PPL": "Primeira Liga",
    "ELC": "Championship (England)",
    "BSA": "Campeonato Brasileiro Série A",
    "WC": "FIFA World Cup",
    "EC": "European Championship",
}

ODDS_SPORT_KEYS = {
    "PL": "soccer_epl",
    "PD": "soccer_spain_la_liga",
    "BL1": "soccer_germany_bundesliga",
    "SA": "soccer_italy_serie_a",
    "FL1": "soccer_france_ligue_one",
    "CL": "soccer_uefa_champs_league",
}

# Per-market gates. Tuned conservative on purpose.
# min_p  = model probability floor ("sure-ish")
# min_ev = model_p * decimal_odds - 1
MARKET_RULES: dict[str, dict] = {
    "home_win": {"min_p": 0.58, "min_odds": 1.28, "max_odds": 2.15, "min_ev": 0.04},
    "away_win": {"min_p": 0.55, "min_odds": 1.30, "max_odds": 2.15, "min_ev": 0.05},
    "over25": {"min_p": 0.58, "min_odds": 1.30, "max_odds": 2.05, "min_ev": 0.04},
    "btts_yes": {"min_p": 0.58, "min_odds": 1.30, "max_odds": 2.05, "min_ev": 0.04},
    "double_chance_home": {"min_p": 0.78, "min_odds": 1.22, "max_odds": 1.55, "min_ev": 0.03},
    "double_chance_away": {"min_p": 0.75, "min_odds": 1.22, "max_odds": 1.55, "min_ev": 0.03},
}


def load_league_ids() -> dict[str, str]:
    override = env("LEAGUE_IDS", "")
    if not override:
        return dict(DEFAULT_LEAGUE_IDS)
    codes = [part.strip().upper() for part in override.split(",") if part.strip()]
    if not codes:
        logger.warning("LEAGUE_IDS set but empty — defaulting to top-5 + UCL.")
        return dict(DEFAULT_LEAGUE_IDS)
    result = {c: KNOWN_LEAGUE_NAMES.get(c, f"Competition {c}") for c in codes}
    logger.info("LEAGUE_IDS override: %s", list(result.values()))
    return result
