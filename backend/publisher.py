"""Plain-text ticket. No LLM — odds cannot be rewritten."""

from __future__ import annotations


def format_ticket(date_str: str, portfolio: dict, decision: dict) -> str:
    if decision.get("decision") != "PUBLISH" or not portfolio.get("selections"):
        reason = decision.get("reason") or portfolio.get("reason") or "Insufficient edge."
        return (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔵 FOOTBALL PULSE AI\n"
            f"📅 {date_str}  |  🕗 08:20 EAT\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🚫 NO BET TODAY\n\n"
            f"Reason: {reason}\n\n"
            "Statistical Poisson model. Independent singles only.\n"
            "Discipline over volume. We wait.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    selections = portfolio["selections"]
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🔵 FOOTBALL PULSE AI",
        f"📅 {date_str}  |  🕗 08:20 EAT",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📐 Model: Dixon-Coles Poisson (free, no LLM)",
        "🎟️  Ticket type: independent singles (NOT an accumulator)",
        f"⚠️  Overall risk: {portfolio.get('risk_level', 'Medium')}",
        "",
    ]
    for i, sel in enumerate(selections, 1):
        lines.extend(
            [
                f"{i}. {sel.get('home_team')} vs {sel.get('away_team')} ({sel.get('league')})",
                f"   Market: {sel.get('market_label') or sel.get('market')}",
                f"   Odds: {sel.get('odds')}",
                f"   Model: {float(sel.get('model_p', 0))*100:.1f}%   Book: {float(sel.get('implied_p', 0))*100:.1f}%   "
                f"EV: {float(sel.get('ev', 0))*100:+.1f}%",
                f"   λ: {sel.get('lambda_home')} – {sel.get('lambda_away')}",
                f"   Reason: {sel.get('rationale', '')}",
                "",
            ]
        )
    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Each line is a 1-unit single. Do not combine them.",
            "",
            "⚠️  DISCLAIMER",
            "This is a probabilistic model output, not a guarantee.",
            "Bet only what you can afford to lose.",
            "Discipline over volume. Always.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
    )
    return "\n".join(lines)
