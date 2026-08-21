from backend.model.poisson import market_probabilities, poisson_pmf, score_matrix


def test_poisson_pmf_sums_to_one():
    total = sum(poisson_pmf(k, 1.4) for k in range(0, 20))
    assert abs(total - 1.0) < 1e-6


def test_score_matrix_normalised():
    m = score_matrix(1.6, 1.1)
    total = sum(p for row in m for p in row)
    assert abs(total - 1.0) < 1e-9


def test_home_favourite_has_higher_home_win():
    probs = market_probabilities(2.1, 0.8)
    assert probs["home_win"] > probs["away_win"]
    assert probs["home_win"] > probs["draw"]
    assert abs(probs["home_win"] + probs["draw"] + probs["away_win"] - 1) < 1e-9
    assert abs(probs["double_chance_home"] - (probs["home_win"] + probs["draw"])) < 1e-9


def test_high_lambdas_raise_over25_and_btts():
    quiet = market_probabilities(0.7, 0.6)
    open_game = market_probabilities(1.9, 1.7)
    assert open_game["over25"] > quiet["over25"]
    assert open_game["btts_yes"] > quiet["btts_yes"]
