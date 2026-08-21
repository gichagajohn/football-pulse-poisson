from datetime import date, timedelta

from backend.model.ratings import MatchResult, build_league_model, predict_match


def _match(home_id, away_id, hg, ag, days_ago, season=2026):
    return MatchResult(
        home_id=home_id,
        away_id=away_id,
        home_goals=hg,
        away_goals=ag,
        played_on=date(2026, 8, 21) - timedelta(days=days_ago),
        competition="PL",
        home_name=f"T{home_id}",
        away_name=f"T{away_id}",
        season=season,
    )


def test_strong_attack_gets_higher_lambda():
    # 8 teams, round-robin-ish: team 1 always scores 3, team 8 always scores 0
    matches = []
    as_of = date(2026, 8, 21)
    day = 20
    teams = list(range(1, 9))
    for i, home in enumerate(teams):
        for away in teams:
            if home == away:
                continue
            hg = 3 if home == 1 else (0 if home == 8 else 1)
            ag = 3 if away == 1 else (0 if away == 8 else 1)
            matches.append(_match(home, away, hg, ag, day))
            day += 1
    model = build_league_model(matches, "PL", as_of)
    assert model is not None
    assert model.ratings[1].attack > model.ratings[8].attack
    pred = predict_match(model, 1, 8)
    assert pred is not None
    assert pred["lambda_home"] > pred["lambda_away"]
    assert pred["probs"]["home_win"] > 0.5
