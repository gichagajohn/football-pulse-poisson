# Review fixes — branch `review-fixes`

Base: `0a4675c`. Everything below is implemented on the branch, `pytest` is
**34 passed** (was 14), and every claim has a test or a command you can re-run.

| # | Fix | Files |
|---|---|---|
| 1 | Odds API spend control: BTTS off by default, credit floor, 401/429 fatal | `agents/scout_agent.py`, `config.py`, `pipeline.py` |
| 2 | Fixture matching: token sets + competition + kickoff + ambiguity guard | `agents/scout_agent.py` |
| 3 | Gates *can* run on a model shrunk toward the de-vigged market — **default 1.0, because the backtest says shrink hurts** | `model/select.py`, `config.py`, `publisher.py` |
| 4 | Double chance removed (synthetic price → fake EV) | `config.py`, `model/select.py` |
| 5 | Postponed/abandoned legs void instead of pinning a ticket forever | `result_checker.py` |
| 6 | `mixed` bug: 2 wins + 1 void is now `win` | `result_checker.py` |
| 7 | Walk-forward backtest on 4 seasons x 5 leagues of real prices | `scripts/backtest.py` |
| 7b | Simulation harness (synthetic sanity check) | `scripts/simulate_selector.py` |
| 8 | 20 new tests: matching, shrinkage, grading, wind guard | `tests/` |

Nothing was pushed — the repo remote is yours, and pushing from here would need
your credentials. To publish:

```bash
git push -u origin review-fixes    # then open a PR
```

---

## 1. The Odds API no longer runs out mid-month

**Before:** two requests per league per day (`h2h,totals` + `btts`) × 6 leagues
= 18 credits/day ≈ 540/month on a 500-credit free plan. The 429 was swallowed
and returned `[]`, so the failure mode was a run of `NO BET TODAY` emails that
looked exactly like the model being disciplined.

**Now:**

- `ODDS_FETCH_BTTS` defaults to `False` → 12 credits/day ≈ **360/month**, ~28%
  headroom for retries and manual dispatch.
- `x-requests-remaining` is read from every response and logged.
- At or below `ODDS_MIN_CREDITS_REMAINING` (default 25), at 401, or at 429, the
  scout raises `OddsQuotaExhausted`; the pipeline converts that into a NO BET
  with an explicit reason:

  > *Odds API quota exhausted — no prices available, and a pick without a real
  > book price is not a pick. (Odds API credits remaining (24) at or below the
  > floor (25).)*

- A 422 on BTTS is still treated as an expected capability response, not a
  failure. Any other non-200 is logged with its status.

Cost of running without BTTS: the `btts_yes` rule stays in `config.py` but will
simply never fire, which is visible in the selector's `failed sure-gates` count.

## 2. Fixture matching can no longer bind the wrong match

**Before:** `if (eh in home or home in eh) and …` — pure substring, no kickoff
check, no competition check, and `fx["id"] = fd.get("id")` ran without verifying
the id wasn't `None`, directly under a comment promising exactly that.

A wrong bind writes the wrong `fixture_id` into `ticket_selections`, and the
result checker then grades your pick against a different match. Nothing raises;
the weekly ROI is just quietly wrong. That is the worst class of bug in a system
whose only output is a performance number.

**Now** a fixture binds only when all of these hold:

1. home and away match on **token sets**, after unicode folding
   (`Mönchengladbach` = `Monchengladbach`, `1. FC Köln` = `1. FC Koln`)
2. same competition code
3. kickoff within `FIXTURE_MATCH_TOLERANCE_MINUTES` (default 30)
4. exactly one candidate competes for the bind — two candidates is a coin flip,
   so both are dropped
5. the football-data record carries a non-`None` id
6. that id hasn't already been consumed by an earlier fixture

Every drop is logged with the club names, competition and kickoff, so an
encoding mismatch shows up as a `WARNING` instead of a missing fixture.

Short tokens are rejected as identifiers (`Real` no longer matches
`Real Madrid`); legal suffixes are stripped (`Arsenal FC` = `Arsenal`).

Covered by `tests/test_scout_matching.py` — 10 cases including accent variants,
wrong kickoff, wrong competition, ambiguous bind, null id and double-bind.

## 3. Gates can run on a blended probability — but the default is the raw model

> **Correction to my first review.** I recommended `MODEL_WEIGHT=0.60` on the
> strength of a synthetic simulation. I then backtested it on real data and it
> is worse. The shipped default is now **1.0**; the mechanism is kept as an
> opt-in knob. Numbers below.

`backend/model/select.py` now computes, per market:

```
market_p  = de-vigged book probability (proportional method for 1X2,
            assumed TWO_WAY_OVERROUND for O2.5 / BTTS)
p_adj     = 0.60 × model_p + 0.40 × market_p
EV        = p_adj × odds − 1          ← this is what has to clear min_ev
```

`min_p` and the sort order also use `p_adj`. Each pick carries `model_p`,
`market_p`, `adj_p`, `ev` and `ev_raw`, and the ticket prints all of them so you
can see how much of the edge was the model arguing with the book.

**Evidence, take 1 (synthetic, misleading).** `scripts/simulate_selector.py`
assumes a market that is right and a model that is that truth plus noise:

```
MODEL_WEIGHT=1.0   picks=1032  claimed EV/pick=+13.5%  ROI= -4.54%
MODEL_WEIGHT=0.6   picks= 512  claimed EV/pick= +8.8%  ROI= -3.59%
```

That looked like a mild vote *for* shrinkage. It was a vote for a model of the
world I invented, so I built the real thing.

**Evidence, take 2 (real, authoritative).** `scripts/backtest.py` — 4 seasons
(2022/23–2025/26) × 5 leagues, real Bet365 opening prices, real results,
refitting only on already-played matches (~28,000 priced matches):

```
weight   bets   hit%   avg odds     ROI      avg CLV   +CLV share
1.00     2472   56.2       1.71   -5.53%      -0.40%          39%
0.80     2199   55.9       1.70   -6.43%      -0.44%          38%
0.60     1736   55.2       1.69   -8.05%      -0.44%          38%
0.40      993   52.5       1.68  -12.78%      -0.63%          36%
```

Shrinkage **cuts volume and raises the loss per bet**. The mechanism is obvious
in hindsight: shrinking lowers `p_adj`, so the picks that survive are the ones
where model and market disagree most — precisely the adverse selection the
blend was supposed to remove. So the default is back to **1.0**.

The blunter findings from the same run, which matter more than the knob:

```
No-model baseline (flat stakes, no gates)      model's own picks at w=0.6
  home_win      7082  -8.22%                     home_win   225  -16.28%
  away_win      7082 -10.14%                     away_win   186  -20.91%
  over25        7082  -3.96%                     over25    1325   -4.85%
  1x2_favourite 7082  -2.08%

1X2 calibration, all priced matches, no gates
   0– 20%   n= 4390   predicted 13.4%  realised 17.0%   delta +3.6
  20– 40%   n= 9430   predicted 28.0%  realised 28.6%   delta +0.6
  40– 60%   n= 3418   predicted 49.2%  realised 47.5%   delta -1.6
  60– 80%   n= 1767   predicted 68.4%  realised 61.4%   delta -7.0
  80–100%   n=  411   predicted 85.1%  realised 77.6%   delta -7.5
```

Three things follow, and none of them are fixable by tuning a threshold:

1. **The gates are worse than blind betting on 1X2.** The model's home picks lose
   16% where betting every home side loses 8%. The selector is not filtering —
   it is selecting.
2. **The model is overconfident exactly where it bets.** `min_p: 0.58–0.78`
   lands in the 60–80% band, which is biased by −7 points. Capacity is being
   spent inside the worst-calibrated part of the distribution.
3. **Only O2.5 is near respectability** (−4.85% vs a −3.96% baseline), and even
   there the model adds nothing over the vig.

`MODEL_WEIGHT` therefore is not the fix, and neither is `min_p`. What *is*
worth doing, in order: fit a calibration layer (favourite–longshot bias is
well documented and this data shows it cleanly), then re-backtest, then decide
whether to publish at all.

## 4. Double chance removed

`MARKET_RULES` no longer contains `double_chance_home` / `double_chance_away`.
The price was reverse-engineered from vigged 1X2 (I measured derived 1.143 vs a
de-vigged-plus-margin 1.170 — close, but still a number no book publishes), so
"EV" was computed against a fiction. The helper functions stay so historical
tickets remain readable, and `grade_selection` still grades them.

## 5. Postponed legs no longer pin a ticket open forever

`fetch_result` now returns a marker for `POSTPONED / CANCELLED / SUSPENDED /
ABANDONED`, and such legs are voided (which is what a book does). Separately,
any leg still unresolved after `TICKET_MAX_PENDING_DAYS` (default 3) is voided,
so `get_pending_tickets()` stops re-polling the same ticket on every run and
burning football-data.org calls (10/min).

## 6. `ticket_outcome` bug

2 wins + 1 void used to record `"mixed"`. For a **singles** ticket that is a
winning day — the void was simply never staked. Extracted to
`ticket_outcome()` and covered by `tests/test_result_checker.py`.

---

## 7b. The backtest itself

`scripts/backtest.py` is the deliverable that matters most in this patch. It
reuses the production `build_league_model`, `predict_match`, `select_sure_picks`
and `grade_selection` unchanged — no reimplementation to drift out of sync —
walks forward one matchday at a time, bets the **opening** price (conservative;
the production run at 08:20 is not the closing line either), and reports ROI
plus **closing-line value**, which is the only metric that can validate an edge
on a few hundred bets.

Data comes from football-data.co.uk season CSVs (no key, no rate limit, cached
in `data/football-data/`, gitignored). BTTS is not in those files, so BTTS is
reported as unvalidated rather than scored — another argument for not betting a
market you cannot cheaply validate.

## Deliberately NOT changed

- **Weather / injuries.** `cities.py`, `fetch_weather_at_kickoff` and
  `fetch_epl_injuries` still exist. Injuries reach no model, and weather's only
  effect is a 60 km/h wind reject. I did not delete them (they're your call, and
  weather is a legitimate soft signal), but they cost ~40 API calls a day for
  one gate that fires a handful of times a season. If you want the cheap win:
  either feed injuries in as an attack multiplier or delete all three modules.
- **Previous-season ratings.** Empirically impossible on the free tier; the code
  path is harmless, and `history.py` already logs the failure. Real fix is
  `football-data.co.uk` CSVs.
- **`MARKET_RULES` thresholds.** Untouched on purpose. Tuning them against
  synthetic data would be astrology.

## The next thing to build

A calibration layer. The backtest says the model's 60–80% band is really ~61%
and its 80–100% band is really ~78%. Two cheap candidates:

- **Power/logit scaling**: fit `p' = sigmoid(a · logit(p) + b)` on out-of-sample
  history, one pair of parameters per market. Standard, two lines of numpy.
- **Fit ρ and the corner cells properly**: the Dixon–Coles low-score correction
  is currently a fixed literature value of −0.10; fitting it alongside attack
  and defence is the textbook treatment and would help 1X2 and O2.5 at once.

Then re-run `python -m scripts.backtest` and compare. Until some weight,
threshold or calibration shows a positive ROI **and** positive CLV out of
sample, the honest verdict on this project is the one the backtest prints:

> *every MODEL_WEIGHT setting lost money out of sample. No positive-EV
> configuration of these gates was found.*
