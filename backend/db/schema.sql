-- ============================================================
-- FOOTBALL PULSE AI — Supabase Schema
-- ============================================================
-- Run in Supabase SQL Editor before the first GitHub Actions run.
-- Safe to re-run (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

create table if not exists prediction_tickets (
    id bigint generated always as identity primary key,
    ticket_date date not null unique,
    status text not null default 'pending',   -- pending | published | no_bet
    combined_odds numeric,                    -- unused for singles; kept for history
    selection_count smallint,
    final_confidence numeric,                 -- mean model_p of published singles
    risk_level text,
    reason text,
    ticket_text text,
    outcome text default 'pending',           -- pending | win | loss | void | mixed
    created_at timestamptz default now()
);

create table if not exists ticket_selections (
    id bigint generated always as identity primary key,
    ticket_date date not null references prediction_tickets(ticket_date),
    fixture_id bigint not null,
    home_team text,
    away_team text,
    league text,
    market text,
    odds numeric,
    rationale text,
    outcome text default 'pending',           -- pending | win | loss | void
    home_score smallint,
    away_score smallint
);

alter table ticket_selections
    add column if not exists home_score smallint;
alter table ticket_selections
    add column if not exists away_score smallint;

create unique index if not exists ticket_selections_unique
    on ticket_selections (ticket_date, fixture_id, market);

create index if not exists idx_tickets_date on prediction_tickets(ticket_date);
create index if not exists idx_selections_date on ticket_selections(ticket_date);

-- RLS stays off: writes use the service_role key from GitHub Actions,
-- which bypasses RLS. Enable RLS before exposing a public dashboard.
