"""
Sure-mode selector.

Independent singles only. A market is published only when ALL of these hold:

  1. We have a real book price (not a hallucinated number)
  2. Model probability ≥ min_p for that market
  3. Decimal odds inside a conservative band (no 1.05 traps, no longshots)
  4. EV = model_p * odds - 1 ≥ min_ev
  5. At most one market per match
  6. Weather wind below the hard reject
  7. Both clubs have enough weighted history

If nothing survives, the ticket is NO BET. That is a feature.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.config import MARKET_RULES, MAX_SELECTIONS, WIND_REJECT_KMH

logger = logging.getLogger(__name__)

MARKET_LABELS = {
    "home_win": "Home win",
    "away_win": "Away win",
    "over25": "Over 2.5 goals",
    "btts_yes": "Both teams to score",
    "double_chance_home": "Double chance 1X",
    "double_chance_away": "Double chance X2",
}


def _valid_odds(value: Any) -> float | None:
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    if odds < 1.01:
        return None
    return odds


def derive_double_chance_odds(snapshot: dict, side: str) -> float | None:
    """
    Conservative DC price from vigged 1X2 (shorter than a real DC market).
    If this is still +EV, the pick is more likely to be genuine value.
    """
    home = _valid_odds(snapshot.get("home_win"))
    draw = _valid_odds(snapshot.get("draw"))
    away = _valid_odds(snapshot.get("away_win"))
    try:
        if side == "home" and home and draw:
            implied = (1 / home) + (1 / draw)
            return round(1 / implied, 3) if implied > 0 else None
        if side == "away" and away and draw:
            implied = (1 / away) + (1 / draw)
            return round(1 / implied, 3) if implied > 0 else None
    except ZeroDivisionError:
        return None
    return None


def price_for_market(market: str, snapshot: dict) -> float | None:
    if market == "home_win":
        return _valid_odds(snapshot.get("home_win"))
    if market == "away_win":
        return _valid_odds(snapshot.get("away_win"))
    if market == "over25":
        return _valid_odds(snapshot.get("over25"))
    if market == "btts_yes":
        return _valid_odds(snapshot.get("btts_yes"))
    if market == "double_chance_home":
        return derive_double_chance_odds(snapshot, "home")
    if market == "double_chance_away":
        return derive_double_chance_odds(snapshot, "away")
    return None


def _wind_reject(match: dict) -> bool:
    weather = match.get("weather") or {}
    wind = weather.get("wind_kmh")
    try:
        return wind is not None and float(wind) > WIND_REJECT_KMH
    except (TypeError, ValueError):
        return False


def _best_market(match: dict) -> dict | None:
    probs: dict = (match.get("probs") or {})
    snapshot: dict = (match.get("odds_snapshot") or {})
    if not probs or not snapshot:
        return None
    if _wind_reject(match):
        logger.info(
            "[SELECT] Reject %s vs %s — wind %.1f km/h",
            match.get("home_team"),
            match.get("away_team"),
            float((match.get("weather") or {}).get("wind_kmh")),
        )
        return None

    candidates = []
    for market, rules in MARKET_RULES.items():
        model_p = probs.get(market)
        if model_p is None:
            continue
        odds = price_for_market(market, snapshot)
        if odds is None:
            continue
        if not (rules["min_odds"] <= odds <= rules["max_odds"]):
            continue
        if model_p < rules["min_p"]:
            continue
        ev = model_p * odds - 1.0
        if ev < rules["min_ev"]:
            continue
        candidates.append(
            {
                "market": market,
                "market_label": MARKET_LABELS.get(market, market),
                "odds": round(odds, 3),
                "model_p": round(float(model_p), 4),
                "implied_p": round(1.0 / odds, 4),
                "edge": round(float(model_p) - (1.0 / odds), 4),
                "ev": round(ev, 4),
            }
        )

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["ev"], c["model_p"]), reverse=True)
    return candidates[0]


def _rationale(match: dict, pick: dict) -> str:
    return (
        f"Poisson λ {match.get('lambda_home')}–{match.get('lambda_away')}; "
        f"model {pick['model_p']*100:.1f}% vs book {pick['implied_p']*100:.1f}% "
        f"(edge {pick['edge']*100:+.1f}%, EV {pick['ev']*100:+.1f}%). "
        f"Ratings from {match.get('home_games')} / {match.get('away_games')} weighted games."
    )


def select_sure_picks(matches: list[dict], max_selections: int | None = None) -> dict:
    cap = max_selections if max_selections is not None else MAX_SELECTIONS
    ranked: list[dict] = []
    skipped_no_model = 0
    skipped_gates = 0

    for match in matches:
        if not match.get("probs"):
            skipped_no_model += 1
            continue
        best = _best_market(match)
        if not best:
            skipped_gates += 1
            continue
        ranked.append(
            {
                "fixture_id": match.get("fixture_id"),
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
                "league": match.get("league"),
                "competition_code": match.get("competition_code"),
                "kickoff_utc": match.get("kickoff_utc"),
                "lambda_home": match.get("lambda_home"),
                "lambda_away": match.get("lambda_away"),
                "home_games": match.get("home_games"),
                "away_games": match.get("away_games"),
                **best,
                "rationale": _rationale(match, best),
            }
        )

    ranked.sort(key=lambda r: (r["ev"], r["model_p"]), reverse=True)
    # One selection per fixture (already true) and cap.
    seen: set[int] = set()
    final = []
    for row in ranked:
        fid = row.get("fixture_id")
        if fid in seen:
            continue
        seen.add(fid)
        final.append(row)
        if len(final) >= cap:
            break

    logger.info(
        "[SELECT] %s priced, %s no-model, %s failed sure-gates, %s published.",
        len(matches),
        skipped_no_model,
        skipped_gates,
        len(final),
    )

    if not final:
        return {
            "decision": "NO_BET",
            "reason": (
                "No selection cleared the sure-mode gates "
                f"(model_p + EV + odds band, max {cap} independent singles). "
                f"{skipped_no_model} match(es) lacked ratings; "
                f"{skipped_gates} had a model but failed the filters. "
                "Discipline over volume."
            ),
            "selections": [],
            "risk_level": "None",
        }

    avg_p = sum(s["model_p"] for s in final) / len(final)
    risk = "Low" if avg_p >= 0.70 else "Medium"
    return {
        "decision": "PUBLISH",
        "reason": f"{len(final)} independent single(s) passed sure-mode filters.",
        "selections": final,
        "risk_level": risk,
        "ticket_type": "singles",
    }
