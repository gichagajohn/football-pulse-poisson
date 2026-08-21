"""
Attack / defence ratings from finished matches.

Per-competition multiplicative model:

    λ_home = attack_home * defence_away * league_avg_home_goals
    λ_away = attack_away * defence_home * league_avg_away_goals

defence is a *conceding* multiplier (1.0 = average, >1 leakier).
Recent matches weigh more (exponential half-life). Previous season is
down-weighted so early August is not pure noise and not pure last year.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import math

from backend.config import (
    HALF_LIFE_DAYS,
    MIN_WEIGHTED_GAMES,
    PREVIOUS_SEASON_FACTOR,
)
from backend.model.poisson import market_probabilities


@dataclass(frozen=True)
class MatchResult:
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int
    played_on: date
    competition: str
    home_name: str
    away_name: str
    season: int


@dataclass(frozen=True)
class TeamRating:
    team_id: int
    name: str
    attack: float
    defense: float
    weighted_games: float


@dataclass(frozen=True)
class LeagueModel:
    competition: str
    ratings: dict[int, TeamRating]
    avg_home_goals: float
    avg_away_goals: float
    current_season: int


def european_season_start_year(as_of: date) -> int:
    """2026-08-21 → 2026 (2026/27 season). 2026-05-01 → 2025."""
    return as_of.year if as_of.month >= 7 else as_of.year - 1


def time_weight(played_on: date, as_of: date, half_life_days: float = HALF_LIFE_DAYS) -> float:
    age = (as_of - played_on).days
    if age < 0:
        return 0.0
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (age / half_life_days)


def _geo_mean(values: list[float]) -> float:
    positives = [v for v in values if v > 0]
    if not positives:
        return 1.0
    return math.exp(sum(math.log(v) for v in positives) / len(positives))


def build_league_model(
    matches: list[MatchResult],
    competition: str,
    as_of: date,
    iterations: int = 8,
) -> LeagueModel | None:
    current_season = european_season_start_year(as_of)
    league = [
        m
        for m in matches
        if m.competition == competition
        and m.played_on < as_of
        and m.home_goals is not None
        and m.away_goals is not None
    ]
    if len(league) < 20:
        return None

    weighted: list[tuple[MatchResult, float]] = []
    for m in league:
        w = time_weight(m.played_on, as_of)
        if m.season < current_season:
            w *= PREVIOUS_SEASON_FACTOR
        if w <= 0:
            continue
        weighted.append((m, w))
    if not weighted:
        return None

    w_sum = sum(w for _, w in weighted)
    avg_home = sum(m.home_goals * w for m, w in weighted) / w_sum
    avg_away = sum(m.away_goals * w for m, w in weighted) / w_sum
    avg_home = max(avg_home, 0.6)
    avg_away = max(avg_away, 0.4)

    names: dict[int, str] = {}
    team_ids: set[int] = set()
    for m, _ in weighted:
        team_ids.add(m.home_id)
        team_ids.add(m.away_id)
        names[m.home_id] = m.home_name
        names[m.away_id] = m.away_name

    attack = {t: 1.0 for t in team_ids}
    defense = {t: 1.0 for t in team_ids}

    for _ in range(iterations):
        att_num: dict[int, float] = defaultdict(float)
        att_den: dict[int, float] = defaultdict(float)
        def_num: dict[int, float] = defaultdict(float)
        def_den: dict[int, float] = defaultdict(float)
        games: dict[int, float] = defaultdict(float)

        for m, w in weighted:
            att_num[m.home_id] += w * m.home_goals
            att_den[m.home_id] += w * defense[m.away_id] * avg_home
            att_num[m.away_id] += w * m.away_goals
            att_den[m.away_id] += w * defense[m.home_id] * avg_away

            def_num[m.home_id] += w * m.away_goals
            def_den[m.home_id] += w * attack[m.away_id] * avg_away
            def_num[m.away_id] += w * m.home_goals
            def_den[m.away_id] += w * attack[m.home_id] * avg_home

            games[m.home_id] += w
            games[m.away_id] += w

        for t in team_ids:
            if att_den[t] > 0:
                attack[t] = att_num[t] / att_den[t]
            if def_den[t] > 0:
                defense[t] = def_num[t] / def_den[t]
            attack[t] = min(max(attack[t], 0.35), 2.6)
            defense[t] = min(max(defense[t], 0.35), 2.6)

        att_g = _geo_mean(list(attack.values()))
        def_g = _geo_mean(list(defense.values()))
        if att_g > 0:
            attack = {t: v / att_g for t, v in attack.items()}
        if def_g > 0:
            defense = {t: v / def_g for t, v in defense.items()}

    games_final: dict[int, float] = defaultdict(float)
    for m, w in weighted:
        games_final[m.home_id] += w
        games_final[m.away_id] += w
    ratings = {
        t: TeamRating(
            team_id=t,
            name=names.get(t, str(t)),
            attack=round(attack[t], 4),
            defense=round(defense[t], 4),
            weighted_games=round(games_final[t], 2),
        )
        for t in team_ids
    }

    return LeagueModel(
        competition=competition,
        ratings=ratings,
        avg_home_goals=round(avg_home, 4),
        avg_away_goals=round(avg_away, 4),
        current_season=current_season,
    )


def build_league_models(matches: list[MatchResult], as_of: date) -> dict[str, LeagueModel]:
    comps = {m.competition for m in matches}
    models: dict[str, LeagueModel] = {}
    for code in comps:
        model = build_league_model(matches, code, as_of)
        if model:
            models[code] = model
    return models


def predict_match(model: LeagueModel, home_id: int, away_id: int) -> dict | None:
    home = model.ratings.get(home_id)
    away = model.ratings.get(away_id)
    if not home or not away:
        return None
    if home.weighted_games < MIN_WEIGHTED_GAMES or away.weighted_games < MIN_WEIGHTED_GAMES:
        return None

    lam_home = home.attack * away.defense * model.avg_home_goals
    lam_away = away.attack * home.defense * model.avg_away_goals
    lam_home = min(max(lam_home, 0.35), 3.8)
    lam_away = min(max(lam_away, 0.25), 3.5)

    probs = market_probabilities(lam_home, lam_away)
    return {
        "lambda_home": round(lam_home, 3),
        "lambda_away": round(lam_away, 3),
        "home_attack": home.attack,
        "home_defense": home.defense,
        "away_attack": away.attack,
        "away_defense": away.defense,
        "home_games": home.weighted_games,
        "away_games": away.weighted_games,
        "probs": probs,
    }
