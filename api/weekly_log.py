"""
Weekly production log (2026-09-28, user-requested): one row per Style per
Weekly Sales Report week (1-7 / 8-15 / 16-23 / 24-end). While the week runs,
its ~2-month forecast, stock + WIP and suggested production are refreshed on
every data refresh; once the week has finished, its actual sale is filled in
and the row is locked (never updated again). The week's own forecast
(forecast_qty) is kept as saved at week start, for an honest accuracy score.

Stored as a SQLite table in api/.cache/ (a local cache, not a shared
database). Only the latest ``KEEP_COMPLETED_WEEKS`` completed weeks are kept
(~2 months): closing a new week deletes the oldest one beyond that. The week
still running is always kept.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from datetime import date
from pathlib import Path

_DB = Path(__file__).resolve().parent / ".cache" / "weekly_production_log.sqlite"
KEEP_COMPLETED_WEEKS = 8
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_production (
    style                    TEXT    NOT NULL,
    sub_category             TEXT,
    category                 TEXT,
    month                    TEXT    NOT NULL,  -- e.g. "October 2026"
    week                     TEXT    NOT NULL,  -- "W1".."W4"
    week_start               TEXT    NOT NULL,  -- ISO date
    week_end                 TEXT    NOT NULL,  -- ISO date
    forecast_qty             INTEGER NOT NULL,  -- forecast for this week, as of week start (never updated)
    forecast_2m_qty          INTEGER NOT NULL,  -- forecast for the ~2 months from the week (live until completed)
    available_qty            INTEGER NOT NULL,  -- stock + WIP (live until completed)
    suggested_production_qty INTEGER NOT NULL,  -- max(0, forecast_2m - available) (live until completed)
    captured_on              TEXT    NOT NULL,  -- data date of the week-start capture
    updated_on               TEXT,              -- data date of the last live update
    actual_qty               INTEGER,           -- gross units sold in the week (once completed)
    completed_on             TEXT,              -- data snapshot date the actual came from
    status                   TEXT    NOT NULL DEFAULT 'open',  -- 'open' | 'completed'
    PRIMARY KEY (style, week_start)
)
"""


def _connect() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_production)")}
    if "updated_on" not in cols:  # tables created before updated_on existed
        conn.execute("ALTER TABLE weekly_production ADD COLUMN updated_on TEXT")
        conn.execute("UPDATE weekly_production SET updated_on = captured_on")
    return conn


def save_running_week(rows: list[dict], month: str, week: str, week_start: date, week_end: date, as_of: date) -> int:
    """Save the running week for every Style (``rows``: dicts with style,
    sub_category, category, forecast_qty, forecast_2m_qty, available_qty,
    suggested_production_qty): new Styles are inserted; existing OPEN rows
    get their ~2-month forecast, stock + WIP and suggestion refreshed.
    forecast_qty and captured_on keep their week-start values, and
    completed rows are never touched."""
    with _LOCK, _connect() as conn:
        cur = conn.executemany(
            """INSERT INTO weekly_production
               (style, sub_category, category, month, week, week_start, week_end, forecast_qty,
                forecast_2m_qty, available_qty, suggested_production_qty, captured_on, updated_on)
               VALUES (:style, :sub_category, :category, :month, :week, :week_start, :week_end,
                       :forecast_qty, :forecast_2m_qty, :available_qty, :suggested_production_qty, :as_of, :as_of)
               ON CONFLICT (style, week_start) DO UPDATE SET
                   forecast_2m_qty          = excluded.forecast_2m_qty,
                   available_qty            = excluded.available_qty,
                   suggested_production_qty = excluded.suggested_production_qty,
                   updated_on               = excluded.updated_on
               WHERE weekly_production.status = 'open'""",
            [{**r, "month": month, "week": week, "week_start": week_start.isoformat(),
              "week_end": week_end.isoformat(), "as_of": as_of.isoformat()} for r in rows],
        )
        return cur.rowcount


def open_weeks_ending_by(data_through: date) -> list[str]:
    """week_start of every still-open week whose last day is covered by the data."""
    with _LOCK, _connect() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT week_start FROM weekly_production WHERE status = 'open' AND week_end <= ?",
            (data_through.isoformat(),))]


def complete_week(week_start: str, actual_by_style: dict[str, int], completed_on: date) -> None:
    """Fill in the actual sale for a finished week and mark it completed, then
    drop completed weeks older than the latest KEEP_COMPLETED_WEEKS."""
    with _LOCK, _connect() as conn:
        styles = [r[0] for r in conn.execute(
            "SELECT style FROM weekly_production WHERE week_start = ?", (week_start,))]
        conn.executemany(
            """UPDATE weekly_production SET actual_qty = ?, completed_on = ?, status = 'completed'
               WHERE style = ? AND week_start = ?""",
            [(int(actual_by_style.get(s, 0)), completed_on.isoformat(), s, week_start) for s in styles],
        )
        keep = [r[0] for r in conn.execute(
            """SELECT DISTINCT week_start FROM weekly_production WHERE status = 'completed'
               ORDER BY week_start DESC LIMIT ?""", (KEEP_COMPLETED_WEEKS,))]
        if len(keep) == KEEP_COMPLETED_WEEKS:
            dropped = conn.execute(
                "DELETE FROM weekly_production WHERE status = 'completed' AND week_start < ?", (keep[-1],)).rowcount
            if dropped:
                print(f"[weekly_log] dropped {dropped} rows older than {keep[-1]}", file=sys.stderr)


def read(style: str = "", status: str = "") -> list[dict]:
    """Every stored row (newest week first), optionally for one Style / status."""
    sql, args = "SELECT * FROM weekly_production WHERE 1=1", []
    if style:
        sql += " AND style = ?"
        args.append(style)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY week_start DESC, forecast_2m_qty DESC"
    with _LOCK, _connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]
