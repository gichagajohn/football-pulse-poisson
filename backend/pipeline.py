"""
FOOTBALL PULSE AI — Statistical pipeline (GitHub Actions edition)

Scout (APIs) → ratings (finished scores) → Poisson probabilities
→ sure-mode filter → f-string ticket → Supabase.

Zero LLM calls. Zero Groq. Inference cost: $0.
"""

from __future__ import annotations

import logging
from datetime import date

from backend.agents.scout_agent import run as run_scout
from backend.db import supabase_client
from backend.model.history import fetch_history
from backend.model.ratings import build_league_models, predict_match
from backend.model.select import select_sure_picks
from backend.publisher import format_ticket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("football_pulse")


def _no_bet(date_str: str, reason: str) -> tuple[str, dict, dict]:
    decision = {"decision": "NO_BET", "reason": reason, "final_confidence": 0.0}
    portfolio: dict = {"decision": "NO_BET", "reason": reason, "selections": []}
    return format_ticket(date_str, portfolio, decision), portfolio, decision


async def run_pipeline(target_date: date | None = None) -> str:
    target_date = target_date or date.today()
    date_str = target_date.strftime("%A, %d %B %Y")
    logger.info("=" * 60)
    logger.info("  FOOTBALL PULSE AI — Poisson pipeline")
    logger.info("  Target Date: %s", date_str)
    logger.info("=" * 60)

    logger.info("[1/5] SCOUT")
    intelligence = await run_scout(target_date)
    logger.info("[1/5] %s fixture(s).", len(intelligence))
    if not intelligence:
        ticket, portfolio, decision = _no_bet(
            date_str, f"No upcoming fixtures in configured leagues for {date_str}."
        )
        supabase_client.store_ticket(target_date, ticket, portfolio, decision)
        return ticket

    codes = {m.get("competition_code") for m in intelligence if m.get("competition_code")}
    logger.info("[2/5] HISTORY for %s", sorted(codes))
    history = await fetch_history(codes, target_date)
    logger.info("[2/5] %s finished matches loaded.", len(history))

    logger.info("[3/5] RATINGS")
    models = build_league_models(history, target_date)
    logger.info("[3/5] League models: %s", list(models.keys()))

    logger.info("[4/5] POISSON")
    priced: list[dict] = []
    for match in intelligence:
        code = match.get("competition_code")
        model = models.get(code)
        if not model:
            logger.info("[4/5] No ratings for %s — skip %s vs %s", code, match.get("home_team"), match.get("away_team"))
            priced.append(match)
            continue
        home_id = match.get("home_team_id")
        away_id = match.get("away_team_id")
        pred = predict_match(model, home_id, away_id) if home_id and away_id else None
        if not pred:
            logger.info(
                "[4/5] Insufficient history: %s vs %s",
                match.get("home_team"),
                match.get("away_team"),
            )
            priced.append(match)
            continue
        merged = {**match, **pred}
        top = pred["probs"]
        logger.info(
            "[4/5] %s vs %s  λ=%.2f-%.2f  P(1X2)=%.0f/%.0f/%.0f  O2.5=%.0f",
            match.get("home_team"),
            match.get("away_team"),
            pred["lambda_home"],
            pred["lambda_away"],
            top["home_win"] * 100,
            top["draw"] * 100,
            top["away_win"] * 100,
            top["over25"] * 100,
        )
        priced.append(merged)

    logger.info("[5/5] SURE-MODE SELECT")
    portfolio = select_sure_picks(priced)
    if portfolio.get("decision") == "PUBLISH":
        decision = {
            "decision": "PUBLISH",
            "reason": portfolio.get("reason"),
            "final_confidence": round(
                sum(s["model_p"] for s in portfolio["selections"]) / len(portfolio["selections"]),
                4,
            ),
        }
    else:
        decision = {
            "decision": "NO_BET",
            "reason": portfolio.get("reason"),
            "final_confidence": 0.0,
        }

    ticket = format_ticket(date_str, portfolio, decision)
    logger.info("\n%s", ticket)
    supabase_client.store_ticket(target_date, ticket, portfolio, decision)
    return ticket


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(run_pipeline()))
