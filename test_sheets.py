import pandas as pd

from sheets_client import SheetError, export_url, guess_column, parse_sheet_url, to_records

SHEET = (
    "https://docs.google.com/spreadsheets/d/1HDfOvmyhDYpDqfro1ON1z4DHazmGWvgYtdmxp6TIgBs/"
    "edit?gid=1935943579#gid=1935943579"
)


def test_parse_sheet_url() -> None:
    sheet_id, gid = parse_sheet_url(SHEET)
    assert sheet_id == "1HDfOvmyhDYpDqfro1ON1z4DHazmGWvgYtdmxp6TIgBs"
    assert gid == "1935943579"
    assert export_url(sheet_id, gid).endswith("export?format=csv&gid=1935943579")


def test_parse_rejects_other_links() -> None:
    try:
        parse_sheet_url("https://example.com/not-a-sheet")
    except SheetError:
        return
    raise AssertionError("expected SheetError")


def test_to_records_uses_calendar_dates() -> None:
    frame = pd.DataFrame(
        {
            "Created": ["1/15/2026", "2/2/2026", ""],
            "Status": ["Approved", "Rejected", "Approved"],
            "Deal": ["Alpha", "Beta", "Gamma"],
        }
    )
    records = to_records(frame, date_column="Created", status_column="Status", name_column="Deal")
    assert list(records["event_raw"]) == ["2026-01-15", "2026-02-02", ""]
    assert list(records["status_raw"]) == ["Approved", "Rejected", "Approved"]
    assert records.loc[0, "name"] == "Alpha"
    assert records.loc[0, "event_kind"] == "date"
    assert records.loc[0, "item_id"] == "2"


def test_guess_column() -> None:
    columns = ["Deal name", "Created date", "Stage", "Team Manager", "Team"]
    assert guess_column(columns, ("date", "created")) == 1
    assert guess_column(columns, ("status", "stage")) == 2
    assert guess_column(columns, ("team",), avoid=("manager",)) == 4


if __name__ == "__main__":
    test_parse_sheet_url()
    test_parse_rejects_other_links()
    test_to_records_uses_calendar_dates()
    test_guess_column()
    print("ok")
