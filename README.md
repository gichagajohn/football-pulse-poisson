# Football Pulse Poisson

Standalone **Dixon–Coles Poisson** football picker. Completely free inference.
No Groq, no LLM, no paid model.

This is a **new project**. It does not replace and does not talk to
`football_ai` (the Groq 9-agent edition). Upload this folder to a **new**
GitHub repo and a **new** Supabase project so the two systems never share
tickets.

---

## What it does

- **Daily (08:20 EAT)**: grades yesterday’s singles, pulls today’s top-5 + UCL
  fixtures, fits attack/defence from real scores, prices 1X2 / O2.5 / BTTS,
  emails **independent singles** that pass sure-mode — or **NO BET TODAY**.
- **Weekly (Sunday 09:20 EAT)**: hit rate and ROI at **1 unit per single**.

A pick is emailed only if all of these hold:

1. Both clubs have enough weighted history
2. The probability is high (e.g. home win ≥ 58%) — the model's own, unless
   `MODEL_WEIGHT` is lowered to blend it with the de-vigged market price
3. Odds sit in a conservative band (no 1.05 traps, no longshots)
4. EV = `p × odds − 1` ≥ ~4%
5. One market per match, max 3 singles (never an accumulator)
6. The Odds API still has credits left, and the fixture matched a real
   football-data.org ID (same clubs, same competition, kickoff within 30 min)

The ticket prints λ, model %, market %, blended %, book % and EV so you can
check the arithmetic — and see by how much the "edge" was model disagreement.

**This is still gambling.** “Sure” means transparent and conservative, not
guaranteed.

---

## Create the new GitHub repo (does not touch `football_ai`)

On your machine, in this folder:

```bash
git init
git add .
git commit -m "Football Pulse Poisson — free Dixon-Coles model"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/football-pulse-poisson.git
git push -u origin main
```

Create the empty repo on GitHub first (no README, no .gitignore — this folder
already has both).

---

## Setup

### 1. New Supabase project (do not reuse the old one)

If you point this at the same Supabase as `football_ai`, both jobs will fight
over `ticket_date` (unique). Use a **new** project.

1. [supabase.com](https://supabase.com) → New Project
2. SQL Editor → paste `backend/db/schema.sql` → Run
3. Project Settings → API:
   - Project URL → `SUPABASE_URL`
   - **service_role** key → `SUPABASE_KEY` (never the anon key)

### 2. Free data keys

You may reuse the same keys as `football_ai`. This job is paced for the
football-data.org free tier (10 req/min). Cron is offset to **08:20 EAT** so
it does not collide with the 08:00 Groq job.

| Secret | Where |
|---|---|
| `FOOTBALL_DATA_KEY` | [football-data.org](https://www.football-data.org/client/register) |
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com/) |
| `OPENWEATHER_KEY` | [openweathermap.org](https://openweathermap.org/api) (5-day forecast) |

### 3. Gmail App Password

1. Enable 2-Step Verification
2. [App passwords](https://myaccount.google.com/apppasswords) → Mail
3. That 16-character value is `SMTP_PASSWORD`

Same inbox as the old bot is fine — subjects start with `Football Pulse AI`
either way; this edition’s body says **Dixon-Coles Poisson** on line 1.

### 4. GitHub Secrets (on the NEW repo only)

Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `FOOTBALL_DATA_KEY` | football-data.org token |
| `ODDS_API_KEY` | The Odds API key |
| `OPENWEATHER_KEY` | OpenWeather key |
| `SUPABASE_URL` | new project URL |
| `SUPABASE_KEY` | new project service_role key |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | Gmail address |
| `SMTP_PASSWORD` | App password |
| `EMAIL_TO` | inbox for tickets |

No `GROQ_API_KEY`. It is not used.

### 5. Test

Actions → **Football Pulse AI — Daily Ticket** → Run workflow.

Locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill keys
pytest
python -m backend.daily_run
```

---

## Model (short)

```
λ_home = attack_home × defence_away × league_avg_home_goals
λ_away = attack_away × defence_home × league_avg_away_goals
```

Scorelines ~ Poisson(λ) with Dixon-Coles ρ ≈ −0.10 on 0-0 / 1-0 / 0-1 / 1-1.
Thresholds live in `backend/config.py`.

### ⚠️ The backtest result (read this before trusting a pick)

`scripts/backtest.py` replays 4 seasons x 5 leagues of real Bet365 prices and
real results — ~28,000 matches — using the same ratings, Poisson and selector
code as production, refitting only on matches already played:

```
MODEL_WEIGHT   bets   hit%   avg odds     ROI      avg CLV
1.00           2472   56.2       1.71   -5.53%      -0.40%
0.80           2199   55.9       1.70   -6.43%      -0.44%
0.60           1736   55.2       1.69   -8.05%      -0.44%
0.40            993   52.5       1.68  -12.78%      -0.63%
```

**Every setting loses money, and the average closing-line value is negative.**
There is no positive-EV configuration of these gates in this sample. Compare
against flat-betting the same markets with no model at all:

```
market           bets      ROI
home_win         7082   -8.22%
away_win         7082  -10.14%
over25           7082   -3.96%
1x2_favourite    7082   -2.08%
```

The model's own 1X2 picks do **−16.3%** (home) and **−20.9%** (away) at
w=0.6 — worse than blind betting, i.e. the gates actively select bad bets.
Its O2.5 picks (−4.85%) are merely indistinguishable from the vig (−3.96%).

And the model is not calibrated where it bets:

```
 60– 80%  predicted 68.4%  realised 61.4%  delta -7.0   <- the band the gates bet in
 80–100%  predicted 85.1%  realised 77.6%  delta -7.5
```

A goal-only Poisson is systematically overconfident on strong favourites
(classic favourite–longshot bias). That is `min_p: 0.58–0.78` territory, so
every published 1X2 single is drawn from the model's most biased band.

Treat the ticket as a documented record of a statistical model, not as a
betting edge. Reproduce with:

```bash
python -m scripts.backtest                          # all leagues, 4 seasons
python -m scripts.backtest --seasons 2526 --divs E0
```

### Model vs market — the knob, and why it defaults to off

The gates can run on a probability shrunk toward the de-vigged market price:

```
p_adj = MODEL_WEIGHT × model_p + (1 − MODEL_WEIGHT) × market_p_devigged
```

`MODEL_WEIGHT` defaults to **1.0** (the raw model). It was 0.6 in the first
draft of these changes, on the theory that a goal-only model should not win an
argument with 40 books; the backtest then showed shrinkage makes the *loss per
bet* worse, because the picks that survive it are the highest-disagreement
fixtures. The knob is kept for experimentation, and each pick prints
model / market / blended / EV plus `ev_raw` so any setting is auditable.

### Odds API budget (this matters on the free plan)

The free plan is **500 credits/month**, and one credit = one market × one
region. Daily h2h + totals across 6 leagues = 12 credits/day ≈ 360/month,
which fits. BTTS is a third credit per league per day ≈ 540/month, which does
not fit — so `ODDS_FETCH_BTTS` is **off by default**, and the run stops
cleanly (NO BET with a quota reason) when `x-requests-remaining` reaches
`ODDS_MIN_CREDITS_REMAINING`.

Match markets are **1X2, O2.5 and BTTS only**. Double chance was removed: The
Odds API publishes no DC market, so its price was reverse-engineered from
vigged 1X2 and its "EV" was measured against a number no book offers.

### History: what the free tier does not give you

football-data.org free is **current season only**, so the previous-season
down-weighting (`PREVIOUS_SEASON_FACTOR`) has nothing to weight, and ratings
need ~6–7 matches before `MIN_WEIGHTED_GAMES` is satisfied. Expect the bot to
be quiet in August/September each season. A free upgrade path is
`football-data.co.uk` CSVs (20+ seasons, no key) — that is the next real
improvement to the model.

---

## Files

```
.github/workflows/daily.yml
.github/workflows/weekly.yml
.env.example
.gitignore
pytest.ini
requirements.txt
README.md

backend/
  config.py
  pipeline.py
  publisher.py
  daily_run.py
  weekly_run.py
  email_sender.py
  result_checker.py
  weekly_report.py
  cities.py
  agents/scout_agent.py
  model/poisson.py
  model/ratings.py
  model/history.py
  model/select.py
  db/schema.sql
  db/supabase_client.py
tests/
  test_poisson.py
  test_ratings.py
  test_select.py
  test_cities.py
```
