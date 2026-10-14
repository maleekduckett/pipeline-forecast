"""Shared settings, loaders and the pipeline-as-of-a-date rebuild every script uses."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT.parent / "gtm-synthetic-data" / "data"

AS_OF = pd.Timestamp("2026-10-13")      # the day the dataset was generated
OPEN_STAGES = ["Prospecting", "Discovery", "Proposal Sent", "Negotiation", "Contract Review"]

# Forecast category a rep would put a deal in, by stage. Matches the CRM's own field.
CATEGORY = {"Prospecting": "Pipeline", "Discovery": "Pipeline", "Proposal Sent": "Best Case",
            "Negotiation": "Best Case", "Contract Review": "Commit"}

# Textbook stage weights for a weighted pipeline. Picked before looking at any results.
STAGE_WEIGHT = {"Prospecting": .10, "Discovery": .20, "Proposal Sent": .40,
                "Negotiation": .60, "Contract Review": .80}

# Health signal thresholds: (amber at, red at). Direction is set per signal in health.py.
COVERAGE_TARGET = 3.0          # pipeline should be 3x what's left to close
THRESHOLDS = {
    "coverage": (3.0, 2.0),            # below these is amber / red
    "growth_coverage": (3.0, 2.0),
    "next_q_coverage": (2.0, 1.0),
    "orphaned": (0.0, 0.05),           # any open $ owned by someone who left is a problem
    "creation": (0.9, 0.7),            # last 90 days vs the 90 before
    "aging": (0.20, 0.35),             # share of open pipeline $ stuck in stage
    "past_due": (0.05, 0.15),          # share of open $ with a close date already passed
    "slipped": (0.25, 0.40),           # share of open deals pushed at least once
    "concentration": (0.35, 0.50),     # top 5 deals' share of this quarter's pipeline
    "win_rate": (-0.03, -0.07),        # change in win rate, last 2 quarters vs the 2 before
    "capacity": (-0.05, -0.15),        # change in effective AEs vs the same quarter last year
    "lead_conversion": (-0.01, -0.03), # change in lead conversion, last 2 quarters vs the 2 before
}


def load(data_dir: Path | str = DEFAULT_DATA) -> dict[str, pd.DataFrame]:
    d = Path(data_dir)
    o = pd.read_csv(d / "opportunities.csv", parse_dates=["created_date", "close_date", "original_close_date"])
    o["amount"] = o.amount.fillna(0)
    o["segment"] = o.account_id.map(pd.read_csv(d / "accounts.csv").set_index("account_id").segment)
    h = pd.read_csv(d / "opportunity_stage_history.csv", parse_dates=["entered_date"])
    u = pd.read_csv(d / "users.csv", parse_dates=["hire_date", "termination_date"])
    leads = pd.read_csv(d / "leads.csv", parse_dates=["created_date", "converted_date"])
    return {"opps": o, "history": h, "users": u, "leads": leads}


def quarter_bounds(q: pd.Period) -> tuple[pd.Timestamp, pd.Timestamp]:
    return q.start_time.normalize(), q.end_time.normalize()


def pipeline_as_of(t: dict[str, pd.DataFrame], day: pd.Timestamp, use_original_close: bool) -> pd.DataFrame:
    """Every deal that was open on `day`, with the stage it was in that day.

    For past dates we don't know what close date the rep had entered on that day, only the first
    one (original_close_date) and the final one. Back-tests use the original; today's forecast
    uses the current close date."""
    o, h = t["opps"], t["history"]
    opened = o[o.created_date <= day]
    still_open = opened[~opened.is_closed | (opened.close_date > day)]
    stage = (h[h.entered_date <= day].sort_values("entered_date")
             .groupby("opportunity_id").stage.last())
    p = still_open.assign(stage_then=still_open.opportunity_id.map(stage))
    p = p[p.stage_then.isin(OPEN_STAGES)].copy()
    p["expected_close"] = p.original_close_date if use_original_close else p.close_date
    return p
