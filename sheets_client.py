"""Read a Google Sheet that is shared as anyone with the link."""

from __future__ import annotations

import io
import re
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

from volume import RAW_COLUMNS

SHEET_ID = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
PRIVATE_MESSAGE = (
    "This spreadsheet is private. In Google Sheets, open Share and set "
    "General access to Anyone with the link, as Viewer. Then press Refresh data."
)


class SheetError(Exception):
    """The sheet link could not be read."""


def parse_sheet_url(url: str) -> tuple[str, str | None]:
    text = url.strip()
    parsed = urlparse(text)
    match = SHEET_ID.search(parsed.path)
    if not match:
        raise SheetError("Paste a Google Sheets link, including docs.google.com/spreadsheets.")
    gid = _query_value(parsed.query, "gid") or _query_value(parsed.fragment, "gid")
    return match.group(1), gid


def export_url(sheet_id: str, gid: str | None) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        return f"{url}&gid={gid}"
    return url


def fetch_sheet(url: str) -> pd.DataFrame:
    sheet_id, gid = parse_sheet_url(url)
    try:
        response = requests.get(export_url(sheet_id, gid), timeout=60)
    except requests.RequestException as exc:
        raise SheetError("Could not reach Google Sheets. Check the connection and try again.") from exc

    content_type = response.headers.get("content-type", "")
    if response.status_code in {401, 403} or "text/html" in content_type:
        raise SheetError(PRIVATE_MESSAGE)
    if response.status_code != 200:
        raise SheetError(f"Google Sheets returned status {response.status_code}.")

    frame = pd.read_csv(io.BytesIO(response.content))
    frame = frame.dropna(how="all")
    frame.columns = [str(column).strip() for column in frame.columns]
    if frame.empty or frame.shape[1] == 0:
        raise SheetError("That sheet has no data rows.")
    return frame


def named_columns(frame: pd.DataFrame) -> list[str]:
    return [str(column) for column in frame.columns if not str(column).startswith("Unnamed")]


def guess_column(columns: list[str], keywords: tuple[str, ...], avoid: tuple[str, ...] = ()) -> int:
    folded = [column.lower() for column in columns]
    for keyword in keywords:
        for index, name in enumerate(folded):
            if any(word in name for word in avoid):
                continue
            if keyword in name:
                return index
    return 0


def to_records(
    frame: pd.DataFrame,
    *,
    date_column: str,
    status_column: str,
    name_column: str | None = None,
    group_column: str | None = None,
    agent_column: str | None = None,
    team_column: str | None = None,
) -> pd.DataFrame:
    """Map spreadsheet columns onto the dashboard record shape."""
    parsed = pd.to_datetime(frame[date_column], errors="coerce")
    agent = _text_column(frame, agent_column or name_column)
    team = _text_column(frame, team_column or group_column)
    records = pd.DataFrame(
        {
            "item_id": [str(position + 2) for position in range(len(frame))],
            "name": agent,
            "group": team,
            "event_raw": parsed.dt.strftime("%Y-%m-%d").fillna(""),
            "event_kind": "date",
            "status_raw": _text_column(frame, status_column),
            "agent": agent,
            "team": team,
        }
    )
    keep = records["event_raw"].ne("") | records["status_raw"].ne("")
    kept = records.loc[keep, [*RAW_COLUMNS, "agent", "team"]].reset_index(drop=True)
    if kept.empty:
        return pd.DataFrame(columns=RAW_COLUMNS)
    return kept


def _text_column(frame: pd.DataFrame, column: str | None) -> pd.Series:
    if not column or column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index)
    return frame[column].fillna("").astype(str).str.strip()


def _query_value(text: str, key: str) -> str | None:
    values = parse_qs(text).get(key)
    if not values or not values[0]:
        return None
    return values[0]
