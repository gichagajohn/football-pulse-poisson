"""
DAILY RUN — Football Pulse AI
1. Grade past tickets (same football-data.org IDs)
2. Run Poisson pipeline for today
3. Email the ticket (fail the job if SMTP dies after a publish)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from backend import result_checker
from backend.email_sender import send_email
from backend.pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("daily_run")


async def main() -> None:
    today = date.today()

    logger.info("Checking outcomes of past tickets...")
    try:
        result_checker.run(today=today)
    except Exception as exc:
        logger.error("Result checking failed (non-fatal): %s", exc)

    logger.info("Running Poisson pipeline...")
    ticket = await run_pipeline(target_date=today)

    subject = f"⚽ Football Pulse AI — Daily Ticket ({today.isoformat()})"
    sent = send_email(subject, ticket)
    if sent is False:
        logger.error("Email failed.")
        if "NO BET TODAY" not in ticket:
            sys.exit(1)

    logger.info("Daily run complete.")


if __name__ == "__main__":
    asyncio.run(main())
