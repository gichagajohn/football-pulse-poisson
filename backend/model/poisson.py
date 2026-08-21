"""
Dixon-Coles Poisson scoreline model.

Completely free, deterministic, no API and no LLM.
Given expected home/away goals (λ_h, λ_a), this produces probabilities
for 1X2, Over 2.5, BTTS and Double Chance.

The low-score correction (ρ) slightly reduces 0-0 / 1-1 independence error
that a raw Poisson has. Default ρ = -0.10, a typical literature value.
"""

from __future__ import annotations

import math
from functools import lru_cache

from backend.config import DIXON_COLES_RHO, MAX_GOALS


def poisson_pmf(k: int, lam: float) -> float:
    if lam < 0:
        raise ValueError("lambda must be >= 0")
    if k < 0:
        return 0.0
    # e^{-λ} λ^k / k!
    return math.exp(-lam + k * math.log(lam + 1e-15) - math.lgamma(k + 1))


def dixon_coles_tau(home_goals: int, away_goals: int, lam_home: float, lam_away: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1.0 - lam_home * lam_away * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + lam_home * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + lam_away * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


@lru_cache(maxsize=4096)
def score_matrix(
    lam_home: float,
    lam_away: float,
    max_goals: int = MAX_GOALS,
    rho: float = DIXON_COLES_RHO,
) -> tuple[tuple[float, ...], ...]:
    """Return P(home=i, away=j) for i,j in 0..max_goals, renormalised."""
    lam_home = max(0.05, min(float(lam_home), 4.5))
    lam_away = max(0.05, min(float(lam_away), 4.5))
    rows: list[tuple[float, ...]] = []
    total = 0.0
    for i in range(max_goals + 1):
        row = []
        p_i = poisson_pmf(i, lam_home)
        for j in range(max_goals + 1):
            p = p_i * poisson_pmf(j, lam_away) * dixon_coles_tau(i, j, lam_home, lam_away, rho)
            p = max(p, 0.0)
            row.append(p)
            total += p
        rows.append(tuple(row))
    if total <= 0:
        n = max_goals + 1
        uniform = 1.0 / (n * n)
        return tuple(tuple(uniform for _ in range(n)) for _ in range(n))
    return tuple(tuple(p / total for p in row) for row in rows)


def market_probabilities(
    lam_home: float,
    lam_away: float,
    max_goals: int = MAX_GOALS,
    rho: float = DIXON_COLES_RHO,
) -> dict[str, float]:
    m = score_matrix(round(lam_home, 4), round(lam_away, 4), max_goals, round(rho, 4))
    n = len(m)
    p_home = p_draw = p_away = p_over25 = p_btts = 0.0
    for i in range(n):
        for j in range(n):
            p = m[i][j]
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if i + j >= 3:
                p_over25 += p
            if i >= 1 and j >= 1:
                p_btts += p
    return {
        "home_win": p_home,
        "draw": p_draw,
        "away_win": p_away,
        "over25": p_over25,
        "btts_yes": p_btts,
        "double_chance_home": p_home + p_draw,
        "double_chance_away": p_away + p_draw,
        "draw_no_bet_home": p_home / (p_home + p_away) if (p_home + p_away) > 0 else 0.0,
        "draw_no_bet_away": p_away / (p_home + p_away) if (p_home + p_away) > 0 else 0.0,
    }
