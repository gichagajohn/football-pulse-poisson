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


def bool_env(name: str, default: bool = False) -> bool:
    raw = env(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


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

# ── Odds API spend control ───────────────────────────────────────────────────
# The free plan is 500 credits / month and one credit = one market × one region.
# h2h + totals for 6 leagues = 12 credits/day = ~360/month, which fits.
# BTTS is billed separately (3 more credits/league/day → ~540/month, which
# does NOT fit) so it is opt-in and off by default.
ODDS_FETCH_BTTS = bool_env("ODDS_FETCH_BTTS", False)
ODDS_REGIONS = env("ODDS_REGIONS", "eu")
# Stop spending when the account runs this low; the run publishes NO BET with
# a quota reason instead of silently pretending there were no fixtures.
ODDS_MIN_CREDITS_REMAINING = int_env("ODDS_MIN_CREDITS_REMAINING", 25)

# ── Fixture matching (Odds API ⇄ football-data.org) ─────────────────────────
# Kickoff must agree this closely before the two records are considered the
# same match. Matches the wrong ID is worse than skipping the fixture.
FIXTURE_MATCH_TOLERANCE_MINUTES = float_env("FIXTURE_MATCH_TOLERANCE_MINUTES", 30.0)

# ── Model vs market ────────────────────────────────────────────────────────
# Probability used for the gates is
#   p_adj = MODEL_WEIGHT * p_model + (1 - MODEL_WEIGHT) * p_market_devigged
#
# Default is 1.0 = the raw model, and that is a MEASURED choice, not an
# oversight. Walk-forward backtest over 4 seasons x 5 leagues (~28k matches,
# scripts/backtest.py):
#
#   MODEL_WEIGHT   bets   ROI       avg CLV
#   1.00           2472   -5.53%    -0.40%
#   0.80           2199   -6.43%    -0.44%
#   0.60           1736   -8.05%    -0.44%
#   0.40            993  -12.78%    -0.63%
#
# Shrinking toward the market cut the number of bets but made each one worse:
# the picks that survive shrinkage are the highest-disagreement fixtures, which
# is exactly the adverse selection the blend was meant to remove. So the
# mechanism stays available as a knob, and the honest default is 1.0.
MODEL_WEIGHT = min(max(float_env("MODEL_WEIGHT", 1.0), 0.0), 1.0)
# Two-way markets (O2.5, BTTS) only give us one side, so they cannot be
# de-vigged properly; this is the assumed overround for the missing side.
TWO_WAY_OVERROUND = float_env("TWO_WAY_OVERROUND", 1.04)

# A leg that is never confirmed (postponed / abandoned / abandoned-and-not-
# replayed) must not pin the whole ticket at "pending" forever.
TICKET_MAX_PENDING_DAYS = int_env("TICKET_MAX_PENDING_DAYS", 3)

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
#
#   min_p  = floor on the ADJUSTED probability (model shrunk toward the
#            de-vigged market price — see backend/model/select.py)
#   min_ev = adj_p * decimal_odds - 1
#
# Double chance is deliberately absent: The Odds API does not publish a DC
# market, so the only price available was reverse-engineered from vigged 1X2
# and any "EV" against it was measured against a number no book offers.
MARKET_RULES: dict[str, dict] = {
    "home_win": {"min_p": 0.58, "min_odds": 1.28, "max_odds": 2.15, "min_ev": 0.04},
    "away_win": {"min_p": 0.55, "min_odds": 1.30, "max_odds": 2.15, "min_ev": 0.05},
    "over25": {"min_p": 0.58, "min_odds": 1.30, "max_odds": 2.05, "min_ev": 0.04},
    "btts_yes": {"min_p": 0.58, "min_odds": 1.30, "max_odds": 2.05, "min_ev": 0.04},
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
