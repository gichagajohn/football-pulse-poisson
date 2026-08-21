"""SMTP email. Returns True on success, False on skip/failure."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.config import env, int_env

logger = logging.getLogger(__name__)


def send_email(subject: str, body: str) -> bool:
    smtp_host = env("SMTP_HOST")
    smtp_port = int_env("SMTP_PORT", 587)
    smtp_user = env("SMTP_USERNAME")
    smtp_pass = env("SMTP_PASSWORD")
    email_to = env("EMAIL_TO")

    if not all([smtp_host, smtp_user, smtp_pass, email_to]):
        logger.warning("[EMAIL] SMTP env vars not fully set — skipping email send.")
        return False

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, email_to, msg.as_string())
        logger.info("[EMAIL] Sent: %r to %s", subject, email_to)
        return True
    except Exception as exc:
        logger.error("[EMAIL] Failed to send email: %s", exc)
        return False
