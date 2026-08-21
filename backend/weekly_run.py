"""WEEKLY RUN — re-grade late results, then email the 7-day singles report."""

from __future__ import annotations

import logging
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from backend import result_checker
from backend.email_sender import send_email
from backend.weekly_report import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("weekly_run")


def main() -> None:
    logger.info("Re-checking pending results before building the report...")
    try:
        result_checker.run(today=date.today())
    except Exception as exc:
        logger.error("Result checking failed (non-fatal): %s", exc)

    logger.info("Generating weekly performance report...")
    report = generate_report(days=7)
    logger.info("\n%s", report)

    subject = f"📊 Football Pulse AI — Weekly Report ({date.today().isoformat()})"
    send_email(subject, report)
    logger.info("Weekly run complete.")


if __name__ == "__main__":
    main()
