"""
RESULT CHECKER — grades previous tickets against football-data.org.

Uses the SAME fixture IDs Scout stored (football-data.org match ids).
"""

from __future__ import annotations

import logging
import time
from datetime import date

import httpx

from backend.config import FD_MIN_INTERVAL_SECONDS, TICKET_MAX_PENDING_DAYS, env
from backend.db import supabase_client

logger = logging.getLogger(__name__)


# Statuses that will never produce a score for this ticket date.
DEAD_STATUSES = {"POSTPONED", "CANCELLED", "SUSPENDED", "ABANDONED"}


def fetch_result(fixture_id: int) -> dict | None:
    """Return the final score, a dead-status marker, or None if still pending.

    Distinguishing "not played yet" from "will never be played" matters:
    otherwise a postponed leg keeps the whole ticket at `pending` forever and
    the ticket is re-polled on every subsequent run.
    """
    try:
        resp = httpx.get(
            f"https://api.football-data.org/v4/matches/{fixture_id}",
            headers={"X-Auth-Token": env("FOOTBALL_DATA_KEY")},
            timeout=15,
        )
        resp.raise_for_status()
        match = resp.json()
        status = (match.get("status") or "").upper()
        if status in DEAD_STATUSES:
            return {"status": status, "home_goals": None, "away_goals": None}
        if status not in {"FINISHED", "AWARDED"}:
            return None
        full = (match.get("score") or {}).get("fullTime") or {}
        home, away = full.get("home"), full.get("away")
        if home is None or away is None:
            return None
        return {"home_goals": int(home), "away_goals": int(away), "status": status}
    except Exception as exc:
        logger.warning("[RESULT_CHECK] fixture %s: %s", fixture_id, exc)
        return None


def grade_selection(market: str, home_goals: int, away_goals: int) -> str:
    total_goals = home_goals + away_goals
    if market == "home_win":
        return "win" if home_goals > away_goals else "loss"
    if market == "away_win":
        return "win" if away_goals > home_goals else "loss"
    if market == "draw":
        return "win" if home_goals == away_goals else "loss"
    if market == "double_chance_home":
        return "win" if home_goals >= away_goals else "loss"
    if market == "double_chance_away":
        return "win" if away_goals >= home_goals else "loss"
    if market == "draw_no_bet_home":
        if home_goals == away_goals:
            return "void"
        return "win" if home_goals > away_goals else "loss"
    if market == "draw_no_bet_away":
        if home_goals == away_goals:
            return "void"
        return "win" if away_goals > home_goals else "loss"
    if market == "btts_yes":
        return "win" if (home_goals > 0 and away_goals > 0) else "loss"
    if market == "over25":
        return "win" if total_goals > 2.5 else "loss"
    logger.warning("[RESULT_CHECK] Unknown market %r — void.", market)
    return "void"


def ticket_outcome(outcomes: list[str]) -> str:
    """Aggregate leg outcomes for a singles ticket.

    Singles are staked independently, so a void leg next to three winners is a
    winning day — the void simply was not staked. (The old code called that
    "mixed", which also meant the row never reached the weekly ROI either way.)
    """
    settled = [o for o in outcomes if o in {"win", "loss"}]
    if not settled:
        return "void"
    if all(o == "win" for o in settled):
        return "win"
    if all(o == "loss" for o in settled):
        return "loss"
    return "mixed"


def run(today: date | None = None) -> None:
    today = today or date.today()
    logger.info("[RESULT_CHECK] Pending tickets before %s...", today)
    pending_tickets = supabase_client.get_pending_tickets(before_date=today)
    if not pending_tickets:
        logger.info("[RESULT_CHECK] No pending tickets.")
        return

    for ticket in pending_tickets:
        ticket_date = date.fromisoformat(ticket["ticket_date"])
        selections = supabase_client.get_selections_for_date(ticket_date)
        if not selections:
            continue

        all_graded = True
        outcomes: list[str] = []

        expired = (today - ticket_date).days >= TICKET_MAX_PENDING_DAYS

        for sel in selections:
            existing = sel.get("outcome")
            if existing and existing != "pending":
                outcomes.append(existing)
                continue

            result = fetch_result(sel["fixture_id"])
            time.sleep(FD_MIN_INTERVAL_SECONDS)
            if result is None:
                if expired:
                    # Still no score after TICKET_MAX_PENDING_DAYS. A book
                    # would have voided and settled; holding the ticket open
                    # blocks nothing but costs a football-data call every run.
                    logger.warning(
                        "[RESULT_CHECK] %s vs %s never resolved (%s days) — void.",
                        sel.get("home_team"),
                        sel.get("away_team"),
                        (today - ticket_date).days,
                    )
                    supabase_client.update_selection_outcome(sel["id"], "void")
                    outcomes.append("void")
                    continue
                all_graded = False
                continue

            if result.get("home_goals") is None:
                logger.info(
                    "[RESULT_CHECK] %s vs %s is %s — void (a book voids this).",
                    sel.get("home_team"),
                    sel.get("away_team"),
                    result.get("status"),
                )
                supabase_client.update_selection_outcome(sel["id"], "void")
                outcomes.append("void")
                continue

            outcome = grade_selection(sel["market"], result["home_goals"], result["away_goals"])
            supabase_client.update_selection_outcome(
                sel["id"],
                outcome,
                home_score=result["home_goals"],
                away_score=result["away_goals"],
            )
            logger.info(
                "[RESULT_CHECK] %s vs %s (%s) -> %s (%s-%s)",
                sel.get("home_team"),
                sel.get("away_team"),
                sel.get("market"),
                outcome,
                result["home_goals"],
                result["away_goals"],
            )
            outcomes.append(outcome)

        if all_graded and outcomes:
            supabase_client.update_ticket_outcome(ticket_date, ticket_outcome(outcomes))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run()
