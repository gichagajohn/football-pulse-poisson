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
2. Model probability is high (e.g. home win ≥ 58%)
3. Odds sit in a conservative band (no 1.05 traps, no longshots)
4. EV = `model_p × odds − 1` ≥ ~4%
5. One market per match, max 3 singles (never an accumulator)

The ticket prints λ, model %, book %, and EV so you can check the arithmetic.

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
