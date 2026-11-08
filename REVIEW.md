# Code review — `gichagajohn/football-pulse-poisson`

Reviewed at commit `0a4675c` (last commit 2026-08-28), ~2,500 lines of Python, 12 commits.
Checked by cloning, installing deps, running the suite (`14 passed in 0.21s`) and running
targeted numerical experiments against the model and the selector.

**Verdict:** the code is clean, honest and unusually well-scaffolded for a hobby bot — no LLM
hand-waving, thresholds centralised, ticket is a plain f-string so odds can't be rewritten,
`NO BET` is a first-class outcome and the tests pass. The problems are not typos; they are
three structural things: a data tier that can't supply what the model asks for, an API
credit budget that runs dry mid-month, and a pick criterion that is mathematically guaranteed
to select cases where the book disagrees with the model.

---

## Severity summary

| # | Finding | Severity | Where |
|---|---|---|---|
| 1 | Odds API free tier (500 credits/mo) is overspent by ~8–40% | **Critical (ops)** | `scout_agent.py:288`, `config.py:79` |
| 2 | football-data.org free tier is *current season only* → previous-season code is dead | **High** | `history.py:102`, `ratings.py:104` |
| 3 | The EV gate selects maximum model-vs-market disagreement, with no calibration or backtest | **High (design)** | `select.py:129` |
| 4 | Name-substring fixture matching can bind the wrong football-data ID → wrong grading | **High (correctness)** | `scout_agent.py:196` |
| 5 | Unfinished/postponed legs leave a ticket `pending` forever, re-polling daily | Medium | `result_checker.py:86` |
| 6 | Synthetic double-chance prices: +EV measured against a price you can't get | Medium | `select.py:41` |
| 7 | Weather + injuries are fetched, stored, and never used by the model | Medium (dead weight) | `scout_agent.py:307-480` |
| 8 | `_merge_fd_ids` accepts a `None` fixture id, contradicting its own comment | Low | `scout_agent.py:205` |
| 9 | `ticket_outcome` "mixed" for 2 wins + 1 void is inconsistent | Low | `result_checker.py:123` |
| 10 | GitHub disables `schedule:` workflows after 60 days of repo inactivity | Low (ops) | `.github/workflows/*.yml` |

---

## 1. The Odds API budget does not fit the free tier

`fetch_league_odds()` makes **two** requests per league (`h2h,totals`, then `btts`).
The Odds API bills **1 credit per market per region**, so per league per day:

```
h2h + totals (region eu)  = 2 credits
btts                      = 1 credit  (if the market is featured)
```

6 leagues × 3 = **18 credits/day → ~540 credits/month** against a **500 credit/month**
free plan [1](https://sportsapis.dev/apis/the-odds-api). Even in the best case
(`btts` 422s for every league, so you only pay 12/day = 360/month) you have 28% headroom
for retries and manual `workflow_dispatch` runs. Realistically the job dies around the
20th of the month, and it dies **silently**: `_fetch_odds_market` swallows the 401/429
and returns `[]`, so Scout falls back to football-data fixtures with no odds, `select`
publishes `NO BET`, and the only symptom is a run of unexplained `NO BET TODAY` emails.

Fixes, in order of preference:

1. Drop `btts` entirely (it's the weakest-priced market anyway, and the model's BTTS
   edge is the least trustworthy of the four). 12 credits/day = 360/month. ✅
2. Or fetch `btts` only for leagues you're actually going to bet, and only once per week.
3. Or request a single combined call per league with `markets=h2h,totals` and read BTTS
   from `alternate` markets — still 2 credits but one round-trip.
4. Add a credit guard: log `x-requests-remaining` from the response headers (The Odds API
   returns `x-requests-used` / `x-requests-remaining`) and skip the run with a clear
   `NO BET — odds quota exhausted` reason instead of an unexplained empty ticket.

Also treat a 401/429 as **fatal for the run**, not as "no odds".

## 2. The previous-season half of the ratings model can never run

`history.py:102` requests `seasons = [current, current - 1]` from
`/competitions/{code}/matches`. The football-data.org **free tier is limited to the
current season**; multi-season history is a paid add-on. So every run logs

```
[HISTORY] Failed PL season 2025: ...   (or returns 0 matches)
```

and `PREVIOUS_SEASON_FACTOR = 0.45` in `ratings.py:104` is dead code. Consequences:

- Ratings come from *this season only*. Combined with `MIN_WEIGHTED_GAMES = 6.0` and
  `HALF_LIFE_DAYS = 120`, a team needs ~6–7 matches before it qualifies — so the bot is
  **structurally silent until roughly matchday 7** of each season, and the "down-weighted
  previous season so early August is not pure noise" design note in the README isn't true.
- In a CL model each team has ~6 CL games a season, so the CL leg will almost never pass
  the weighted-games gate either.

Either (a) accept it and delete the dead branch + document the cold start, or (b) get real
history from a free source — `football-data.co.uk` CSVs give 20+ seasons of results with
no key and no rate limit, which is the single biggest quality upgrade available here.
Fitting attack/defence on 3 seasons of real data rather than 7 matches would do more for
this model than any threshold tweak.

## 3. The selector's edge test is adversarially framed

This is the one I'd think hardest about. The gate is:

```
model_p >= min_p  AND  min_odds <= odds <= max_odds  AND  model_p * odds - 1 >= 0.04
```

I simulated the selector over 3,000 synthetic days of 40 fixtures, generating prices from
the model's own probabilities plus a book overround and realistic price noise:

| Book noise around the model | Days with ≥1 pick | Avg picks/day |
|---|---|---|
| ±3% | **0%** | 0.00 |
| ±6% | **0%** | 0.00 |
| ±10% | 86% | 1.71 |

Read that table carefully. If the book is anywhere near efficient, this system publishes
`NO BET` **every single day**. So the picks that do go out are, by construction, exactly
the fixtures where **the market (many sharp books, team news, line movement, xG) disagrees
with a goals-only Poisson fit by 5+ probability points** — i.e. the cases where the model
is most likely to be the one that's wrong. That's textbook adverse selection, and no
amount of `min_p` tightening fixes it, because the EV gate is what's doing the selecting.

The fix is not "loosen the gate". It's:

- **Shrink the model toward the market** before testing edge:
  `p_adj = w * p_model + (1 - w) * p_market_devigged`, then require
  `p_adj * odds - 1 >= min_ev`. With `w = 0.5` you only bet when the model survives
  half-shrinkage — cheap, one function, and it kills the wild outliers.
- **Track closing line value.** You store the odds at pick time (`ticket_selections.odds`)
  but never the closing price. Add a `closing_odds` column filled by the result checker
  (re-fetch the event right before kickoff, or store the `commence_time` snapshot), and
  report **CLV** weekly. CLV is the only metric that tells you whether you have edge in
  ~50 picks; hit-rate and ROI on 3 singles/day need ~500+ bets to distinguish skill from
  variance, at which point you've spent a year.
- **Backtest before you bet.** There is no backtest script in the repo. The whole model is
  deterministic and free — you can replay 3 seasons of `football-data.co.uk` results offline
  in a minute and get a real hit rate per market. That's a weekend's work that turns the
  weekly report from a diary into evidence.

One more: sorting candidates by `ev` desc (`select.py:129`) deliberately picks the market
where the model most disagrees with the book, per fixture. Sorting by `model_p` desc, or by
EV *after* shrinkage, would be less self-harming.

## 4. Fixture matching is substring-based and can bind the wrong ID

`_merge_fd_ids` (`scout_agent.py:196`) matches Odds API events to football-data fixtures with

```python
if (eh in home or home in eh) and (ea in away or away in ea):
```

Three problems:

- **Wrong bind.** "Sporting" ⊂ "Sporting CP" and "Sporting Lisbon"; "Inter" ⊂ "Inter Milan";
  "Real" ⊂ "Real Madrid" *and* "Real Sociedad" (not triggered by full names, but any short
  nickname would). A mis-bind writes the **wrong `fixture_id`**, and the result checker then
  grades your pick against a different match — silently, and it corrupts the weekly ROI.
- **No kickoff check.** Even with both names right, two fixtures can't be distinguished
  without also requiring `|utcDate_odds - utcDate_fd| < 30 min`. Cheap, and it makes the
  match effectively unique.
- **Encoding failures silently drop fixtures.** "Borussia Mönchengladbach" vs "Borussia
  Monchengladbach", "1. FC Köln" vs "FC Koln" won't match, and the fixture vanishes with
  only a count in the log. Add unicode folding (`unicodedata.normalize('NFKD', ...)` +
  strip combining marks) and a token-set comparison instead of substring.

The recent commit ("Enhance logging for matched Odds API fixtures") already exposes the
match rate — that's the right instinct. Next step is to make a **failed match fail loudly**
and drop the fixture rather than guessing.

## 5. Tickets with an unresolvable leg stay `pending` forever

`result_checker.py:86-119`: `all_graded` stays `False` if *any* leg's fixture isn't
`FINISHED`/`AWARDED`, and `update_ticket_outcome` is only called when `all_graded and
outcomes`. A postponed, abandoned or rescheduled leg therefore pins that ticket at
`pending`, and `get_pending_tickets(before_date=today)` re-fetches it **every day forever**
— burning football-data.org calls (10/min) and never clearing. Add a `resolved_at` or an
age cutoff: after N days, mark the leg `void` (a bookmaker would void it) and finalise the
ticket. `fetch_result` also can't tell "not played yet" from "POSTPONED" — it returns `None`
for both, so add the status to the return value.

## 6. Double-chance EV is measured against a price that doesn't exist

`derive_double_chance_odds` reverse-engineers a DC price from two vigged 1X2 prices:

```python
implied = (1 / home) + (1 / draw)
return round(1 / implied, 3)
```

Numerically it lands close to a real DC quote (I checked: derived 1.143 vs a de-vigged-plus-5%-margin
1.170), so the direction is fine — but you are then computing `model_p * synthetic_odds - 1`
and calling it `+EV`, when no book actually offers that number. The Odds API doesn't list
double chance, so this is unresolvable as written. Cleanest: **delete the two
`double_chance_*` rules.** They're your lowest-information markets, they're the only ones
whose price is invented, and the reachability window is razor-thin anyway (needs a home
price near 1.40 *with* a draw near 9.5 to clear `min_odds 1.22` while `min_p 0.78` holds).
If you keep them, at least compute the EV against the *devigged fair* price rather than the
raw combination, and label them `derived` on the ticket.

## 7. Weather and injuries: cost without signal

`cities.py` is a 169-line hardcoded team→city map; `fetch_weather_at_kickoff` makes one
OpenWeather call **per fixture per day**; `fetch_epl_injuries` pulls the whole FPL
bootstrap. None of it reaches the model:

- `injuries` is written into the fixture dict (`scout_agent.py:477`) and then read by
  nobody — `ratings.py` and `select.py` never look at it. The README implies a richer
  scouting layer than exists.
- Weather's **only** effect is `_wind_reject` at 60 km/h (`select.py:104`), and
  `>60 km/h` at a European stadium is a once-a-season event. Unknown weather deliberately
  doesn't reject, which makes the module a no-op on the days it matters (API failure).
- `_completeness(..., has_history_hook)` is never called with `True`, and
  `data_completeness` is stored on the fixture but never used as a gate.

Either wire injuries in (a simple availability multiplier on attack: `attack * (1 - 0.08 *
n_key_absent)`, capped) and make wind a soft penalty rather than a hard reject — or delete
the three modules and save ~350 lines, ~40 API calls a day and two failure modes. Right now
it's the worst of both: all the cost, none of the effect.

## 8–10. Smaller items

- **`scout_agent.py:205`** — `fx["id"] = fd.get("id")` runs inside the match branch without
  checking the id is non-`None`, directly under a comment saying only real football-data
  IDs are retained. Add `if fd.get("id") is not None:`.
- **`result_checker.py:123`** — 2 wins + 1 void produces `"mixed"`, not `"win"`. Also
  `"mixed"` and `"void"` ticket outcomes are excluded from the weekly ROI either way, so
  the branch differences never reach the report; simplify or document.
- **`_best_market` wind log (`select.py:108`)** — `float(...)` on `wind_kmh` will raise if
  the value is ever a non-numeric string; `_wind_reject` already guards, this doesn't.
- **GitHub Actions** — scheduled workflows are auto-disabled after 60 days without a commit
  to the repo. Last commit was 2026-08-28, so the daily ticket may already be stopped.
  Worth a monthly no-op commit or a manual `workflow_dispatch` reminder.
- **`combined_odds`** in `schema.sql` and `store_ticket` is always `None` — a leftover from
  the accumulator version. Fine, but it'll confuse the next reader.
- **Test coverage** — the 14 tests are all fast unit tests of the model/selector. There is
  no test for `_merge_fd_ids`, `_extract_odds_snapshot`, `format_ticket`, or the pipeline's
  NO-BET path. Those are where a regression would actually cost money. The merge function
  especially: a handful of fixture pairs (correct match, near-miss, wrong-league) would lock
  in the fix for #4.

---

## What I'd do first, in order

1. **Delete the `btts` request** and add the `x-requests-remaining` guard — 20 minutes,
   keeps the job alive all month.
2. **Add kickoff-time + unicode-folded token matching** to `_merge_fd_ids` and make an
   unmatched fixture an explicit drop — 1 hour, protects the ROI numbers.
3. **Shrink model probabilities toward the devigged market** before the EV test — 30 lines,
   and it's the difference between "the model picks outliers" and "the model filters the
   market".
4. **Write `scripts/backtest.py`** against `football-data.co.uk` CSVs — the only thing that
   can tell you whether any of this has edge, and it costs nothing to run.
5. Then and only then tune `MARKET_RULES`.

The architecture is good. The gates are honest. The missing piece is evidence.

---

[1] The Odds API credit model and 500-credit free tier,
<https://sportsapis.dev/apis/the-odds-api>. football-data.org free tier = current season
only, 12 competitions, 10 req/min, <https://www.thestatsapi.com/blog/football-data-org-free-tier-limits-2026>.

*Not financial advice. A Poisson model with no team news is a prior, not an edge.*
