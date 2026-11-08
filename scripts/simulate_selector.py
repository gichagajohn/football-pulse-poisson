"""
Simulate the selector against a market that is *right* and a model that is
*noisy*, to measure what the EV gate actually buys.

This is a synthetic worst-case benchmark, not a backtest of real results: it
answers "if the closing line is an unbiased estimate of the truth and my
Poisson is the same truth plus extra noise, what does this gate do?"

    python -m scripts.simulate_selector --trials 4000 --model-sigma 0.08

The interesting pair of columns is CLAIMED EV vs ROUGH ROI. The gate reports a
positive EV on every pick by construction; if the realised ROI sits near the
overround instead, the "edge" was the model's own error term, not information.
Shrinking the model toward the market cuts exposure without inventing skill —
no weight setting beats the vig here, and none should be expected to.
"""

from __future__ import annotations

import argparse
import random

from backend.config import MARKET_RULES, MAX_SELECTIONS
from backend.model.poisson import market_probabilities
from backend.model.select import adjusted_probability

OVERROUND = 1.05


def _sample_score(lam_home: float, lam_away: float, rng: random.Random) -> tuple[int, int]:
    def pois(lam: float) -> int:
        # Knuth; λ is small here.
        import math

        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1

    return pois(lam_home), pois(lam_away)


def _settles(market: str, hg: int, ag: int) -> bool:
    if market == "home_win":
        return hg > ag
    if market == "away_win":
        return ag > hg
    if market == "over25":
        return hg + ag >= 3
    if market == "btts_yes":
        return hg > 0 and ag > 0
    return False


def run(trials: int, model_sigma: float, market_sigma: float, fixture_prior: float,
        model_weight: float, seed: int = 11) -> dict:
    rng = random.Random(seed)
    staked = returned = 0.0
    picks = 0
    claimed_ev = 0.0

    for _ in range(trials):
        if rng.random() > fixture_prior:
            continue
        lam_home = rng.uniform(0.7, 2.6)
        lam_away = rng.uniform(0.5, 1.9)
        truth = market_probabilities(lam_home, lam_away)

        # Market: truth + its own small error, renormalised.
        raw = {
            "home_win": max(truth["home_win"] + rng.gauss(0, market_sigma), 0.01),
            "draw": max(truth["draw"] + rng.gauss(0, market_sigma), 0.01),
            "away_win": max(truth["away_win"] + rng.gauss(0, market_sigma), 0.01),
        }
        norm = sum(raw.values())
        market_p = {k: v / norm for k, v in raw.items()}

        # Model: truth + larger error.
        model_p = {
            k: max(v + rng.gauss(0, model_sigma), 0.005)
            for k, v in truth.items()
        }

        # Decimal odds = 1 / (book probability x overround). Getting this
        # backwards is how a simulation "discovers" free money.
        def price(p: float) -> float:
            return round(1.0 / (max(p, 0.01) * OVERROUND), 3)

        snapshot = {
            "home_win": price(market_p["home_win"]),
            "away_win": price(market_p["away_win"]),
            "over25": price(max(truth["over25"] * (1 + rng.gauss(0, market_sigma)), 0.05)),
            "btts_yes": price(max(truth["btts_yes"] * (1 + rng.gauss(0, market_sigma)), 0.05)),
        }

        best = None
        for market, rules in MARKET_RULES.items():
            odds = snapshot.get(market)
            if odds is None:
                continue
            if not (rules["min_odds"] <= odds <= rules["max_odds"]):
                continue
            book_p = (
                market_p[market] if market in market_p else (1 / odds) / OVERROUND
            )
            adj = adjusted_probability(model_p[market], book_p, model_weight)
            if adj < rules["min_p"]:
                continue
            ev = adj * odds - 1.0
            if ev < rules["min_ev"]:
                continue
            if best is None or ev > best["ev"]:
                best = {"market": market, "odds": odds, "ev": ev}

        if not best:
            continue
        picks += 1
        staked += 1.0
        claimed_ev += best["ev"]
        hg, ag = _sample_score(lam_home, lam_away, rng)
        if _settles(best["market"], hg, ag):
            returned += best["odds"]

    roi = (returned - staked) / staked * 100 if staked else 0.0
    return {
        "picks": picks,
        "claimed_ev_pct": round(claimed_ev / picks * 100, 2) if picks else 0.0,
        "picks_per_1000_fixtures": round(picks / max(trials, 1) * 1000, 1),
        "roi_pct": round(roi, 2),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=4000)
    ap.add_argument("--model-sigma", type=float, default=0.08)
    ap.add_argument("--market-sigma", type=float, default=0.015)
    ap.add_argument("--fixture-prior", type=float, default=0.25,
                    help="probability a simulated fixture is in scope that day")
    args = ap.parse_args()

    print(f"model_sigma={args.model_sigma}  market_sigma={args.market_sigma}  "
          f"max_selections={MAX_SELECTIONS}")
    for weight in (1.0, 0.8, 0.6, 0.4):
        res = run(args.trials, args.model_sigma, args.market_sigma,
                  args.fixture_prior, weight)
        print(f"  MODEL_WEIGHT={weight:.1f}  picks={res['picks']:5d} "
              f"({res['picks_per_1000_fixtures']:5.1f}/1000 fixtures)  "
              f"claimed EV/pick={res['claimed_ev_pct']:+5.1f}%  "
              f"ROI={res['roi_pct']:+6.2f}%")
