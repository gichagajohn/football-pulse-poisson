"""Free Dixon-Coles / Poisson engine. No LLM. No paid inference."""

from backend.model.poisson import market_probabilities, score_matrix
from backend.model.ratings import LeagueModel, MatchResult, build_league_models, predict_match
from backend.model.select import select_sure_picks

__all__ = [
    "market_probabilities",
    "score_matrix",
    "LeagueModel",
    "MatchResult",
    "build_league_models",
    "predict_match",
    "select_sure_picks",
]
