"""Monthly volume and conversion dashboard."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from volume import (
    KNOWN_STATUSES,
    STATUS_OPTIONS,
    apply_status,
    agent_daily,
    breakdown_by,
    build_daily,
    build_monthly,
    default_classification,
    localize_events,
    mapping_from_table,
    month_label,
    month_over_month,
    month_to_date,
    period_kpis,
    team_daily,
    team_for_agents,
)
from sheets_client import SheetError, fetch_sheet, guess_column, named_columns, to_records

# Query tab. The Raw tab (gid 1935943579) is not the dashboard source.
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1HDfOvmyhDYpDqfro1ON1z4DHazmGWvgYtdmxp6TIgBs/"
    "edit?gid=526210959#gid=526210959"
)
TIMEZONE = "America/Los_Angeles"
STATUS_COLORS = {
    "Approved": "#1F7A4D",
    "Active deals": "#2F6FED",
    "Rejected": "#D4524E",
    "Correction needed": "#E09A2B",
    "Missing information": "#8A6A3B",
    "No status": "#A8A29E",
}
EXTRA_COLORS = ["#5C6B73", "#7A6A9A", "#3E7C7C", "#C46B4A", "#3F5E8A", "#9A5B7A"]
PLOT_CONFIG = {"displayModeBar": False, "responsive": True}


def main() -> None:
    st.set_page_config(
        page_title="Volume",
        page_icon="▦",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()
    st.title("Volume")
    st.caption(
        "Each record is counted on its generation date, using its current status. "
        "Conversion is Approved divided by every record in the period."
    )

    connection_controls()
    load_sheet(TIMEZONE)


def connection_controls() -> None:
    st.sidebar.header("Connection")
    if st.sidebar.button("Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()


def load_sheet(timezone: str) -> None:
    st.sidebar.header("Spreadsheet")
    url = read_secret("SHEET_URL") or DEFAULT_SHEET_URL

    try:
        with st.spinner("Reading the spreadsheet…"):
            frame = cached_sheet(url)
    except SheetError as exc:
        st.error(str(exc))
        return

    columns = named_columns(frame)
    if not columns:
        st.error("That sheet has no column names in the first row.")
        return

    date_column = st.sidebar.selectbox(
        "Date column",
        columns,
        index=guess_column(columns, ("generation date", "date", "created", "fecha")),
        key=f"sheet-date-{url}",
    )
    status_column = st.sidebar.selectbox(
        "Status column",
        columns,
        index=guess_column(columns, ("grupo", "status", "stage", "estado")),
        key=f"sheet-status-{url}",
    )
    agent_column = st.sidebar.selectbox(
        "Agent column",
        columns,
        index=guess_column(columns, ("full name", "agent", "name"), avoid=("manager",)),
        key=f"sheet-agent-{url}",
    )
    team_column = st.sidebar.selectbox(
        "Team column",
        columns,
        index=guess_column(columns, ("team",), avoid=("manager",)),
        key=f"sheet-team-{url}",
    )

    records = to_records(
        frame,
        date_column=date_column,
        status_column=status_column,
        agent_column=agent_column,
        team_column=team_column,
    )
    has_team = records["team"].fillna("").astype(str).str.strip().ne("")
    records = records.loc[has_team].reset_index(drop=True)
    source = localize_events(records, timezone)
    render_board(
        source,
        scope=f"sheet-{url}",
        board_name="Google Sheet",
        date_label=date_column,
        status_label=status_column,
    )


def render_board(
    source: pd.DataFrame,
    *,
    scope: str,
    board_name: str,
    date_label: str,
    status_label: str,
) -> None:
    if source.empty:
        st.info("This spreadsheet has no records.")
        return

    labeled = source.copy()
    labeled["column_status"] = labeled["status_raw"]
    classified = classify(labeled, scope)
    filtered, undated, period_label, population, end = apply_period_filters(classified, scope)

    st.caption(f"{board_name} · date from {date_label} · status from {status_label}.")
    if undated:
        st.caption(f"{undated:,} records have no date and are left out of the totals and the conversion rate.")

    monthly_tab, daily_tab, agent_tab, team_tab = st.tabs(["Monthly", "Daily", "Agents", "Teams"])
    with monthly_tab:
        render_monthly(filtered, period_label)
    with daily_tab:
        render_daily(filtered, period_label)
    with agent_tab:
        render_agents(population, end)
    with team_tab:
        render_teams(population, end)


def classify(source: pd.DataFrame, scope: str) -> pd.DataFrame:
    base = default_classification(source["status_raw"] if "status_raw" in source.columns else pd.Series(dtype=str))
    if base.empty:
        return apply_status(source, {})

    needs_review = bool((base["Counts as"] == "Other").any())
    with st.expander("Status labels", expanded=False):
        st.caption(
            "Approved, Active deals, Rejected, Correction needed, and Missing information "
            "are recognized automatically. If a label uses another name, map it here."
        )
        if len(base) > 40:
            st.caption("There are many distinct labels, so the automatic mapping was kept.")
            edited = base
        else:
            edited = st.data_editor(
                base,
                disabled=["Label", "Records"],
                column_config={
                    "Counts as": st.column_config.SelectboxColumn(
                        "Counts as",
                        options=STATUS_OPTIONS,
                        required=True,
                    )
                },
                hide_index=True,
                width="stretch",
                key=f"status-editor-{scope}",
            )
    if needs_review and (edited["Counts as"] == "Other").any():
        st.warning(
            "Some labels are set to Other. Conversion only counts records marked Approved."
        )
    return apply_status(source, mapping_from_table(edited))


def apply_period_filters(df: pd.DataFrame, scope: str) -> tuple[pd.DataFrame, int, str, pd.DataFrame, object]:
    st.sidebar.header("Period")
    team_values = _labels(df, "team", "group")
    agent_values = _labels(df, "agent", "name")
    if len(team_values) > 1:
        selected_teams = st.sidebar.multiselect("Teams", team_values, default=team_values, key=f"teams-{scope}")
    else:
        selected_teams = team_values
    if len(agent_values) > 1:
        selected_agents = st.sidebar.multiselect("Agents", agent_values, default=agent_values, key=f"agents-{scope}")
    else:
        selected_agents = agent_values

    team_column = "team" if "team" in df.columns else "group"
    agent_column = "agent" if "agent" in df.columns else "name"
    scoped = _filter_labels(df, team_column, selected_teams)
    scoped = _filter_labels(scoped, agent_column, selected_agents)
    undated = int(scoped["event_date"].isna().sum()) if "event_date" in scoped.columns else 0
    dated = scoped.dropna(subset=["event_date"])
    years = [int(year) for year in sorted(dated["event_date"].dt.year.unique())]
    if years:
        selected_years = st.sidebar.multiselect("Years", years, default=years, key=f"years-{scope}")
    else:
        selected_years = []

    if dated.empty:
        return dated.copy(), undated, "no dated records", dated.copy(), None

    min_day = dated["event_date"].min().date()
    max_day = dated["event_date"].max().date()
    start = st.sidebar.date_input("Start date", value=min_day, key=f"start-date-{scope}")
    end = st.sidebar.date_input("End date", value=max_day, key=f"end-date-{scope}")
    period_label = f"{format_day(start)} – {format_day(end)}"

    if selected_years:
        dated = dated[dated["event_date"].dt.year.isin(selected_years)]
    else:
        dated = dated.iloc[0:0]
    population = dated.copy()
    if start > end:
        st.sidebar.warning("Start date is after the end date.")
        return dated.iloc[0:0].copy(), undated, period_label, population, end
    filtered = dated[(dated["event_date"].dt.date >= start) & (dated["event_date"].dt.date <= end)]
    return filtered.copy(), undated, period_label, population, end


def _labels(df: pd.DataFrame, primary: str, fallback: str) -> list[str]:
    column = primary if primary in df.columns else fallback
    if column not in df.columns:
        return []
    text = df[column].fillna("").astype(str).str.strip()
    labels = sorted({value for value in text.unique() if value})
    if (text == "").any():
        labels.append("(blank)")
    return labels


def _filter_labels(df: pd.DataFrame, column: str, selected: list[str]) -> pd.DataFrame:
    if column not in df.columns or not selected:
        return df.iloc[0:0]
    text = df[column].fillna("").astype(str).str.strip().replace("", "(blank)")
    return df[text.isin(selected)]


def render_monthly(filtered: pd.DataFrame, period_label: str) -> None:
    if filtered.empty:
        st.info("No dated records in the selected period.")
        return
    monthly = build_monthly(filtered)
    render_kpis(period_kpis(filtered), monthly)
    render_charts(monthly)
    render_table(monthly, period_label)


def render_daily(filtered: pd.DataFrame, period_label: str) -> None:
    st.caption(f"Showing {period_label}.")
    if filtered.empty:
        st.info("No dated records in the selected period.")
        return
    daily = build_daily(filtered)
    render_kpis(period_kpis(filtered), None)
    labels = [day_label(value) for value in daily["day"]]
    status_columns = _status_columns(daily, "day")
    st.subheader("Records by day")
    bars = go.Figure()
    for status in status_columns:
        bars.add_trace(go.Bar(x=labels, y=daily[status], name=status, marker_color=color_for(status)))
    style_figure(bars, height=460)
    bars.update_layout(barmode="stack", margin=dict(l=8, r=8, t=28, b=72))
    bars.update_yaxes(title_text="Records", rangemode="tozero")
    st.plotly_chart(bars, width="stretch", config=PLOT_CONFIG)

    st.subheader("Day detail")
    view = _period_view(daily, "day", "Day", day_label)
    render_count_table(view, "Day")


def render_agents(population: pd.DataFrame, end) -> None:
    current, window = _current_month(population, end)
    st.caption(f"Month to date: {window}. One row per agent.")
    if current.empty:
        st.info("No records in that month.")
        return
    render_kpis(period_kpis(current), None)
    agents = breakdown_by(current, "agent")
    teams = team_for_agents(current)
    agents.insert(1, "Team", agents["Agent"].map(teams).fillna(""))
    st.subheader("Agents")
    chart = go.Figure(
        go.Bar(
            x=agents["Total"],
            y=agents["Agent"],
            orientation="h",
            marker_color="#2F6FED",
            text=[f"{int(value):,}" for value in agents["Total"]],
            textposition="outside",
            cliponaxis=False,
        )
    )
    style_figure(chart, height=max(280, 42 * len(agents) + 80))
    chart.update_layout(margin=dict(l=8, r=48, t=8, b=24), yaxis=dict(autorange="reversed"))
    chart.update_xaxes(title_text="Records", rangemode="tozero")
    st.plotly_chart(chart, width="stretch", config=PLOT_CONFIG)
    render_count_table(agents, "Agent")
    render_agent_daily(current, list(agents["Agent"]))


def render_agent_daily(current: pd.DataFrame, names: list[str]) -> None:
    st.subheader("Daily")
    st.caption("Month to date for the agents you pick. Start with one so the page stays readable.")
    if not names:
        return
    selected = st.multiselect(
        "Agents in the daily view",
        names,
        default=names[:1],
        key="agent-daily-filter",
    )
    if not selected:
        st.info("Select at least one agent.")
        return

    agent_key = current["agent"].fillna("").astype(str).str.strip().replace("", "(blank)")
    scoped = current.loc[agent_key.isin(selected)].copy()
    if scoped.empty:
        st.info("No records for the selected agents.")
        return

    if len(selected) == 1:
        daily = build_daily(scoped)
        labels = [day_label(value) for value in daily["day"]]
        bars = go.Figure()
        for status in _status_columns(daily, "day"):
            bars.add_trace(go.Bar(x=labels, y=daily[status], name=status, marker_color=color_for(status)))
        style_figure(bars, height=420)
        bars.update_layout(barmode="stack", margin=dict(l=8, r=8, t=28, b=72))
        bars.update_yaxes(title_text="Records", rangemode="tozero")
        st.plotly_chart(bars, width="stretch", config=PLOT_CONFIG)
        render_count_table(_period_view(daily, "day", "Day", day_label), "Day")
        return

    by_day = agent_daily(scoped)
    teams = team_for_agents(scoped)
    by_day.insert(2, "Team", by_day["Agent"].map(teams).fillna(""))
    wide = (
        by_day.assign(day=pd.to_datetime(by_day["day"]))
        .pivot_table(index="day", columns="Agent", values="Total", aggfunc="sum")
        .reindex(columns=selected)
        .fillna(0)
        .sort_index()
    )
    labels = [day_label(day) for day in wide.index]
    bars = go.Figure()
    for index, agent in enumerate(selected):
        bars.add_trace(
            go.Bar(
                x=labels,
                y=wide[agent].astype(int),
                name=agent,
                marker_color=EXTRA_COLORS[index % len(EXTRA_COLORS)],
            )
        )
    style_figure(bars, height=420)
    bars.update_layout(barmode="group", margin=dict(l=8, r=8, t=28, b=72))
    bars.update_yaxes(title_text="Records", rangemode="tozero")
    st.plotly_chart(bars, width="stretch", config=PLOT_CONFIG)

    view = by_day.copy()
    view.insert(0, "Day", pd.to_datetime(view["day"]).map(day_label))
    view = view.drop(columns=["day"])
    front = [column for column in ["Day", "Agent", "Team", "Total", "Conversion"] if column in view.columns]
    rest = [column for column in view.columns if column not in front]
    render_count_table(view[front + rest], "Day")


def render_teams(population: pd.DataFrame, end) -> None:
    current, window = _current_month(population, end)
    st.caption(f"Month to date: {window}.")
    if current.empty:
        st.info("No records in that month.")
        return
    teams = breakdown_by(current, "team")
    st.subheader("Teams, month to date")
    chart = go.Figure()
    for status in _status_columns(teams, "Team"):
        chart.add_trace(go.Bar(x=teams["Team"], y=teams[status], name=status, marker_color=color_for(status)))
    style_figure(chart, height=420)
    chart.update_layout(barmode="stack", margin=dict(l=8, r=8, t=8, b=72))
    chart.update_yaxes(title_text="Records", rangemode="tozero")
    st.plotly_chart(chart, width="stretch", config=PLOT_CONFIG)
    render_count_table(teams, "Team")

    st.subheader("Teams, daily")
    st.caption("Each day in the month to date, split by team.")
    by_day = team_daily(current)
    view = by_day.copy()
    view.insert(0, "Day", view["day"].map(day_label))
    view = view.drop(columns=["day"])
    render_count_table(view, "Day")


def _current_month(population: pd.DataFrame, end) -> tuple[pd.DataFrame, str]:
    if end is None or population.empty:
        return population.iloc[0:0].copy(), "no dated records"
    current = month_to_date(population, end)
    start = end.replace(day=1)
    return current, f"{format_day(start)} – {format_day(end)}"


def _status_columns(frame: pd.DataFrame, label: str) -> list[str]:
    skip = {label, "day", "month", "Total", "Cumulative", "Conversion", "Team", "Agent"}
    return [column for column in frame.columns if column not in skip]


def _period_view(frame: pd.DataFrame, column: str, label: str, formatter) -> pd.DataFrame:
    view = frame.copy()
    view.insert(0, label, view[column].map(formatter))
    view = view.drop(columns=[column])
    front = [column for column in [label, "Total", "Cumulative", "Conversion"] if column in view.columns]
    rest = [column for column in view.columns if column not in front]
    return view[front + rest]


def render_count_table(view: pd.DataFrame, label: str) -> None:
    column_config = {
        label: st.column_config.TextColumn(label),
        "Team": st.column_config.TextColumn("Team"),
        "Agent": st.column_config.TextColumn("Agent"),
        "Total": st.column_config.NumberColumn("Total", format="localized"),
        "Cumulative": st.column_config.NumberColumn("Cumulative", format="localized"),
        "Conversion": st.column_config.NumberColumn("Conversion", format="percent", help="Approved / total"),
    }
    for status in KNOWN_STATUSES:
        if status in view.columns:
            column_config[status] = st.column_config.NumberColumn(status, format="localized")
    st.dataframe(view, column_config=column_config, hide_index=True, width="stretch")


def render_kpis(kpis: dict, monthly: pd.DataFrame) -> None:
    if kpis["approved"] == 0 and kpis["total"] > 0:
        st.warning(
            "No records in this period are classified as Approved, so conversion is 0%. "
            "Check Status labels if the sheet uses a different name."
        )

    total_col, approved_col, rate_col = st.columns(3)
    total_col.metric("Records", f"{kpis['total']:,}", help="Items with a date in the selected period.")
    approved_col.metric("Approved", f"{kpis['approved']:,}", help="Items classified as Approved.")
    rate_col.metric(
        "Conversion",
        format_percent(kpis["rate"]),
        help="Approved / every record in the period.",
    )

    other_cols = st.columns(4)
    for column, status in zip(
        other_cols,
        ["Active deals", "Rejected", "Correction needed", "Missing information"],
    ):
        column.metric(status, f"{kpis['counts'][status]:,}")

    if kpis["extras"]:
        st.caption(f"Other statuses outside that list: {kpis['extras']:,} records.")

    if monthly is not None and "month" in getattr(monthly, "columns", []):
        change = month_over_month(monthly)
        if change:
            rate_text = "no comparable rate"
            if change["rate_delta"] is not None:
                rate_text = f"{change['rate_delta'] * 100:+.1f} pp"
            st.caption(
                f"{change['last_label']} vs {change['prev_label']}: "
                f"{change['total_delta']:+,} records, conversion {rate_text}."
            )


def render_charts(monthly: pd.DataFrame) -> None:
    labels = [month_label(value) for value in monthly["month"]]
    status_columns = [
        column
        for column in monthly.columns
        if column not in {"month", "Total", "Cumulative", "Conversion"}
    ]

    st.subheader("Records by month")
    bars = go.Figure()
    for status in status_columns:
        bars.add_trace(
            go.Bar(
                x=labels,
                y=monthly[status],
                name=status,
                marker_color=color_for(status),
            )
        )
    if len(monthly) <= 16:
        bars.add_trace(
            go.Scatter(
                x=labels,
                y=monthly["Total"],
                mode="text",
                text=[f"{int(value):,}" for value in monthly["Total"]],
                textposition="top center",
                showlegend=False,
                hoverinfo="skip",
                cliponaxis=False,
            )
        )
    style_figure(bars, height=460)
    bars.update_layout(barmode="stack", margin=dict(l=8, r=8, t=28, b=72))
    bars.update_yaxes(title_text="Records", rangemode="tozero")
    st.plotly_chart(bars, width="stretch", config=PLOT_CONFIG)

    left, right = st.columns(2)
    with left:
        st.subheader("Conversion")
        rates = [None if value is None or pd.isna(value) else float(value) * 100 for value in monthly["Conversion"]]
        conversion = go.Figure()
        conversion.add_trace(
            go.Scatter(
                x=labels,
                y=rates,
                mode="lines+markers",
                name="Approved / total",
                line=dict(color="#1F7A4D", width=3),
                marker=dict(size=8),
                connectgaps=False,
            )
        )
        style_figure(conversion, height=380)
        conversion.update_yaxes(title_text="Approved / total", range=[0, 100], ticksuffix="%")
        st.plotly_chart(conversion, width="stretch", config=PLOT_CONFIG)

    with right:
        st.subheader("Running total")
        cumulative = go.Figure()
        cumulative.add_trace(
            go.Scatter(
                x=labels,
                y=monthly["Cumulative"],
                mode="lines",
                name="Running total",
                line=dict(color="#2F6FED", width=3),
                fill="tozeroy",
                fillcolor="rgba(47, 111, 237, 0.16)",
            )
        )
        style_figure(cumulative, height=380)
        cumulative.update_yaxes(title_text="Records", rangemode="tozero")
        st.plotly_chart(cumulative, width="stretch", config=PLOT_CONFIG)


def render_table(monthly: pd.DataFrame, year_label: str) -> None:
    st.subheader("Month detail")
    st.caption(f"Showing {year_label}. The running total adds the months in this table, in order.")
    view = monthly.copy()
    view.insert(0, "Month", view["month"].map(month_label))
    view = view.drop(columns=["month"])
    front = [column for column in ["Month", "Total", "Cumulative", "Conversion"] if column in view.columns]
    rest = [column for column in view.columns if column not in front]
    view = view[front + rest]
    column_config = {
        "Month": st.column_config.TextColumn("Month"),
        "Total": st.column_config.NumberColumn("Total", format="localized"),
        "Cumulative": st.column_config.NumberColumn("Cumulative", format="localized"),
        "Conversion": st.column_config.NumberColumn(
            "Conversion",
            format="percent",
            help="Approved / total for that month",
        ),
    }
    for status in KNOWN_STATUSES:
        if status in view.columns:
            column_config[status] = st.column_config.NumberColumn(status, format="localized")
    st.dataframe(view, column_config=column_config, hide_index=True, width="stretch")


def day_label(value) -> str:
    stamp = pd.Timestamp(value)
    return f"{stamp:%b} {stamp.day}"


def color_for(status: str) -> str:
    if status in STATUS_COLORS:
        return STATUS_COLORS[status]
    return EXTRA_COLORS[sum(ord(ch) for ch in status) % len(EXTRA_COLORS)]


def style_figure(figure: go.Figure, *, height: int) -> None:
    figure.update_layout(
        height=height,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=8, b=72),
        legend=dict(orientation="h", yanchor="top", y=-0.22, x=0),
        font=dict(family="Segoe UI, sans-serif", size=13, color="#1C1917"),
        hovermode="x unified",
    )
    figure.update_xaxes(showgrid=False)
    figure.update_yaxes(gridcolor="#E7E5E4", zeroline=False)


def format_day(value) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def format_percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value * 100:.1f}%"


def read_secret(name: str) -> str:
    try:
        value = st.secrets[name]
    except Exception:
        return ""
    return str(value).strip() if value else ""


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.4rem; padding-bottom: 3rem; }
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e7e5e4;
            border-radius: 14px;
            padding: 0.7rem 0.9rem;
        }
        div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
        button[aria-label="Download as CSV"] { display: none !important; }
        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] * {
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: unset !important;
            height: auto !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=300, show_spinner=False)
def cached_sheet(url: str) -> pd.DataFrame:
    return fetch_sheet(url)


if __name__ == "__main__":
    main()
