"""Weekly performance from graded SINGLES (1 unit each)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from backend.db import supabase_client

logger = logging.getLogger(__name__)


def generate_report(days: int = 7) -> str:
    cutoff = date.today() - timedelta(days=days)
    rows = supabase_client.get_selections_since(cutoff)
    relevant = [r for r in rows if r.get("outcome") in ("win", "loss", "void")]

    if not relevant:
        return (
            "📊 FOOTBALL PULSE AI — WEEKLY REPORT\n"
            f"No graded singles in the last {days} days yet.\n"
            "(Either no PUBLISH decisions, or results have not been confirmed.)"
        )

    wins = sum(1 for r in relevant if r["outcome"] == "win")
    losses = sum(1 for r in relevant if r["outcome"] == "loss")
    voids = sum(1 for r in relevant if r["outcome"] == "void")
    graded = wins + losses
    hit_rate = (wins / graded * 100) if graded else 0.0

    staked = 0.0
    returns = 0.0
    for r in relevant:
        if r["outcome"] == "void":
            continue
        staked += 1.0
        if r["outcome"] == "win":
            returns += 1.0 * float(r.get("odds") or 0)

    profit = returns - staked
    roi = (profit / staked * 100) if staked else 0.0
    avg_odds = (
        sum(float(r.get("odds") or 0) for r in relevant if r["outcome"] != "void") / graded
        if graded
        else 0.0
    )

    by_market: dict[str, list[str]] = defaultdict(list)
    for r in relevant:
        by_market[r.get("market") or "?"].append(r["outcome"])

    lines = [
        "📊 FOOTBALL PULSE AI — WEEKLY PERFORMANCE REPORT",
        f"Period: last {days} days ({cutoff.isoformat()} to {date.today().isoformat()})",
        "Model: Dixon-Coles Poisson  |  Stake: 1 unit per single",
        "",
        f"Singles graded: {graded}  (Wins: {wins}, Losses: {losses}, Void: {voids})",
        f"Hit Rate: {hit_rate:.1f}%",
        f"Average odds: {avg_odds:.2f}",
        f"ROI: {roi:+.1f}%",
        f"Net: {profit:+.2f} units",
        "",
        "By market:",
    ]
    for market, outs in sorted(by_market.items()):
        g = [o for o in outs if o in ("win", "loss")]
        w = sum(1 for o in g if o == "win")
        rate = (w / len(g) * 100) if g else 0.0
        lines.append(f"  {market}: {w}/{len(g)} ({rate:.0f}%)")

    lines.append("")
    lines.append("Daily singles:")
    for r in sorted(relevant, key=lambda x: x["ticket_date"], reverse=True):
        icon = {"win": "✅", "loss": "❌", "void": "➖"}.get(r["outcome"], "?")
        lines.append(
            f"  {icon} {r['ticket_date']} {r.get('home_team')} vs {r.get('away_team')} "
            f"— {r.get('market')} @ {r.get('odds')}"
        )
    lines.append("")
    lines.append("⚠️ Past performance does not guarantee future results.")
    lines.append("Discipline over volume. Always.")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(generate_report())
