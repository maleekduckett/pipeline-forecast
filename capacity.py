"""Effective headcount and rep productivity.

A new rep isn't a full rep. Effective headcount counts each person by how ramped they are and how
much of the quarter they were employed, so a rep hired halfway through their first month counts
for very little and a rep who left mid-quarter counts for part of it."""

from __future__ import annotations

import argparse

import pandas as pd

from common import AS_OF, DEFAULT_DATA, ROOT, load, quarter_bounds

# Share of a fully ramped rep, by month since hire (month 1, 2, 3, ...). Full after the list ends.
RAMP = {"Account Executive": [0.25, 0.50, 0.75], "SDR": [0.33, 0.67]}


def ramp_factor(role: str, hire: pd.Timestamp, day: pd.Timestamp) -> float:
    months = (day.year - hire.year) * 12 + (day.month - hire.month) - (day.day < hire.day)
    steps = RAMP[role]
    return steps[months] if 0 <= months < len(steps) else (1.0 if months >= len(steps) else 0.0)


def effective_headcount(users: pd.DataFrame, role: str, q: pd.Period, known_on: pd.Timestamp | None = None) -> float:
    """Average ramped headcount over the quarter's days.

    With `known_on`, only what was known that day counts: people hired by then, and nobody
    leaving early. That's what a forecast made on that day could have used."""
    start, end = quarter_bounds(q)
    days = pd.date_range(start, end, freq="D")
    total = 0.0
    for u in users[users.role == role].itertuples():
        if known_on is not None and u.hire_date > known_on:
            continue
        leave = u.termination_date
        if known_on is not None and pd.notna(leave) and leave > known_on:
            leave = pd.NaT
        for d in days[::7]:                      # weekly steps are plenty for an average
            if d < u.hire_date or (pd.notna(leave) and d > leave):
                continue
            total += ramp_factor(role, u.hire_date, d)
    return total / len(days[::7])


def bookings(o: pd.DataFrame, q: pd.Period) -> float:
    return float(o.loc[o.is_won & (o.close_date.dt.to_period("Q") == q), "amount"].sum())


def capacity_table(t: dict, quarters: list[pd.Period]) -> pd.DataFrame:
    o, u = t["opps"], t["users"]
    rows = []
    for q in quarters:
        ae = effective_headcount(u, "Account Executive", q)
        sdr = effective_headcount(u, "SDR", q)
        b = bookings(o, q)
        rows.append({"quarter": str(q), "effective_aes": round(ae, 2), "effective_sdrs": round(sdr, 2),
                     "bookings": b, "per_ae": b / ae if ae else 0.0})
    return pd.DataFrame(rows)


def capacity_forecast(t: dict, q: pd.Period, day: pd.Timestamp) -> float:
    """Effective AEs expected in the quarter (as known on `day`) times the average bookings per
    effective AE over the last two full quarters."""
    o, u = t["opps"], t["users"]
    prior = [q - 1, q - 2]
    per_ae = [bookings(o, p) / effective_headcount(u, "Account Executive", p) for p in prior]
    return effective_headcount(u, "Account Executive", q, known_on=day) * sum(per_ae) / len(per_ae)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    args = ap.parse_args()
    t = load(args.data_dir)
    cur = AS_OF.to_period("Q")
    table = capacity_table(t, list(pd.period_range("2025Q1", cur, freq="Q")))
    table.to_csv(ROOT / "results" / "capacity.csv", index=False)
    show = table.copy()
    show["bookings"] = show.bookings.map(lambda v: f"${v / 1e6:.2f}M")
    show["per_ae"] = show.per_ae.map(lambda v: f"${v / 1e3:,.0f}K")
    print(show.to_string(index=False))
    print(f"\n{cur} is partial: bookings so far, as of {AS_OF.date()}.")


if __name__ == "__main__":
    main()
