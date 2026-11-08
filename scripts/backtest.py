"""
Walk-forward backtest on free historical data.

Data: football-data.co.uk season CSVs (no API key, no rate limit, 20+ seasons
of results with bookmaker odds, including CLOSING prices).

    python -m scripts.backtest                     # all cached leagues, 4 seasons
    python -m scripts.backtest --seasons 2526 --divs E0
    python -m scripts.backtest --weights 1.0,0.6   # compare MODEL_WEIGHT

What it does, per league and season:

  for each matchday:
      history = all matches in that season and the season before it
      model    = build_league_model(history, as_of=matchday)   <- same code as prod
      priced   = predict_match(...) for that day's fixtures    <- same code as prod
      picks    = select_sure_picks(priced, model_weight=w)     <- same code as prod
      settle each pick against the real scoreline

No synthetic inputs: the odds are real Bet365 prices, the results are real
results, and the ratings are fitted only on matches that had already been
played. The only thing it cannot model is the difference between the price our
08:20 run would have got and the price in the CSV, so it bets the OPENING
price (the less sharp, more conservative of the two) and reports the CLV
against the CLOSING price.

BTTS is not in these files, so BTTS is reported as unvalidated rather than
scored — which is itself a reason to think twice about betting it.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
import urllib.request
import zlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend.model.ratings import MatchResult, build_league_model, predict_match
from backend.model.select import select_sure_picks
from backend.result_checker import grade_selection

logger = logging.getLogger("backtest")

BASE = "https://football-data.co.uk/mmz4281/{season}/{div}.csv"
CACHE = Path(__file__).resolve().parent.parent / "data" / "football-data"

# football-data.co.uk division code -> football-data.org competition code
DIVISIONS = {
    "E0": ("PL", "Premier League"),
    "SP1": ("PD", "La Liga"),
    "D1": ("BL1", "Bundesliga"),
    "I1": ("SA", "Serie A"),
    "F1": ("FL1", "Ligue 1"),
}

DEFAULT_SEASONS = ["2223", "2324", "2425", "2526"]


@dataclass
class Row:
    """One historical fixture: the result, the prices, and the model inputs."""

    match: MatchResult
    open_odds: dict[str, float]
    close_odds: dict[str, float]
    season_start: int


def _num(value: str | None) -> float | None:
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return f if f > 1.0 else None


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _team_id(competition: str, name: str) -> int:
    """Stable synthetic id — the CSVs have names, not ids."""
    return zlib.crc32(f"{competition}:{name.strip().lower()}".encode()) & 0x7FFFFFFF


def download(season: str, div: str, offline: bool = False) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{season}_{div}.csv"
    if path.exists() and path.stat().st_size > 1000:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    if offline:
        logger.warning("No cached file for %s %s and --offline set.", season, div)
        return None
    url = BASE.format(season=season, div=div)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8-sig", errors="replace")
    except Exception as exc:
        logger.warning("Download failed for %s: %s", url, exc)
        return None
    path.write_text(raw, encoding="utf-8")
    return raw


def load_season(season: str, div: str, offline: bool = False) -> list[Row]:
    raw = download(season, div, offline=offline)
    if not raw:
        return []
    code, lemma = DIVISIONS[div]
    season_start = 2000 + int(season[:2])
    rows: list[Row] = []
    for record in csv.DictReader(io.StringIO(raw)):
        record = {(k or "").strip().lstrip("\ufeff"): v for k, v in record.items()}
        played = _parse_date(record.get("Date") or "")
        home = (record.get("HomeTeam") or "").strip()
        away = (record.get("AwayTeam") or "").strip()
        try:
            hg = int(record.get("FTHG"))
            ag = int(record.get("FTAG"))
        except (TypeError, ValueError):
            continue
        if not played or not home or not away:
            continue

        open_odds = {
            "home_win": _num(record.get("B365H")),
            "draw": _num(record.get("B365D")),
            "away_win": _num(record.get("B365A")),
            "over25": _num(record.get("B365>2.5")),
        }
        close_odds = {
            "home_win": _num(record.get("B365CH")) or open_odds["home_win"],
            "draw": _num(record.get("B365CD")) or open_odds["draw"],
            "away_win": _num(record.get("B365CA")) or open_odds["away_win"],
            "over25": _num(record.get("B365C>2.5")) or open_odds["over25"],
        }
        rows.append(
            Row(
                match=MatchResult(
                    home_id=_team_id(code, home),
                    away_id=_team_id(code, away),
                    home_goals=hg,
                    away_goals=ag,
                    played_on=played,
                    competition=code,
                    home_name=home,
                    away_name=away,
                    season=season_start,
                ),
                open_odds={k: v for k, v in open_odds.items() if v},
                close_odds={k: v for k, v in close_odds.items() if v},
                season_start=season_start,
            )
        )
    return rows


@dataclass
class Bet:
    season: str
    league: str
    played_on: date
    market: str
    odds: float
    close_odds: float | None
    model_p: float
    adj_p: float
    outcome: str
    profit: float


def _settle(market: str, odds: float, outcome: str) -> float:
    if outcome == "win":
        return odds - 1.0
    if outcome == "loss":
        return -1.0
    return 0.0


def run(
    seasons: list[str],
    divs: list[str],
    weights: list[float],
    max_selections: int = 3,
    offline: bool = False,
) -> dict[float, list[Bet]]:
    by_weight: dict[float, list[Bet]] = {w: [] for w in weights}
    calibration: list[tuple[float, int]] = []
    baseline: list[Row] = []

    for season in seasons:
        for div in divs:
            test_rows = load_season(season, div, offline=offline)
            if not test_rows:
                continue
            code = DIVISIONS[div][0]
            test_start = test_rows[0].season_start

            # Practice like production: only this season and the one before.
            pool = [
                r
                for r in test_rows
            ] + load_season(f"{str(test_start - 1)[2:]}{str(test_start)[2:]}", div, offline=offline)
            history = [r.match for r in pool]
            by_date: dict[date, list[Row]] = defaultdict(list)
            for r in test_rows:
                by_date[r.match.played_on].append(r)
                if "over25" in r.open_odds:
                    baseline.append(r)

            for matchday in sorted(by_date):
                model = build_league_model(history, code, matchday)
                if model is None:
                    continue
                priced = []
                for r in by_date[matchday]:
                    pred = predict_match(
                        model, r.match.home_id, r.match.away_id, r.match.home_name, r.match.away_name
                    )
                    if not pred:
                        continue
                    # Calibration on every priced match, gates or not.
                    if "home_win" in r.close_odds:
                        calibration.append((pred["probs"]["home_win"], r.match.home_goals > r.match.away_goals))
                        calibration.append((pred["probs"]["draw"], r.match.home_goals == r.match.away_goals))
                        calibration.append((pred["probs"]["away_win"], r.match.away_goals > r.match.home_goals))
                    priced.append(
                        {
                            "fixture_id": r.match.home_id * 100003 + r.match.away_id + matchday.toordinal(),
                            "home_team": r.match.home_name,
                            "away_team": r.match.away_name,
                            "league": DIVISIONS[div][1],
                            "competition_code": code,
                            "lambda_home": pred["lambda_home"],
                            "lambda_away": pred["lambda_away"],
                            "home_games": pred["home_games"],
                            "away_games": pred["away_games"],
                            "weather": {"wind_kmh": 0},
                            "odds_snapshot": r.open_odds,
                            "probs": pred["probs"],
                            "_row": r,
                        }
                    )

                if not priced:
                    continue

                for weight in weights:
                    portfolio = select_sure_picks(priced, max_selections=max_selections, model_weight=weight)
                    for sel in portfolio.get("selections", []):
                        row: Row = next(p for p in priced if p["fixture_id"] == sel["fixture_id"])["_row"]
                        outcome = grade_selection(sel["market"], row.match.home_goals, row.match.away_goals)
                        by_weight[weight].append(
                            Bet(
                                season=season,
                                league=code,
                                played_on=matchday,
                                market=sel["market"],
                                odds=float(sel["odds"]),
                                close_odds=row.close_odds.get(sel["market"]),
                                model_p=float(sel["model_p"]),
                                adj_p=float(sel["adj_p"]),
                                outcome=outcome,
                                profit=_settle(sel["market"], float(sel["odds"]), outcome),
                            )
                        )

            logger.info(
                "  %s %s done (%s matchdays, %s bets so far at w=%.2f)",
                season,
                div,
                len(by_date),
                len(by_weight[weights[0]]),
                weights[0],
            )

    return {
        "bets": by_weight,
        "calibration": calibration,
        "baseline": baseline,
    }  # type: ignore[return-value]


def market_baseline(rows: list[Row]) -> dict[str, dict]:
    """What you get by ignoring the model and flat-betting the price.

    This is the number any "edge" has to beat. If the model's ROI is the same
    as blind betting, the model contributed nothing and the loss is the vig.
    """
    out: dict[str, dict] = {}
    for market in ("home_win", "away_win", "over25"):
        staked = profit = 0.0
        for r in rows:
            odds = r.open_odds.get(market)
            if not odds:
                continue
            outcome = grade_selection(market, r.match.home_goals, r.match.away_goals)
            if outcome not in {"win", "loss"}:
                continue
            staked += 1.0
            profit += (odds - 1.0) if outcome == "win" else -1.0
        out[market] = {"bets": int(staked), "roi": (profit / staked * 100) if staked else 0.0}

    # Back the shorter-priced side of every 1X2 market.
    staked = profit = 0.0
    for r in rows:
        prices = {
            "home_win": r.open_odds.get("home_win"),
            "away_win": r.open_odds.get("away_win"),
        }
        prices = {k: v for k, v in prices.items() if v}
        if len(prices) < 2:
            continue
        market = min(prices, key=lambda k: prices[k])
        odds = prices[market]
        outcome = grade_selection(market, r.match.home_goals, r.match.away_goals)
        if outcome not in {"win", "loss"}:
            continue
        staked += 1.0
        profit += (odds - 1.0) if outcome == "win" else -1.0
    out["1x2_favourite"] = {"bets": int(staked), "roi": (profit / staked * 100) if staked else 0.0}
    return out


def summarise(bets: list[Bet]) -> dict:
    settled = [b for b in bets if b.outcome in {"win", "loss"}]
    wins = sum(1 for b in settled if b.outcome == "win")
    staked = len(settled)
    profit = sum(b.profit for b in bets)
    clvs = [
        b.odds / b.close_odds - 1.0
        for b in bets
        if b.close_odds and b.close_odds > 1.0
    ]
    return {
        "bets": len(bets),
        "settled": staked,
        "wins": wins,
        "hit_rate": (wins / staked * 100) if staked else 0.0,
        "profit": profit,
        "roi": (profit / staked * 100) if staked else 0.0,
        "avg_odds": (sum(b.odds for b in settled) / staked) if staked else 0.0,
        "avg_clv_pct": (sum(clvs) / len(clvs) * 100) if clvs else None,
        "positive_clv_share": (sum(1 for c in clvs if c > 0) / len(clvs) * 100) if clvs else None,
    }


def calibration_table(pairs: list[tuple[float, int]], buckets: int = 5) -> list[str]:
    if not pairs:
        return ["  no data"]
    edges = [i / buckets for i in range(buckets + 1)]
    buckets_data: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, hit in pairs:
        idx = min(int(p * buckets), buckets - 1)
        buckets_data[idx].append((p, hit))
    lines = []
    for idx in range(buckets):
        chunk = buckets_data.get(idx, [])
        if not chunk:
            continue
        mean_p = sum(p for p, _ in chunk) / len(chunk) * 100
        realised = sum(h for _, h in chunk) / len(chunk) * 100
        lines.append(
            f"  {edges[idx]*100:3.0f}–{edges[idx+1]*100:3.0f}%  n={len(chunk):5d}  "
            f"predicted {mean_p:5.1f}%  realised {realised:5.1f}%  "
            f"delta {realised - mean_p:+5.1f}"
        )
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default=",".join(DEFAULT_SEASONS))
    ap.add_argument("--divs", default=",".join(DIVISIONS))
    ap.add_argument("--weights", default="1.0,0.8,0.6,0.4")
    ap.add_argument("--max-selections", type=int, default=3)
    ap.add_argument("--offline", action="store_true", help="use cached CSVs only")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    divs = [d.strip() for d in args.divs.split(",") if d.strip()]
    weights = [float(w) for w in args.weights.split(",") if w.strip()]

    print(f"Backtest: seasons {seasons}, divisions {divs}, max {args.max_selections} singles/day")
    result = run(seasons, divs, weights, args.max_selections, args.offline)
    by_weight = result["bets"]

    print("\nMODEL_WEIGHT sweep (1.0 = raw Poisson, lower = shrunk toward the market)")
    print(f"  {'weight':>6}  {'bets':>5}  {'hit%':>5}  {'avg odds':>8}  {'ROI':>7}  {'avg CLV':>8}  {'+CLV%':>6}")
    for w in weights:
        s = summarise(by_weight[w])
        clv = f"{s['avg_clv_pct']:+.2f}%" if s["avg_clv_pct"] is not None else "n/a"
        pos = f"{s['positive_clv_share']:.0f}%" if s["positive_clv_share"] is not None else "n/a"
        print(
            f"  {w:6.2f}  {s['bets']:5d}  {s['hit_rate']:5.1f}  {s['avg_odds']:8.2f}  "
            f"{s['roi']:+6.2f}%  {clv:>8}  {pos:>6}"
        )

    from backend.config import MODEL_WEIGHT as SHIPPED_WEIGHT

    focus = SHIPPED_WEIGHT if SHIPPED_WEIGHT in by_weight else weights[0]
    print(f"\nPer market at MODEL_WEIGHT={focus:.2f}")
    markets = sorted({b.market for b in by_weight[focus]})
    print(f"  {'market':<12} {'bets':>5}  {'hit%':>5}  {'ROI':>7}  {'avg CLV':>8}")
    for market in markets:
        subset = [b for b in by_weight[focus] if b.market == market]
        s = summarise(subset)
        clv = f"{s['avg_clv_pct']:+.2f}%" if s["avg_clv_pct"] is not None else "n/a"
        print(f"  {market:<12} {s['bets']:5d}  {s['hit_rate']:5.1f}  {s['roi']:+6.2f}%  {clv:>8}")

    print("\nPer season at MODEL_WEIGHT=%.2f" % focus)
    for season in seasons:
        subset = [b for b in by_weight[focus] if b.season == season]
        if not subset:
            continue
        s = summarise(subset)
        print(f"  {season}  bets={s['bets']:5d}  hit={s['hit_rate']:5.1f}%  ROI={s['roi']:+6.2f}%")

    print("\nNo-model baseline — flat-staking the price, no gates")
    print(f"  {'market':<14} {'bets':>6}  {'ROI':>7}")
    for market, stats in market_baseline(result["baseline"]).items():  # type: ignore[arg-type]
        print(f"  {market:<14} {stats['bets']:6d}  {stats['roi']:+6.2f}%")

    print("\n1X2 calibration (all priced matches, no gates) — is the model honest?")
    for line in calibration_table(result["calibration"]):  # type: ignore[arg-type]
        print(line)

    losing = [w for w in weights if summarise(by_weight[w])["roi"] < 0]
    if len(losing) == len(weights):
        print(
            "\nVERDICT: every MODEL_WEIGHT setting lost money out of sample. "
            "No positive-EV configuration of these gates was found — publish nothing, "
            "or publish with the loss treated as the cost of a public record."
        )

    print("\nNOTE: BTTS is not in the football-data.co.uk files, so it is unvalidated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
