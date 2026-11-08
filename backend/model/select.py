"""
Sure-mode selector.

Independent singles only. A market is published only when ALL of these hold:

  1. We have a real book price (not a hallucinated number)
  2. The ADJUSTED probability clears min_p for that market
  3. Decimal odds inside a conservative band (no 1.05 traps, no longshots)
  4. EV = adj_p * odds - 1 ≥ min_ev
  5. At most one market per match
  6. Weather wind below the hard reject
  7. Both clubs have enough weighted history

Why "adjusted" probabilities
----------------------------
The naive gate was `p_model * odds - 1 ≥ min_ev`. That is not a filter, it is
a *selector for disagreement*: the only fixtures that pass are the ones where
a goals-only Poisson sits 4+ points away from a market that has team news,
line movement and 40 books in it. Those are precisely the fixtures where the
model is most likely to be the wrong one, so the gate systematically bets on
model error (adverse selection).

So each market's probability is first shrunk toward the book:

    p_adj = MODEL_WEIGHT * p_model + (1 - MODEL_WEIGHT) * p_market

with every gate applied to p_adj, and both numbers printed on the ticket so
the shrinkage is auditable. MODEL_WEIGHT=1 restores the old behaviour; lower
values mean "only bet when the model survives being pulled halfway to the
market". p_model is still shown, because the disagreement is the whole point
of running the model — it just no longer *is* the bet.

If nothing survives, the ticket is NO BET. That is a feature.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.config import (
    MARKET_RULES,
    MAX_SELECTIONS,
    MODEL_WEIGHT,
    TWO_WAY_OVERROUND,
    WIND_REJECT_KMH,
)

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
    Conservative DC price from vigged 1X2.

    NOTE: double chance is no longer selectable (see config.MARKET_RULES) —
    The Odds API publishes no DC market, so any "EV" against this synthetic
    number was measured against a price you cannot actually get. Kept only so
    stored historical tickets remain readable.
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


def devigged_market_probs(snapshot: dict) -> dict[str, float]:
    """Remove the bookmaker margin from the 1X2 prices (proportional method).

    Without this, `p_market` would still contain the overround and the
    shrinkage would lean toward a number the book does not believe either.
    """
    home = _valid_odds(snapshot.get("home_win"))
    draw = _valid_odds(snapshot.get("draw"))
    away = _valid_odds(snapshot.get("away_win"))
    if not (home and draw and away):
        return {}
    implied = (1 / home, 1 / draw, 1 / away)
    total = sum(implied)
    if total <= 0:
        return {}
    return {
        "home_win": implied[0] / total,
        "draw": implied[1] / total,
        "away_win": implied[2] / total,
    }


def market_probability(market: str, snapshot: dict) -> float | None:
    """Best available estimate of what the book thinks, margin removed."""
    three_way = devigged_market_probs(snapshot)
    if market in three_way:
        return three_way[market]
    odds = price_for_market(market, snapshot)
    if odds is None:
        return None
    # Only one side of a two-way market is published, so the margin cannot be
    # measured — assume a typical one and stay conservative.
    if market in {"over25", "btts_yes"}:
        return (1.0 / odds) / max(TWO_WAY_OVERROUND, 1.0)
    return 1.0 / odds


def adjusted_probability(model_p: float, market_p: float | None, weight: float | None = None) -> float:
    """Shrink the model toward the market. No market price → no adjustment."""
    w = MODEL_WEIGHT if weight is None else weight
    if market_p is None:
        return float(model_p)
    return w * float(model_p) + (1.0 - w) * float(market_p)


def _wind_reject(match: dict) -> bool:
    weather = match.get("weather") or {}
    wind = weather.get("wind_kmh")
    try:
        return wind is not None and float(wind) > WIND_REJECT_KMH
    except (TypeError, ValueError):
        return False


def _best_market(match: dict, model_weight: float | None = None) -> dict | None:
    probs: dict = (match.get("probs") or {})
    snapshot: dict = (match.get("odds_snapshot") or {})
    if not probs or not snapshot:
        return None
    if _wind_reject(match):
        logger.info(
            "[SELECT] Reject %s vs %s — wind %s km/h",
            match.get("home_team"),
            match.get("away_team"),
            (match.get("weather") or {}).get("wind_kmh"),
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
        book_p = market_probability(market, snapshot)
        adj_p = adjusted_probability(model_p, book_p, model_weight)
        if adj_p < rules["min_p"]:
            continue
        ev = adj_p * odds - 1.0
        if ev < rules["min_ev"]:
            continue
        candidates.append(
            {
                "market": market,
                "market_label": MARKET_LABELS.get(market, market),
                "odds": round(odds, 3),
                "model_p": round(float(model_p), 4),
                "market_p": round(float(book_p), 4) if book_p is not None else None,
                "adj_p": round(float(adj_p), 4),
                "implied_p": round(1.0 / odds, 4),
                "edge": round(float(adj_p) - (1.0 / odds), 4),
                "ev": round(ev, 4),
                "ev_raw": round(float(model_p) * odds - 1.0, 4),
            }
        )

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["ev"], c["adj_p"]), reverse=True)
    return candidates[0]


def _rationale(match: dict, pick: dict) -> str:
    market_txt = (
        f"{pick['market_p']*100:.1f}%" if pick.get("market_p") is not None else "n/a"
    )
    return (
        f"Poisson λ {match.get('lambda_home')}–{match.get('lambda_away')}; "
        f"model {pick['model_p']*100:.1f}% / market {market_txt} / "
        f"blended {pick['adj_p']*100:.1f}% vs book {pick['implied_p']*100:.1f}% "
        f"(edge {pick['edge']*100:+.1f}%, EV {pick['ev']*100:+.1f}%). "
        f"Ratings from {match.get('home_games')} / {match.get('away_games')} weighted games."
    )


def select_sure_picks(
    matches: list[dict],
    max_selections: int | None = None,
    model_weight: float | None = None,
) -> dict:
    cap = max_selections if max_selections is not None else MAX_SELECTIONS
    ranked: list[dict] = []
    skipped_no_model = 0
    skipped_gates = 0

    for match in matches:
        if not match.get("probs"):
            skipped_no_model += 1
            continue
        best = _best_market(match, model_weight=model_weight)
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

    ranked.sort(key=lambda r: (r["ev"], r["adj_p"]), reverse=True)
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
        "[SELECT] %s priced, %s no-model, %s failed sure-gates, %s published "
        "(model weight %.2f).",
        len(matches),
        skipped_no_model,
        skipped_gates,
        len(final),
        MODEL_WEIGHT if model_weight is None else model_weight,
    )

    if not final:
        return {
            "decision": "NO_BET",
            "reason": (
                "No selection cleared the sure-mode gates "
                f"(adjusted model_p + EV + odds band, max {cap} independent singles). "
                f"{skipped_no_model} match(es) lacked ratings; "
                f"{skipped_gates} had a model but failed the filters. "
                "Discipline over volume."
            ),
            "selections": [],
            "risk_level": "None",
        }

    avg_p = sum(s["adj_p"] for s in final) / len(final)
    risk = "Low" if avg_p >= 0.70 else "Medium"
    return {
        "decision": "PUBLISH",
        "reason": f"{len(final)} independent single(s) passed sure-mode filters.",
        "selections": final,
        "risk_level": risk,
        "ticket_type": "singles",
    }
