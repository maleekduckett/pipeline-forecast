"""Small checks on the forecast rules. Run with: python tests/test_rules.py"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capacity import effective_headcount, ramp_factor  # noqa: E402
from common import pipeline_as_of, quarter_bounds  # noqa: E402
from forecast import forecast, stage_win_rates  # noqa: E402
from health import status  # noqa: E402

D = pd.Timestamp


def toy():
    """Three deals: one still open, one won in Q2, one lost in Q1."""
    opps = pd.DataFrame({
        "opportunity_id": ["O-1", "O-2", "O-3"],
        "segment": ["SMB"] * 3,
        "amount": [100.0, 50.0, 30.0],
        "created_date": [D("2026-01-05"), D("2026-01-10"), D("2026-01-02")],
        "close_date": [D("2026-05-20"), D("2026-04-15"), D("2026-02-01")],
        "original_close_date": [D("2026-05-01"), D("2026-04-15"), D("2026-02-01")],
        "is_closed": [False, True, True],
        "is_won": [False, True, False],
    })
    history = pd.DataFrame({
        "opportunity_id": ["O-1", "O-1", "O-2", "O-2", "O-2", "O-3", "O-3"],
        "stage": ["Prospecting", "Negotiation", "Prospecting", "Contract Review", "Closed Won",
                  "Prospecting", "Closed Lost"],
        "entered_date": [D("2026-01-05"), D("2026-03-01"), D("2026-01-10"), D("2026-03-10"), D("2026-04-15"),
                         D("2026-01-02"), D("2026-02-01")],
    })
    users = pd.DataFrame({"user_id": ["U-1"], "role": ["Account Executive"], "quota_annual": [400.0],
                          "hire_date": [D("2024-01-01")], "termination_date": [pd.NaT]})
    return {"opps": opps, "history": history, "users": users}


def test_stage_on_a_past_day():
    p = pipeline_as_of(toy(), D("2026-02-15"), use_original_close=True).set_index("opportunity_id")
    assert p.at["O-1", "stage_then"] == "Prospecting"     # reached Negotiation later
    assert "O-3" not in p.index                           # already lost by then


def test_won_deal_is_open_until_it_closes():
    p = pipeline_as_of(toy(), D("2026-04-01"), use_original_close=True)
    assert set(p.opportunity_id) == {"O-1", "O-2"}


def test_win_rates_only_use_the_past():
    rates, overall = stage_win_rates(toy(), D("2026-03-01"))
    assert overall["Prospecting"] == 0.0     # only the lost deal had closed by then


def test_past_due_deals_are_left_out():
    t = toy()
    f = forecast(t, pd.Period("2026Q3"), D("2026-07-01"), use_original_close=False)
    assert f["deals_in_quarter"] == 0 and f["past_due_deals"] == 1


def test_ramp():
    hire = D("2026-01-15")
    assert ramp_factor("Account Executive", hire, D("2026-01-20")) == 0.25   # first month
    assert ramp_factor("Account Executive", hire, D("2026-03-20")) == 0.75   # third month
    assert ramp_factor("Account Executive", hire, D("2026-06-01")) == 1.0    # fully ramped
    assert ramp_factor("Account Executive", hire, D("2026-01-01")) == 0.0    # not hired yet


def test_departure_counts_for_part_of_the_quarter():
    users = pd.DataFrame({"user_id": ["U-1", "U-2"], "role": ["Account Executive"] * 2,
                          "hire_date": [D("2020-01-01")] * 2, "termination_date": [pd.NaT, D("2026-08-15")]})
    full = effective_headcount(users, "Account Executive", pd.Period("2026Q3"))
    assert 1.4 < full < 1.6                                  # one full rep plus about half of one
    before = effective_headcount(users, "Account Executive", pd.Period("2026Q3"), known_on=D("2026-07-01"))
    assert before == 2.0                                     # on July 1 nobody knew she'd leave


def test_quarter_bounds():
    assert quarter_bounds(pd.Period("2026Q4")) == (D("2026-10-01"), D("2026-12-31"))


def test_status_direction():
    assert status(3.5, "coverage", True) == "green"
    assert status(1.5, "coverage", True) == "red"
    assert status(0.40, "past_due", False) == "red"
    assert status(0.01, "past_due", False) == "green"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} tests passed")
