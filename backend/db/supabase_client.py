"""
SUPABASE STORAGE — Football Pulse AI

Uses PostgREST (httpx). No SDK.

Required:
  SUPABASE_URL
  SUPABASE_KEY   (service_role — GitHub Secret only)
"""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import urlparse

import httpx

from backend.config import env

logger = logging.getLogger(__name__)

_CACHED_URL: str | None = None


def _url() -> str:
    """
    Accepts any of:
      https://xxxx.supabase.co
      https://xxxx.supabase.co/
      https://xxxx.supabase.co/rest/v1/
    Strips quotes/whitespace. Logs only the hostname (never the key).
    """
    global _CACHED_URL
    if _CACHED_URL is not None:
        return _CACHED_URL
    raw = env("SUPABASE_URL")
    raw = raw.strip().strip('"').strip("'").replace("\n", "").replace("\r", "")
    if not raw:
        _CACHED_URL = ""
        return ""
    if not raw.lower().startswith("http"):
        raw = "https://" + raw.lstrip("/")
    raw = re.sub(r"/rest/v1/?.*$", "", raw, flags=re.IGNORECASE)
    raw = raw.rstrip("/")
    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    if not host.endswith(".supabase.co"):
        logger.error(
            "[SUPABASE] SUPABASE_URL host is not *.supabase.co (got %r). "
            "Use https://YOURPROJECT.supabase.co with no /rest/v1/.",
            host or raw[:60],
        )
        _CACHED_URL = ""
        return ""
    _CACHED_URL = f"{parsed.scheme}://{host}"
    logger.info("[SUPABASE] Host=%s", host)
    return _CACHED_URL


def _key() -> str:
    return env("SUPABASE_KEY")


def _headers(prefer: str | None = None) -> dict:
    headers = {
        "apikey": _key(),
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _enabled() -> bool:
    if not _url() or not _key():
        logger.warning("[SUPABASE] SUPABASE_URL/SUPABASE_KEY not set — skipping storage.")
        return False
    return True


def store_ticket(
    target_date: date,
    ticket_text: str,
    portfolio: dict,
    decision: dict,
) -> None:
    if not _enabled():
        return

    status = "published" if decision.get("decision") == "PUBLISH" else "no_bet"
    selections = portfolio.get("selections") or []

    ticket_row = {
        "ticket_date": target_date.isoformat(),
        "status": status,
        "combined_odds": None,
        "selection_count": len(selections),
        "final_confidence": decision.get("final_confidence"),
        "risk_level": portfolio.get("risk_level"),
        "reason": decision.get("reason"),
        "ticket_text": ticket_text,
        "outcome": "pending" if status == "published" else "void",
    }

    try:
        resp = httpx.post(
            f"{_url()}/rest/v1/prediction_tickets",
            headers=_headers("resolution=merge-duplicates"),
            params={"on_conflict": "ticket_date"},
            json=ticket_row,
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("[SUPABASE] Stored ticket for %s (status=%s).", target_date, status)
    except Exception as exc:
        logger.error("[SUPABASE] Failed to store ticket: %s", exc)
        return

    # Re-runs must not duplicate legs.
    try:
        httpx.delete(
            f"{_url()}/rest/v1/ticket_selections",
            headers=_headers(),
            params={"ticket_date": f"eq.{target_date.isoformat()}"},
            timeout=15,
        ).raise_for_status()
    except Exception as exc:
        logger.error("[SUPABASE] Failed to clear old selections: %s", exc)

    if status != "published" or not selections:
        return

    selection_rows = [
        {
            "ticket_date": target_date.isoformat(),
            "fixture_id": sel.get("fixture_id"),
            "home_team": sel.get("home_team"),
            "away_team": sel.get("away_team"),
            "league": sel.get("league"),
            "market": sel.get("market"),
            "odds": sel.get("odds"),
            "rationale": sel.get("rationale"),
            "outcome": "pending",
        }
        for sel in selections
    ]

    try:
        resp = httpx.post(
            f"{_url()}/rest/v1/ticket_selections",
            headers=_headers(),
            json=selection_rows,
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("[SUPABASE] Stored %s selection(s).", len(selection_rows))
    except Exception as exc:
        logger.error("[SUPABASE] Failed to store selections: %s", exc)


def get_pending_tickets(before_date: date) -> list[dict]:
    if not _enabled():
        return []
    try:
        resp = httpx.get(
            f"{_url()}/rest/v1/prediction_tickets",
            headers=_headers(),
            params={
                "status": "eq.published",
                "outcome": "eq.pending",
                "ticket_date": f"lt.{before_date.isoformat()}",
                "select": "*",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("[SUPABASE] Failed to fetch pending tickets: %s", exc)
        return []


def get_selections_for_date(ticket_date: date) -> list[dict]:
    if not _enabled():
        return []
    try:
        resp = httpx.get(
            f"{_url()}/rest/v1/ticket_selections",
            headers=_headers(),
            params={"ticket_date": f"eq.{ticket_date.isoformat()}", "select": "*"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("[SUPABASE] Failed to fetch selections: %s", exc)
        return []


def get_selections_since(cutoff: date) -> list[dict]:
    if not _enabled():
        return []
    try:
        resp = httpx.get(
            f"{_url()}/rest/v1/ticket_selections",
            headers=_headers(),
            params={
                "ticket_date": f"gte.{cutoff.isoformat()}",
                "select": "*",
                "order": "ticket_date.desc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("[SUPABASE] Failed to fetch selections since %s: %s", cutoff, exc)
        return []


def update_selection_outcome(
    selection_id: int,
    outcome: str,
    home_score: int | None = None,
    away_score: int | None = None,
) -> None:
    if not _enabled():
        return
    payload: dict = {"outcome": outcome}
    if home_score is not None:
        payload["home_score"] = home_score
    if away_score is not None:
        payload["away_score"] = away_score
    try:
        resp = httpx.patch(
            f"{_url()}/rest/v1/ticket_selections",
            headers=_headers(),
            params={"id": f"eq.{selection_id}"},
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("[SUPABASE] Failed to update selection %s: %s", selection_id, exc)


def update_ticket_outcome(ticket_date: date, outcome: str) -> None:
    if not _enabled():
        return
    try:
        resp = httpx.patch(
            f"{_url()}/rest/v1/prediction_tickets",
            headers=_headers(),
            params={"ticket_date": f"eq.{ticket_date.isoformat()}"},
            json={"outcome": outcome},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("[SUPABASE] Ticket %s -> %s", ticket_date, outcome)
    except Exception as exc:
        logger.error("[SUPABASE] Failed to update ticket outcome: %s", exc)


def get_recent_tickets(limit: int = 30) -> list[dict]:
    if not _enabled():
        return []
    try:
        resp = httpx.get(
            f"{_url()}/rest/v1/prediction_tickets",
            headers=_headers(),
            params={"select": "*", "order": "ticket_date.desc", "limit": str(limit)},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("[SUPABASE] Failed to fetch recent tickets: %s", exc)
        return []
