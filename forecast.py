"""Forecast a quarter four ways, back-test them on past quarters, and forecast the current one.

Deals whose close date has already passed are left out of every method and reported on their own.

  Commit       deals in Contract Review (what reps would call)
  Best case    Commit plus Proposal Sent and Negotiation
  Weighted     every deal times a textbook stage weight
  Capacity     effective AE headcount times recent bookings per ramped AE (top-down, ignores pipeline)
  Model        every deal times the win rate its stage and segment have earned so far,
               cut for deals that slip past the quarter, plus the deals that get created
               and won inside the quarter (which no pipeline snapshot can see)
"""

from __future__ import annotations

import argparse

import pandas as pd

from capacity import capacity_forecast
from common import AS_OF, CATEGORY, DEFAULT_DATA, ROOT, STAGE_WEIGHT, load, pipeline_as_of, quarter_bounds

METHODS = ["commit", "best_case", "weighted", "model", "capacity"]


def stage_win_rates(t: dict, day: pd.Timestamp) -> tuple[pd.DataFrame, float]:
    """From deals that closed before `day`: the win rate of deals that reached each stage, by
    segment. Uses only what was known on `day`, so the back-test doesn't peek at the future."""
    o, h = t["opps"], t["history"]
    done = o[o.is_closed & (o.close_date < day)]
    reached = h[h.opportunity_id.isin(done.opportunity_id) & h.stage.isin(CATEGORY)]
    reached = reached.merge(done[["opportunity_id", "is_won", "segment"]], on="opportunity_id")
    rates = reached.groupby(["segment", "stage"]).is_won.agg(["mean", "size"]).rename(columns={"mean": "win_rate"})
    overall = reached.groupby("stage").is_won.mean()
    return rates, overall


def on_time_rate(t: dict, day: pd.Timestamp) -> float:
    """Of the deals won before `day`, the share that closed by the end of the quarter first forecast."""
    o = t["opps"]
    won = o[o.is_won & (o.close_date < day)]
    if won.empty:
        return 1.0
    return float((won.close_date.dt.to_period("Q") <= won.original_close_date.dt.to_period("Q")).mean())


def created_and_won(t: dict, q: pd.Period) -> float:
    """Dollars won in quarter q from deals that were also created in q."""
    o = t["opps"]
    w = o[o.is_won]
    return float(w.loc[(w.close_date.dt.to_period("Q") == q) & (w.created_date.dt.to_period("Q") == q), "amount"].sum())


def forecast(t: dict, q: pd.Period, day: pd.Timestamp, use_original_close: bool) -> dict:
    start, end = quarter_bounds(q)
    pipe = pipeline_as_of(t, day, use_original_close)
    # A deal whose close date has already passed can't be placed in any quarter until the rep
    # re-dates it, so every method leaves it out and it's reported on its own.
    past_due = pipe[pipe.expected_close < start]
    inq = pipe[(pipe.expected_close >= start) & (pipe.expected_close <= end)]
    cat = inq.stage_then.map(CATEGORY)
    o = t["opps"]
    won_so_far = float(o.loc[o.is_won & (o.close_date >= start) & (o.close_date <= day), "amount"].sum())

    rates, overall = stage_win_rates(t, day)
    def p_win(r):
        key = (r.segment, r.stage_then)
        if key in rates.index and rates.at[key, "size"] >= 20:
            return rates.at[key, "win_rate"]
        return overall.get(r.stage_then, STAGE_WEIGHT[r.stage_then])
    p = inq.apply(p_win, axis=1) if len(inq) else pd.Series(dtype=float)

    # Deals that don't exist yet: use the last two full quarters' created-and-won dollars,
    # scaled to the part of the quarter that's left.
    prior = [q - 1, q - 2]
    runrate = sum(created_and_won(t, x) for x in prior) / len(prior)
    left = max(0.0, (end - day).days + 1) / ((end - start).days + 1)

    return {
        "quarter": str(q), "as_of": day.date().isoformat(), "won_so_far": won_so_far,
        "deals_in_quarter": int(len(inq)), "pipeline_in_quarter": float(inq.amount.sum()),
        "past_due_deals": int(len(past_due)), "past_due_amount": float(past_due.amount.sum()),
        "commit": won_so_far + float(inq.amount[cat == "Commit"].sum()),
        "best_case": won_so_far + float(inq.amount[cat.isin(["Commit", "Best Case"])].sum()),
        "weighted": won_so_far + float((inq.amount * inq.stage_then.map(STAGE_WEIGHT)).sum()),
        "model": won_so_far + float((inq.amount * p).sum()) * on_time_rate(t, day) + runrate * left,
        "model_new_deals": runrate * left,
        "capacity": capacity_forecast(t, q, day),
    }


def actual(t: dict, q: pd.Period) -> float:
    o = t["opps"]
    return float(o.loc[o.is_won & (o.close_date.dt.to_period("Q") == q), "amount"].sum())


def backtest(t: dict, quarters: list[pd.Period]) -> pd.DataFrame:
    rows = []
    for q in quarters:
        f = forecast(t, q, quarter_bounds(q)[0], use_original_close=True)
        f["actual"] = actual(t, q)
        for m in METHODS:
            f[f"{m}_error"] = (f[m] - f["actual"]) / f["actual"]
        rows.append(f)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    args = ap.parse_args()
    t = load(args.data_dir)
    cur = AS_OF.to_period("Q")
    bt = backtest(t, list(pd.period_range("2025Q3", cur - 1, freq="Q")))
    now = pd.DataFrame([forecast(t, cur, AS_OF, use_original_close=False)])
    (ROOT / "results").mkdir(exist_ok=True)
    bt.to_csv(ROOT / "results" / "backtest.csv", index=False)
    now.to_csv(ROOT / "results" / "current_forecast.csv", index=False)

    money = lambda v: f"${v / 1e6:,.2f}M"
    print("Back-test: forecast at the start of each quarter vs what actually closed\n")
    show = bt[["quarter", "actual"] + METHODS].copy()
    for c in ["actual"] + METHODS:
        show[c] = show[c].map(money)
    print(show.to_string(index=False))
    print("\nAverage miss (absolute % error):")
    for m in METHODS:
        print(f"  {m:<10} {bt[f'{m}_error'].abs().mean():6.1%}   bias {bt[f'{m}_error'].mean():+6.1%}")
    n = now.iloc[0]
    print(f"\n{n.quarter} as of {n.as_of}: won so far {money(n.won_so_far)}")
    for m in METHODS:
        print(f"  {m:<10} {money(n[m])}")


if __name__ == "__main__":
    main()
