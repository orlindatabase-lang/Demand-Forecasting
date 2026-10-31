"""
Latest and Frozen forecast endpoints (2026-10-31, user-requested: "make the
live forecast and the frozen one separate endpoints, with a simple JSON a new
front end can be built on").

Both return the SAME shape - a list of weeks, a total, and one row per Style
or Sub Category, each with its forecast per week and per month - read from
the Weekly Sales Report's own cached grid (data.get_weekly_grid), so every
number is exactly what the dashboard shows:

- latest: today's forecast for the running week and the upcoming weeks;
  changes on every data refresh.
- frozen: each week's forecast as it was frozen (rolling 3 months, see
  forecast_freeze.py) and never changes after that; optionally also the
  completed weeks before the running one.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import data
from models import ForecastTable, ForecastTableRow, ForecastTableTotal, ForecastWeek

_ALL_ROWS = 100_000


def _weeks(grid, today: date) -> list[ForecastWeek]:
    """Every report week of the grid (W1 1-7, W2 8-15, W3 16-23, W4 24-end),
    oldest first, with its end date and status."""
    starts = [(date.fromisoformat(w.weekStart), m.label, w.label) for m in grid.months for w in m.weeks]
    starts.sort()
    out = []
    for i, (start, month_label, label) in enumerate(starts):
        if i + 1 < len(starts):
            end = starts[i + 1][0] - timedelta(days=1)
        else:
            end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        status = "completed" if end < today else ("running" if start <= today else "upcoming")
        out.append(ForecastWeek(
            weekStart=start.isoformat(), weekEnd=end.isoformat(), month=start.strftime("%Y-%m"),
            label=f"{start:%b} {label}", status=status,
        ))
    return out


def _month_totals(by_week: dict[str, int], weeks: list[ForecastWeek]) -> dict[str, int]:
    month_of = {w.weekStart: w.month for w in weeks}
    out: dict[str, int] = {}
    for iso, qty in by_week.items():
        out[month_of[iso]] = out.get(month_of[iso], 0) + qty
    return out


def forecast_table(
    kind: str, group_by: str, weeks: int = 13, past_weeks: int = 0,
    category: str = "", sub_category: str = "", style: str = "",
) -> ForecastTable:
    """``kind`` "latest" or "frozen" - see the module docstring."""
    today = date.today()
    grid = data.get_weekly_grid(group_by, weeks, _ALL_ROWS, 0, "", sub_category, category)
    all_weeks = _weeks(grid, today)
    current = [i for i, w in enumerate(all_weeks) if w.status != "completed"]
    first = current[0] if current else len(all_weeks)
    if kind == "frozen":
        first = max(0, first - past_weeks)
    shown = all_weeks[first:]
    field = "liveForecast" if kind == "latest" else "frozenForecast"

    needle = style.strip().upper()
    rows: list[ForecastTableRow] = []
    total_by_week = {w.weekStart: 0 for w in shown}
    for r in grid.rows:
        if needle and r.key.upper() != needle:
            continue
        by_week: dict[str, int] = {}
        for w in shown:
            qty = getattr(r.cells.get(w.weekStart), field, None) if w.weekStart in r.cells else None
            if qty is None:
                continue
            by_week[w.weekStart] = qty
            total_by_week[w.weekStart] += qty
        rows.append(ForecastTableRow(
            style=r.key if group_by == "style" else None,
            subCategory=r.subCategory, category=r.category,
            total=sum(by_week.values()), byMonth=_month_totals(by_week, shown), byWeek=by_week,
        ))
    rows.sort(key=lambda x: x.total, reverse=True)

    if kind == "latest":
        about = ("Today's forecast for the running week and the upcoming weeks. "
                 "Changes on every data refresh.")
    else:
        about = ("The frozen forecast: each week is frozen at its forecast on the day it first comes "
                 "within the next 3 months, and never changes after that.")
    return ForecastTable(
        forecast=kind, about=about, unit="Gross sale units",
        dataThrough=data.SNAPSHOT_DATE.isoformat() if data.SNAPSHOT_DATE else "",
        frozenOn=grid.weeklyTotals.frozenOn if kind == "frozen" else None,
        groupBy=group_by, weeks=shown,
        total=ForecastTableTotal(
            total=sum(total_by_week.values()), byMonth=_month_totals(total_by_week, shown), byWeek=total_by_week,
        ),
        rowCount=len(rows), rows=rows,
    )
