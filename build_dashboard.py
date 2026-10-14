"""Build the dashboard from the forecast, health and SQL results.

Writes two copies of the same page:
  docs/index.html     a full HTML file (opens in a browser, works on GitHub Pages)
  dashboard/page.html the page body only, for publishing as a hosted page
Run forecast.py and health.py first."""

from __future__ import annotations

import argparse
import json

import pandas as pd

from common import AS_OF, DEFAULT_DATA, ROOT, STAGE_WEIGHT, load, quarter_bounds
from report import run_queries


def build_data(data_dir=DEFAULT_DATA) -> dict:
    t = load(data_dir)
    q = run_queries(data_dir)
    res = ROOT / "results"
    users = t["users"].set_index("user_id").name
    start, _ = quarter_bounds(AS_OF.to_period("Q"))

    funnel = q["stage_funnel"].assign(weight=lambda d: d.stage.map(STAGE_WEIGHT))
    reps = pd.read_csv(res / "rep_coverage.csv")
    reps["coverage"] = reps.coverage.where(reps.coverage.notna(), None)
    stuck = pd.read_csv(res / "stuck_deals.csv", parse_dates=["close_date"])
    stuck = stuck.sort_values("amount", ascending=False).head(15)
    stuck["owner"] = stuck.owner_id.map(users)
    stuck["past_due"] = stuck.close_date < start
    stuck["close_date"] = stuck.close_date.dt.strftime("%Y-%m-%d")
    stuck["limit"] = stuck["limit"].round().astype(int)

    def records(df):
        return json.loads(df.to_json(orient="records"))

    return {
        "asOf": AS_OF.strftime("%Y-%m-%d"),
        "daysIn": int((AS_OF - start).days) + 1,
        "context": json.loads((res / "context.json").read_text()),
        "current": records(pd.read_csv(res / "current_forecast.csv"))[0],
        "backtest": records(pd.read_csv(res / "backtest.csv")),
        "signals": records(pd.read_csv(res / "signals.csv")),
        "funnel": records(funnel),
        "openByStage": records(q["open_by_stage"]),
        "reps": records(reps[["name", "segment", "won", "pipeline", "coverage"]]),
        "stuck": records(stuck[["opportunity_id", "name", "owner", "stage", "days", "limit", "amount",
                                "close_date", "past_due"]]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    args = ap.parse_args()
    data = build_data(args.data_dir)
    page = (ROOT / "dashboard" / "template.html").read_text()
    page = page.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    (ROOT / "dashboard" / "page.html").write_text(page)
    full = ('<!doctype html>\n<html lang="en" data-theme="dark">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + page.replace('<div class="wrap">', '</head>\n<body>\n<div class="wrap">', 1)
            + "\n</body>\n</html>\n")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(full)
    print(f"Wrote docs/index.html and dashboard/page.html ({len(page) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
