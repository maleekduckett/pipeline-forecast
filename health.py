"""Pipeline health signals as of the snapshot date, each with a status and a plain-English line."""

from __future__ import annotations

import argparse

import pandas as pd

from common import AS_OF, DEFAULT_DATA, OPEN_STAGES, ROOT, THRESHOLDS, load, quarter_bounds


def status(value: float, key: str, higher_is_better: bool) -> str:
    amber, red = THRESHOLDS[key]
    if higher_is_better:
        return "red" if value < red else "amber" if value < amber else "green"
    return "red" if value > red else "amber" if value > amber else "green"


def quota_for(users: pd.DataFrame, day: pd.Timestamp) -> pd.Series:
    """Quarterly quota per AE still employed on `day`."""
    ae = users[(users.role == "Account Executive") & (users.termination_date.isna() | (users.termination_date > day))]
    return ae.set_index("user_id").quota_annual / 4


def time_in_stage(t: dict) -> pd.DataFrame:
    """Days each deal spent in each stage: from entering it to entering the next one."""
    h = t["history"].sort_values(["opportunity_id", "entered_date"]).copy()
    h["left"] = h.groupby("opportunity_id").entered_date.shift(-1)
    return h


def signals(t: dict, day: pd.Timestamp = AS_OF) -> tuple[pd.DataFrame, dict]:
    o, users = t["opps"], t["users"]
    q = day.to_period("Q")
    start, end = quarter_bounds(q)
    nstart, nend = quarter_bounds(q + 1)
    op = o[~o.is_closed]
    won_q = o.loc[o.is_won & (o.close_date >= start) & (o.close_date <= day), "amount"].sum()
    quota = quota_for(users, day)
    team_q = quota.sum()
    in_q = op[(op.close_date >= start) & (op.close_date <= end)]
    in_next = op[(op.close_date >= nstart) & (op.close_date <= nend)]
    past_due = op[op.close_date < start]
    last_q_actual = o.loc[o.is_won & (o.close_date.dt.to_period("Q") == q - 1), "amount"].sum()
    rows = []

    def add(key, name, value, shown, good_high, means):
        rows.append({"signal": key, "name": name, "value": value, "shown": shown,
                     "status": status(value, key, good_high), "means": means})

    remaining = max(team_q - won_q, 0)
    cov = in_q.amount.sum() / remaining if remaining else float("inf")
    add("coverage", "Coverage this quarter", cov, f"{cov:.1f}x",  True,
        f"${in_q.amount.sum() / 1e6:.1f}M of pipeline dated this quarter against ${remaining / 1e6:.1f}M of quota "
        f"still to close. The usual target is 3x.")
    gap = max(last_q_actual - won_q, 0)
    gcov = in_q.amount.sum() / gap if gap else float("inf")
    add("growth_coverage", "Coverage to match last quarter", gcov, f"{gcov:.1f}x", True,
        f"Matching last quarter's ${last_q_actual / 1e6:.1f}M means closing ${gap / 1e6:.1f}M more. "
        f"There's ${in_q.amount.sum() / 1e6:.1f}M dated this quarter to do it with.")
    ncov = in_next.amount.sum() / team_q
    add("next_q_coverage", "Coverage next quarter", ncov, f"{ncov:.1f}x", True,
        f"${in_next.amount.sum() / 1e6:.1f}M is dated for next quarter against ${team_q / 1e6:.1f}M of quota. "
        "Early in a quarter, 2x for the next one is healthy.")

    created = o.set_index("created_date").amount
    last90 = created[(created.index > day - pd.Timedelta(days=90)) & (created.index <= day)].sum()
    prev90 = created[(created.index > day - pd.Timedelta(days=180)) & (created.index <= day - pd.Timedelta(days=90))].sum()
    ratio = last90 / prev90 if prev90 else 0
    add("creation", "New pipeline created", ratio, f"{ratio - 1:+.0%}", True,
        f"${last90 / 1e6:.1f}M created in the last 90 days vs ${prev90 / 1e6:.1f}M in the 90 before. "
        "Pipeline built now is next quarter's bookings.")

    # Stuck: in its current stage longer than 3 in 4 winning deals of the same segment spent there.
    # Enterprise deals move slower than SMB, so each is judged against its own segment.
    h = time_in_stage(t).merge(o[["opportunity_id", "segment"]], on="opportunity_id")
    won_ids = set(o.loc[o.is_won, "opportunity_id"])
    done = h[h.opportunity_id.isin(won_ids) & h.left.notna() & h.stage.isin(OPEN_STAGES)]
    limit = (done.left - done.entered_date).dt.days.groupby([done.segment, done.stage]).quantile(.75)
    cur = h[h.opportunity_id.isin(op.opportunity_id)].groupby("opportunity_id").last()
    cur = cur.assign(days=(day - cur.entered_date).dt.days,
                     limit=[limit.get((sg, st)) for sg, st in zip(cur.segment, cur.stage)])
    stuck_ids = cur.index[cur.days > cur.limit]
    stuck = op[op.opportunity_id.isin(stuck_ids)]
    share = stuck.amount.sum() / op.amount.sum()
    add("aging", "Deals stuck in stage", share, f"{share:.0%}", False,
        f"{len(stuck)} open deals (${stuck.amount.sum() / 1e6:.1f}M) have sat in their stage longer than "
        "3 in 4 winning deals in the same segment did.")

    pd_share = past_due.amount.sum() / op.amount.sum()
    add("past_due", "Close date already passed", pd_share, f"{pd_share:.0%}", False,
        f"{len(past_due)} open deals (${past_due.amount.sum() / 1e6:.1f}M) have a close date before this quarter. "
        "They can't be forecast until a rep re-dates them.")

    gone = set(users.loc[users.termination_date.notna() & (users.termination_date <= day), "user_id"])
    orphan = op[op.owner_id.isin(gone)]
    oshare = orphan.amount.sum() / op.amount.sum()
    add("orphaned", "Owned by reps who left", oshare, f"{oshare:.0%}", False,
        (f"{len(orphan)} open deals (${orphan.amount.sum() / 1e6:.1f}M) belong to reps who have left. "
         "Nobody is working them.") if len(orphan) else
        "No open deals belong to the two reps who left. Their deals were reassigned.")

    pushed = op.close_date > op.original_close_date
    closed = o[o.is_closed]
    cp = closed.close_date > closed.original_close_date
    add("slipped", "Deals pushed at least once", pushed.mean(), f"{pushed.mean():.0%}", False,
        f"{pushed.sum()} of {len(op)} open deals have been pushed. Pushed deals have won "
        f"{closed[cp].is_won.mean():.0%} of the time vs {closed[~cp].is_won.mean():.0%} for the rest.")

    top5 = in_q.amount.nlargest(5).sum() / in_q.amount.sum() if len(in_q) else 0
    add("concentration", "Top 5 deals' share", top5, f"{top5:.0%}", False,
        f"The 5 biggest deals dated this quarter are {top5:.0%} of its pipeline. "
        "Above a third, one loss moves the number a lot.")

    c = closed.assign(cq=closed.close_date.dt.to_period("Q"))
    wr = c[c.cq < q].groupby("cq").is_won.mean()
    recent, before = wr.iloc[-2:].mean(), wr.iloc[-4:-2].mean()
    add("win_rate", "Win rate trend", recent - before, f"{(recent - before) * 100:+.1f} pts", True,
        f"{recent:.0%} of deals won in the last two quarters vs {before:.0%} in the two before.")

    context = {"quarter": str(q), "quota": float(team_q), "won_so_far": float(won_q),
               "pipeline_in_quarter": float(in_q.amount.sum()), "last_quarter_actual": float(last_q_actual),
               "open_pipeline": float(op.amount.sum()), "open_deals": int(len(op)),
               "past_due_amount": float(past_due.amount.sum()), "stage_limit_days": limit.round(0).to_dict()}
    stuck_list = cur.loc[stuck_ids, ["stage", "days", "limit"]].join(
        op.set_index("opportunity_id")[["name", "owner_id", "amount", "close_date", "segment"]])
    return pd.DataFrame(rows), {"context": context, "stuck": stuck_list.reset_index(), "quota": quota}


def rep_coverage(t: dict, quota: pd.Series, day: pd.Timestamp = AS_OF) -> pd.DataFrame:
    o, users = t["opps"], t["users"].set_index("user_id")
    q = day.to_period("Q")
    start, end = quarter_bounds(q)
    op = o[~o.is_closed & (o.close_date >= start) & (o.close_date <= end)]
    won = o[o.is_won & (o.close_date >= start) & (o.close_date <= day)]
    df = pd.DataFrame({"quota": quota})
    df["won"] = won.groupby("owner_id").amount.sum()
    df["pipeline"] = op.groupby("owner_id").amount.sum()
    df = df.fillna(0)
    df["remaining"] = (df.quota - df.won).clip(lower=0)
    df["coverage"] = df.pipeline / df.remaining.where(df.remaining > 0)
    df["name"] = users.loc[df.index, "name"]
    df["segment"] = users.loc[df.index, "segment"]
    return df.reset_index(names="owner_id").sort_values("coverage")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    args = ap.parse_args()
    t = load(args.data_dir)
    sig, extra = signals(t)
    reps = rep_coverage(t, extra["quota"])
    sig.to_csv(ROOT / "results" / "signals.csv", index=False)
    reps.to_csv(ROOT / "results" / "rep_coverage.csv", index=False)
    extra["stuck"].to_csv(ROOT / "results" / "stuck_deals.csv", index=False)
    pd.Series(extra["context"]).to_json(ROOT / "results" / "context.json", indent=2)
    for r in sig.itertuples():
        print(f"[{r.status:>5}] {r.name:<28} {r.shown:>8}  {r.means}")
    print()
    print(reps[["name", "segment", "quota", "won", "pipeline", "coverage"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
