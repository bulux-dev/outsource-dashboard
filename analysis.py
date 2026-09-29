"""Monthly volume and conversion rate for the dashboard."""

from __future__ import annotations

import random
import unicodedata

import pandas as pd

KNOWN_STATUSES = [
    "Approved",
    "Active deals",
    "Rejected",
    "Correction needed",
    "Missing information",
]

ALIASES = {
    "active deal": "Active deals",
    "deals activos": "Active deals",
    "deal activo": "Active deals",
    "rechazado": "Rejected",
    "rechazada": "Rejected",
    "aprobado": "Approved",
    "aprobada": "Approved",
    "corrections needed": "Correction needed",
    "needs correction": "Correction needed",
    "correccion necesaria": "Correction needed",
    "missing info": "Missing information",
    "informacion faltante": "Missing information",
    "falta informacion": "Missing information",
}

MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

EMPTY_LABEL = "(blank)"
NO_STATUS = "No status"
OTHER = "Other"
STATUS_OPTIONS = [*KNOWN_STATUSES, NO_STATUS, OTHER]
RAW_COLUMNS = ["item_id", "name", "group", "event_raw", "event_kind", "status_raw"]


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    cleaned = without_marks.lower().replace("-", " ").replace("_", " ").replace("/", " ")
    return " ".join(cleaned.split()).strip(".,;:")


def canonical_status(raw: object) -> str | None:
    """Known status, or 'No status'. None leaves the label as Other."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return NO_STATUS
    text = str(raw).strip()
    if not text:
        return NO_STATUS
    key = fold_text(text)
    known = {fold_text(status): status for status in KNOWN_STATUSES}
    if key in known:
        return known[key]
    return ALIASES.get(key)


def resolve_status(raw: str, choice: str) -> str:
    if choice == OTHER:
        return raw or NO_STATUS
    return choice


def default_classification(status_raw: pd.Series) -> pd.DataFrame:
    keys = status_raw.fillna("").map(lambda value: str(value).strip())
    counts = keys.value_counts()
    rows = []
    for raw, count in counts.items():
        rows.append(
            {
                "Label": raw if raw else EMPTY_LABEL,
                "Records": int(count),
                "Counts as": canonical_status(raw) or OTHER,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["Label", "Records", "Counts as"])
    return pd.DataFrame(rows)


def mapping_from_table(table: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for _, row in table.iterrows():
        label = str(row["Label"])
        raw = "" if label == EMPTY_LABEL else label
        mapping[raw] = str(row["Counts as"])
    return mapping


def apply_status(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = df.copy()
    keys = out["status_raw"].fillna("").map(lambda value: str(value).strip())
    out["status_key"] = keys
    out["status"] = keys.map(lambda raw: resolve_status(raw, mapping.get(raw, OTHER)))
    return out


def localize_events(df: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Convert the event time to a naive local timestamp for calendar months."""
    out = df.copy()
    if out.empty:
        out["event_date"] = pd.Series(dtype="datetime64[ns]")
        return out

    raw = out["event_raw"].fillna("").astype(str).str.strip()
    kind = out["event_kind"].astype(str)
    event = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")

    timestamp_rows = kind.eq("timestamp") & raw.ne("")
    date_rows = kind.eq("date") & raw.ne("")

    if timestamp_rows.any():
        timestamps = pd.to_datetime(raw[timestamp_rows], utc=True, errors="coerce")
        event.loc[timestamp_rows] = timestamps.dt.tz_convert(timezone).dt.tz_localize(None)

    if date_rows.any():
        dates = pd.to_datetime(raw[date_rows], errors="coerce")
        if getattr(dates.dt, "tz", None) is not None:
            dates = dates.dt.tz_convert(timezone).dt.tz_localize(None)
        event.loc[date_rows] = dates

    out["event_date"] = event
    return out


def ordered_statuses(present: set[str]) -> list[str]:
    columns = list(KNOWN_STATUSES)
    if NO_STATUS in present:
        columns.append(NO_STATUS)
    extras = sorted(present - set(columns))
    columns.extend(extras)
    return columns


def build_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """One row per month, with zeros for empty months and a running cumulative."""
    base_columns = ["month", *KNOWN_STATUSES, "Total", "Cumulative", "Conversion"]
    if df.empty or "event_date" not in df.columns:
        return pd.DataFrame(columns=base_columns)

    work = df.dropna(subset=["event_date"]).copy()
    if work.empty:
        return pd.DataFrame(columns=base_columns)

    work["month"] = work["event_date"].dt.to_period("M").dt.to_timestamp()
    return _period_counts(work, "month", "MS", base_columns)


def period_kpis(df: pd.DataFrame) -> dict:
    total = int(len(df))
    counts = {status: int((df["status"] == status).sum()) if total else 0 for status in KNOWN_STATUSES}
    known = set(KNOWN_STATUSES)
    extras = int((~df["status"].isin(known)).sum()) if total else 0
    approved = counts["Approved"]
    rate = approved / total if total else None
    return {
        "total": total,
        "approved": approved,
        "rate": rate,
        "counts": counts,
        "extras": extras,
    }


def build_daily(df: pd.DataFrame) -> pd.DataFrame:
    """One row per day, with zeros for empty days and a running cumulative."""
    base_columns = ["day", *KNOWN_STATUSES, "Total", "Cumulative", "Conversion"]
    if df.empty or "event_date" not in df.columns:
        return pd.DataFrame(columns=base_columns)

    work = df.dropna(subset=["event_date"]).copy()
    if work.empty:
        return pd.DataFrame(columns=base_columns)

    work["day"] = work["event_date"].dt.normalize()
    return _period_counts(work, "day", "D", base_columns)


def month_to_date(df: pd.DataFrame, as_of) -> pd.DataFrame:
    """Records from the first of as_of's month through as_of, inclusive."""
    if df.empty or "event_date" not in df.columns:
        return df.iloc[0:0].copy()
    end = pd.Timestamp(as_of).date()
    start = end.replace(day=1)
    dates = df["event_date"].dt.date
    return df.loc[(dates >= start) & (dates <= end)].copy()


def breakdown_by(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Status counts, total, and conversion for each agent or team."""
    label = "Agent" if column == "agent" else "Team"
    base_columns = [label, "Total", "Conversion", *KNOWN_STATUSES]
    if df.empty or column not in df.columns or "status" not in df.columns:
        return pd.DataFrame(columns=base_columns)

    work = df.copy()
    work[column] = work[column].fillna("").astype(str).str.strip().replace("", "(blank)")
    statuses = ordered_statuses(set(work["status"].dropna().astype(str)))
    counts = (
        work.groupby([column, "status"], observed=False)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=statuses, fill_value=0)
    )
    counts["Total"] = counts[statuses].sum(axis=1).astype(int)
    approved = counts["Approved"] if "Approved" in counts.columns else 0
    counts["Conversion"] = [
        None if int(total) == 0 else float(approved_count) / float(total)
        for approved_count, total in zip(approved, counts["Total"])
    ]
    result = counts.reset_index().rename(columns={column: label})
    result = result.sort_values(["Total", label], ascending=[False, True])
    ordered = [label, "Total", "Conversion", *statuses]
    return result[ordered].reset_index(drop=True)


def team_for_agents(df: pd.DataFrame) -> pd.Series:
    """Team names for each agent, joined when one person appears on more than one team."""
    if df.empty or "agent" not in df.columns:
        return pd.Series(dtype=str)
    work = df.copy()
    work["agent"] = work["agent"].fillna("").astype(str).str.strip().replace("", "(blank)")
    work["team"] = work["team"].fillna("").astype(str).str.strip() if "team" in work.columns else ""

    def joined(values: pd.Series) -> str:
        names = sorted({value for value in values if value})
        return ", ".join(names)

    return work.groupby("agent")["team"].agg(joined)


def team_daily(df: pd.DataFrame) -> pd.DataFrame:
    """One row per day and team."""
    base_columns = ["day", "Team", "Total", "Conversion", *KNOWN_STATUSES]
    if df.empty or "team" not in df.columns or "event_date" not in df.columns:
        return pd.DataFrame(columns=base_columns)

    work = df.dropna(subset=["event_date"]).copy()
    if work.empty:
        return pd.DataFrame(columns=base_columns)
    work["day"] = work["event_date"].dt.normalize()
    work["team"] = work["team"].fillna("").astype(str).str.strip().replace("", "(blank)")
    statuses = ordered_statuses(set(work["status"].dropna().astype(str)))
    counts = (
        work.groupby(["day", "team", "status"], observed=False)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=statuses, fill_value=0)
    )
    counts["Total"] = counts[statuses].sum(axis=1).astype(int)
    approved = counts["Approved"] if "Approved" in counts.columns else 0
    counts["Conversion"] = [
        None if int(total) == 0 else float(approved_count) / float(total)
        for approved_count, total in zip(approved, counts["Total"])
    ]
    result = counts.reset_index().rename(columns={"team": "Team"})
    result = result.sort_values(["day", "Team"])
    return result[["day", "Team", "Total", "Conversion", *statuses]].reset_index(drop=True)


def _period_counts(work: pd.DataFrame, column: str, freq: str, base_columns: list[str]) -> pd.DataFrame:
    statuses = ordered_statuses(set(work["status"].dropna().astype(str)))
    counts = (
        work.groupby([column, "status"], observed=False)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=statuses, fill_value=0)
        .sort_index()
    )
    full_index = pd.date_range(counts.index.min(), counts.index.max(), freq=freq)
    counts = counts.reindex(full_index, fill_value=0)
    counts.index.name = column
    for status in statuses:
        counts[status] = counts[status].astype(int)
    counts["Total"] = counts[statuses].sum(axis=1).astype(int)
    counts["Cumulative"] = counts["Total"].cumsum().astype(int)
    counts["Conversion"] = [
        None if int(total) == 0 else float(approved) / float(total)
        for approved, total in zip(counts["Approved"], counts["Total"])
    ]
    result = counts.reset_index()
    if result.empty:
        return pd.DataFrame(columns=base_columns)
    return result


def month_label(value: pd.Timestamp) -> str:
    stamp = pd.Timestamp(value)
    return f"{MONTHS[stamp.month - 1]} {stamp.year}"


def month_over_month(monthly: pd.DataFrame) -> dict | None:
    if len(monthly) < 2:
        return None
    last = monthly.iloc[-1]
    previous = monthly.iloc[-2]
    rate_delta = None
    if last["Conversion"] is not None and previous["Conversion"] is not None:
        if pd.notna(last["Conversion"]) and pd.notna(previous["Conversion"]):
            rate_delta = float(last["Conversion"]) - float(previous["Conversion"])
    return {
        "last_label": month_label(last["month"]),
        "prev_label": month_label(previous["month"]),
        "total_delta": int(last["Total"]) - int(previous["Total"]),
        "rate_delta": rate_delta,
    }


def demo_items() -> pd.DataFrame:
    """Fixed series for reviewing the dashboard without the spreadsheet."""
    rng = random.Random(29)
    weights = [0.28, 0.24, 0.18, 0.16, 0.14]
    people = [
        ("Alex Rivera", "Training"),
        ("Jordan Lee", "Samurais"),
        ("Casey Nguyen", "Training"),
    ]
    rows = []
    start = pd.Timestamp("2025-01-01")
    for month_index in range(14):
        month = start + pd.DateOffset(months=month_index)
        volume = 40 + month_index * 3
        for row_index in range(volume):
            day = rng.randint(1, 27)
            status = rng.choices(KNOWN_STATUSES, weights=weights, k=1)[0]
            event_day = month + pd.Timedelta(days=day - 1)
            agent, team = people[row_index % len(people)]
            rows.append(
                {
                    "item_id": f"demo-{month_index}-{row_index}",
                    "name": agent,
                    "group": team,
                    "event_raw": event_day.strftime("%Y-%m-%d"),
                    "event_kind": "date",
                    "status_raw": status,
                    "agent": agent,
                    "team": team,
                }
            )
    return pd.DataFrame(rows)


def records_frame(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=RAW_COLUMNS)
    return pd.DataFrame.from_records(records, columns=RAW_COLUMNS)
