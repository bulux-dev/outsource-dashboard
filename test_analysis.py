"""Checks the monthly counts and the Approved / total conversion rate."""

from __future__ import annotations

import pandas as pd

from volume import (
    apply_status,
    agent_daily,
    breakdown_by,
    build_daily,
    build_monthly,
    canonical_status,
    default_classification,
    demo_items,
    localize_events,
    mapping_from_table,
    month_to_date,
    period_kpis,
    team_daily,
)


def test_status_names() -> None:
    assert canonical_status(" APPROVED ") == "Approved"
    assert canonical_status("Active Deals") == "Active deals"
    assert canonical_status("aprobado") == "Approved"
    assert canonical_status("corrección necesaria") == "Correction needed"
    assert canonical_status("Missing info") == "Missing information"
    assert canonical_status("") == "No status"
    assert canonical_status("On hold") is None


def test_conversion_and_gaps() -> None:
    frame = pd.DataFrame(
        {
            "event_date": pd.to_datetime(["2025-01-15", "2025-01-20", "2025-03-02"]),
            "status": ["Approved", "Rejected", "Active deals"],
        }
    )
    monthly = build_monthly(frame)
    assert list(monthly["Total"]) == [2, 0, 1]
    assert list(monthly["Cumulative"]) == [2, 2, 3]
    assert list(monthly["Approved"]) == [1, 0, 0]
    assert monthly.loc[0, "Conversion"] == 0.5
    assert pd.isna(monthly.loc[1, "Conversion"])
    assert monthly.loc[2, "Conversion"] == 0
    assert list(monthly["month"].dt.month) == [1, 2, 3]

    kpis = period_kpis(frame)
    assert kpis["total"] == 3
    assert kpis["approved"] == 1
    assert kpis["rate"] == 1 / 3


def test_timezone_boundary() -> None:
    raw = pd.DataFrame(
        {
            "item_id": ["a", "b"],
            "name": ["a", "b"],
            "group": ["g", "g"],
            "event_raw": ["2025-01-01T04:00:00Z", "2025-01-01"],
            "event_kind": ["timestamp", "date"],
            "status_raw": ["Approved", "Rejected"],
        }
    )
    local = localize_events(raw, "America/Mexico_City")
    assert local.loc[0, "event_date"] == pd.Timestamp("2024-12-31 22:00:00")
    assert local.loc[1, "event_date"] == pd.Timestamp("2025-01-01")


def test_mapping_merges_approved() -> None:
    source = pd.DataFrame({"status_raw": ["Won", "Won", "Approved", ""]})
    table = default_classification(source["status_raw"])
    table.loc[table["Label"] == "Won", "Counts as"] = "Approved"
    mapped = apply_status(source, mapping_from_table(table))
    assert list(mapped["status"]) == ["Approved", "Approved", "Approved", "No status"]
    assert period_kpis(mapped)["rate"] == 0.75


def test_daily_agent_and_team_views() -> None:
    frame = pd.DataFrame(
        {
            "event_date": pd.to_datetime(["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-28"]),
            "status": ["Approved", "Rejected", "Approved", "Approved"],
            "agent": ["Alex", "Alex", "Alex", "Blair"],
            "team": ["Training", "Training", "Training", "Samurais"],
        }
    )
    daily = build_daily(frame[frame["event_date"] >= "2026-09-01"])
    assert int(daily.loc[daily["day"] == "2026-09-01", "Total"].iloc[0]) == 1
    assert int(daily.loc[daily["day"] == "2026-09-03", "Total"].iloc[0]) == 0
    assert int(daily["Cumulative"].iloc[-1]) == 3
    assert pd.isna(daily.loc[daily["day"] == "2026-09-03", "Conversion"].iloc[0])

    current = month_to_date(frame, pd.Timestamp("2026-09-28").date())
    assert len(current) == 3
    agents = breakdown_by(current, "agent")
    assert list(agents["Agent"]) == ["Alex", "Blair"]
    assert int(agents.loc[agents["Agent"] == "Alex", "Total"].iloc[0]) == 2
    assert float(agents.loc[agents["Agent"] == "Alex", "Conversion"].iloc[0]) == 0.5
    teams = breakdown_by(current, "team")
    assert list(teams["Team"]) == ["Training", "Samurais"]
    by_day = team_daily(current)
    assert list(by_day["Team"]) == ["Training", "Training", "Samurais"]
    assert int(by_day["Total"].sum()) == 3
    by_agent = agent_daily(current)
    assert list(by_agent["Agent"]) == ["Alex", "Alex", "Blair"]
    assert int(by_agent.loc[by_agent["Agent"] == "Alex", "Total"].sum()) == 2
    assert float(by_agent.loc[by_agent["Agent"] == "Blair", "Conversion"].iloc[0]) == 1.0


def test_demo_adds_up() -> None:
    local = localize_events(demo_items(), "America/Mexico_City")
    table = default_classification(local["status_raw"])
    assert set(table["Counts as"]) <= set(
        ["Approved", "Active deals", "Rejected", "Correction needed", "Missing information"]
    )
    mapped = apply_status(local, mapping_from_table(table))
    monthly = build_monthly(mapped)
    kpis = period_kpis(mapped)
    assert kpis["total"] == 833
    assert int(monthly["Total"].sum()) == 833
    assert int(monthly["Cumulative"].iloc[-1]) == 833
    assert abs(kpis["rate"] - (kpis["approved"] / 833)) < 1e-12
    assert len(monthly) == 14


if __name__ == "__main__":
    test_status_names()
    test_conversion_and_gaps()
    test_timezone_boundary()
    test_mapping_merges_approved()
    test_daily_agent_and_team_views()
    test_demo_adds_up()
    print("ok")
