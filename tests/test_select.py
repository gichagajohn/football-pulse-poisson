from backend.model.select import select_sure_picks
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
        "odds_snapshot": {"home_win": 1.50, "draw": 4.2, "away_win": 6.5, "over25": 1.8, "btts_yes": 1.9},
        "probs": {
            "home_win": 0.70,
            "draw": 0.22,
            "away_win": 0.12,
            "over25": 0.50,
            "btts_yes": 0.45,
            "double_chance_home": 0.88,
            "double_chance_away": 0.34,
        },
    }
    base.update(kwargs)
    return base


def test_sure_mode_publishes_home_favourite():
    out = select_sure_picks([_match()])
    assert out["decision"] == "PUBLISH"
    assert len(out["selections"]) == 1
    assert out["selections"][0]["market"] == "home_win"


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
