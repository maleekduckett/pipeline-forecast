"""Load the CRM tables into SQLite, run sql/report.sql, and print the summary."""

from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from common import AS_OF, DEFAULT_DATA, ROOT, load, quarter_bounds


def run_queries(data_dir=DEFAULT_DATA) -> dict[str, pd.DataFrame]:
    t = load(data_dir)
    con = sqlite3.connect(":memory:")
    o = t["opps"].copy()
    for c in ["created_date", "close_date", "original_close_date"]:
        o[c] = o[c].dt.strftime("%Y-%m-%d")
    o["is_closed"] = o.is_closed.astype(int)
    o["is_won"] = o.is_won.astype(int)
    o.to_sql("opps", con, index=False)
    h = t["history"].copy()
    h["entered_date"] = h.entered_date.dt.strftime("%Y-%m-%d")
    h.to_sql("history", con, index=False)
    start, end = quarter_bounds(AS_OF.to_period("Q"))
    params = {"q_start": start.strftime("%Y-%m-%d"), "q_end": end.strftime("%Y-%m-%d")}

    out, name, lines = {}, None, []
    for line in (ROOT / "sql" / "report.sql").read_text().splitlines() + ["-- name: end"]:
        if line.startswith("-- name:"):
            if name:
                sql = "\n".join(lines)
                out[name] = pd.read_sql_query(sql, con, params={k: v for k, v in params.items() if f":{k}" in sql})
            name, lines = line.split(":", 1)[1].strip(), []
        else:
            lines.append(line)
    out.pop("end", None)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    args = ap.parse_args()
    res = run_queries(args.data_dir)
    text = "\n".join(f"== {n}\n{(df.tail(24) if n == 'created_by_month' else df).to_string(index=False)}\n"
                     for n, df in res.items())
    (ROOT / "results" / "report.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
