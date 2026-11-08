from backend.model.select import (
    adjusted_probability,
    devigged_market_probs,
    market_probability,
    select_sure_picks,
)
from backend.result_checker import grade_selection


def test_grade_core_markets():
    assert grade_selection("home_win", 2, 1) == "win"
    assert grade_selection("home_win", 1, 2) == "loss"
    assert grade_selection("draw", 1, 1) == "win"
    assert grade_selection("double_chance_home", 0, 0) == "win"
    assert grade_selection("double_chance_away", 2, 1) == "loss"
    assert grade_selection("draw_no_bet_home", 1, 1) == "void"
    assert grade_selection("btts_yes", 1, 1) == "win"
    assert grade_selection("btts_yes", 2, 0) == "loss"
    assert grade_selection("over25", 2, 1) == "win"
    assert grade_selection("over25", 1, 1) == "loss"


def _match(**kwargs):
    base = {
        "fixture_id": 1,
        "home_team": "Arsenal",
        "away_team": "Burnley",
        "league": "Premier League",
        "competition_code": "PL",
        "lambda_home": 2.0,
        "lambda_away": 0.7,
        "home_games": 12,
        "away_games": 12,
        "weather": {"wind_kmh": 10},
        "odds_snapshot": {
            "home_win": 1.95,
            "draw": 3.6,
            "away_win": 4.2,
            "over25": 1.80,
            "btts_yes": 1.90,
        },
        "probs": {
            "home_win": 0.66,
            "draw": 0.20,
            "away_win": 0.14,
            "over25": 0.48,
            "btts_yes": 0.44,
            "double_chance_home": 0.86,
            "double_chance_away": 0.34,
        },
    }
    base.update(kwargs)
    return base


def test_sure_mode_publishes_home_favourite():
    out = select_sure_picks([_match()], model_weight=0.6)
    assert out["decision"] == "PUBLISH"
    assert len(out["selections"]) == 1
    pick = out["selections"][0]
    assert pick["market"] == "home_win"
    # The gate must run on the blended probability, not the raw model, and
    # both numbers have to be on the pick so the ticket is auditable.
    assert pick["adj_p"] < pick["model_p"]
    assert pick["market_p"] is not None
    assert abs(pick["adj_p"] - 0.6 * pick["model_p"] - 0.4 * pick["market_p"]) < 1e-3


def test_devigged_market_probs_sum_to_one():
    probs = devigged_market_probs(_match()["odds_snapshot"])
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert probs["home_win"] < 1 / 1.95  # margin removed


def test_shrinkage_blocks_a_thin_model_edge():
    """The old gate published this; it is exactly the adverse-selection case.

    Model 70% vs a book implying 66.7% (devigged ~63%) used to be a 1.50
    home-win "value" pick purely because the Poisson disagreed with the
    market by 3 points. Blended, the edge is gone — and the model, which has
    no team news, should not win an argument with 40 books by 3 points.
    """
    thin = _match(
        odds_snapshot={
            "home_win": 1.50,
            "draw": 4.2,
            "away_win": 6.5,
            "over25": 1.8,
            "btts_yes": 1.9,
        },
        probs={
            "home_win": 0.70,
            "draw": 0.18,
            "away_win": 0.12,
            "over25": 0.50,
            "btts_yes": 0.45,
            "double_chance_home": 0.88,
            "double_chance_away": 0.34,
        },
    )
    out = select_sure_picks([thin], model_weight=0.6)
    assert out["decision"] == "NO_BET"


def test_default_weight_is_the_raw_model():
    """Measured, not assumed: see config.MODEL_WEIGHT.

    The backtest found shrinkage raised the loss per bet, so the shipped
    default must stay at the raw model until real data says otherwise.
    """
    from backend.config import MODEL_WEIGHT

    assert MODEL_WEIGHT == 1.0


def test_raw_model_weight_publishes_the_thin_edge():
    """MODEL_WEIGHT is a dial, not a hard-coded opinion."""
    thin = _match(
        odds_snapshot={
            "home_win": 1.50,
            "draw": 4.2,
            "away_win": 6.5,
            "over25": 1.8,
            "btts_yes": 1.9,
        },
        probs={
            "home_win": 0.70,
            "draw": 0.18,
            "away_win": 0.12,
            "over25": 0.50,
            "btts_yes": 0.45,
            "double_chance_home": 0.88,
            "double_chance_away": 0.34,
        },
    )
    assert select_sure_picks([thin], model_weight=1.0)["decision"] == "PUBLISH"


def test_adjusted_probability_without_a_price_is_the_model():
    assert adjusted_probability(0.61, None) == 0.61


def test_two_way_market_probability_is_discounted_for_margin():
    book = market_probability("over25", {"over25": 1.80})
    assert book is not None and book < 1 / 1.80


def test_sure_mode_rejects_longshot_and_no_odds():
    longshot = _match(
        fixture_id=2,
        probs={
            "home_win": 0.40,
            "draw": 0.28,
            "away_win": 0.32,
            "over25": 0.40,
            "btts_yes": 0.40,
            "double_chance_home": 0.68,
            "double_chance_away": 0.60,
        },
    )
    no_odds = _match(fixture_id=3, odds_snapshot={})
    out = select_sure_picks([longshot, no_odds])
    assert out["decision"] == "NO_BET"


def test_one_pick_per_match_and_cap():
    matches = [_match(fixture_id=i, home_team=f"H{i}") for i in range(5)]
    out = select_sure_picks(matches, max_selections=2)
    assert out["decision"] == "PUBLISH"
    assert len(out["selections"]) == 2


def test_high_wind_rejects():
    out = select_sure_picks([_match(weather={"wind_kmh": 75})])
    assert out["decision"] == "NO_BET"


def test_wind_reject_survives_a_non_numeric_reading():
    """The log line used to float() the value unguarded and raise."""
    out = select_sure_picks([_match(weather={"wind_kmh": "n/a"})])
    assert out["decision"] == "PUBLISH"


def test_double_chance_is_no_longer_selectable():
    """Its price was synthesised from vigged 1X2 — EV against a fiction."""
    from backend.config import MARKET_RULES

    assert "double_chance_home" not in MARKET_RULES
    assert "double_chance_away" not in MARKET_RULES
