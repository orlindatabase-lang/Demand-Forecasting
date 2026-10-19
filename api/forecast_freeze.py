"""
Frozen forecasts for completed report weeks (2026-09-29, user-requested:
"completed weeks and months forecast numbers must not change every time the
data is refreshed - only the upcoming weeks' data changes").

Every forecast cell the Weekly Sales Report (and its channel drill-downs)
shows is keyed by (scope, key, period_start) - e.g. ("grid:style", "417-03",
"2026-09-24"). While a period is upcoming OR still running, its value is
re-saved on every data refresh (status 'open') and the live value is shown.
Once the period has ENDED, the value last saved while it ran is locked
(status 'locked') and returned from then on, regardless of what a later
retrain or refresh would compute for it. (2026-10-12, user-requested: the
running week is no longer frozen - only completed weeks; running weeks
locked under the earlier rule are reopened.) A period first seen only after
it had already ended (e.g. history from before this module existed) is
locked at the value computed at that moment.

Stored as a SQLite table in api/.cache/ (a local cache, like weekly_log.py).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path

_DB = Path(__file__).resolve().parent / ".cache" / "forecast_freeze.sqlite"
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS frozen_forecast (
    scope        TEXT NOT NULL,  -- e.g. "grid:style", "grid:subCategory"
    key          TEXT NOT NULL,  -- row key within the scope
    period_start TEXT NOT NULL,  -- ISO date of the report week's first day
    qty          REAL NOT NULL,
    status       TEXT NOT NULL,  -- 'open' (still refreshed) | 'locked' (never changes)
    updated_on   TEXT NOT NULL,  -- real date of the last write
    start_qty    REAL,           -- the value when the period started (2026-10-14)
    PRIMARY KEY (scope, key, period_start)
);
CREATE TABLE IF NOT EXISTS frozen_snapshot (
    scope        TEXT NOT NULL,
    key          TEXT NOT NULL,
    as_of        TEXT NOT NULL,  -- first day of the week the snapshot was taken at
    period_start TEXT NOT NULL,  -- a week within SNAPSHOT_DAYS of as_of
    qty          REAL NOT NULL,  -- its forecast as saved just before as_of
    PRIMARY KEY (scope, key, as_of, period_start)
)
"""
# 2-month freeze (2026-10-15, user-requested: "freeze the forecast snapshot
# for the whole 2 months"): one snapshot of the running week and every week
# starting within SNAPSHOT_DAYS, kept unchanged until that window ends; then
# the next 2 months are frozen.
SNAPSHOT_DAYS = 62
# 2026-10-19, user-requested: no forecast snapshot of the NEXT 2 months - the
# frozen view keeps past (completed) weeks only. New 2-month snapshots are no
# longer taken; the existing one stays in the table (used for its completed
# weeks only). FORECAST_2M_FREEZE=on brings the 2-month freeze back.
import os as _os
SNAPSHOT_ENABLED = _os.getenv("FORECAST_2M_FREEZE", "off").strip().lower() in ("1", "on", "true")


def _connect() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB)
    conn.executescript(_SCHEMA)
    # Stores created before start_qty existed: add the column.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(frozen_forecast)")}
    if "start_qty" not in cols:
        conn.execute("ALTER TABLE frozen_forecast ADD COLUMN start_qty REAL")
    return conn


def _started(iso: str, today: date) -> bool:
    try:
        return date.fromisoformat(iso) <= today
    except ValueError:
        return False


def _completed(iso: str, end: date | None, today: date) -> bool:
    """Has the period starting on ``iso`` (ending on ``end``) finished as of
    ``today``? Without an end date, a period counts as a 7-day week."""
    if end is not None:
        return end < today
    try:
        return date.fromisoformat(iso) + timedelta(days=6) < today
    except ValueError:
        return False


def apply(
    scope: str,
    values: dict[str, dict[str, float]],
    period_end: dict[str, date],
    today: date | None = None,
) -> dict[str, dict[str, float]]:
    """Return ``values`` ({key: {period_start_iso: qty}}) with every COMPLETED
    period (its end in ``period_end`` before ``today``) replaced by its locked
    value, saving upcoming and running periods' current values and locking
    newly completed ones on the way. A running period locked under the old
    "freeze from the start" rule is reopened and follows the live value."""
    today = today or date.today()
    out: dict[str, dict[str, float]] = {}
    writes: list[tuple] = []
    reopen: list[tuple] = []
    starts: list[tuple] = []   # (start_qty, scope, key, period_start): the value when it started
    with _LOCK, _connect() as conn:
        stored = {}
        start_known = set()
        saved_on: dict[tuple[str, str], str] = {}
        for k, p, q, st, upd, sq in conn.execute(
                "SELECT key, period_start, qty, status, updated_on, start_qty FROM frozen_forecast WHERE scope = ?", (scope,)):
            stored[(k, p)] = (q, st)
            saved_on[(k, p)] = upd
            if sq is not None:
                start_known.add((k, p))
        locked_by_key: dict[str, dict[str, float]] = {}
        for (k, p), (q, s) in stored.items():
            if s == "locked":
                locked_by_key.setdefault(k, {})[p] = q
        stamp = today.isoformat()
        for key, cells in values.items():
            row: dict[str, float] = {}
            for iso, qty in cells.items():
                end = period_end.get(iso)
                prev = stored.get((key, iso))
                if (_started(iso, today) and (key, iso) not in start_known and prev is not None
                        and saved_on.get((key, iso), "") < iso):
                    # First refresh since it started: its last value saved BEFORE the
                    # start. Nothing saved before the start = start value unknown.
                    starts.append((prev[0], scope, key, iso))
                if _completed(iso, end, today):
                    if prev is None:
                        writes.append((scope, key, iso, qty, "locked", stamp))
                    elif prev[1] == "open":
                        qty = prev[0]                       # the last value saved while it ran
                        writes.append((scope, key, iso, qty, "locked", stamp))
                    else:
                        qty = prev[0]
                elif prev is not None and prev[1] == "locked":
                    reopen.append((qty, stamp, scope, key, iso))   # running, frozen by the old rule
                    locked_by_key.get(key, {}).pop(iso, None)
                elif prev is None or prev[0] != qty:
                    writes.append((scope, key, iso, qty, "open", stamp))
                row[iso] = qty
            # A completed period locked earlier stays even if today's live
            # values no longer include it (e.g. a cell that is now zero).
            for iso, q in locked_by_key.get(key, {}).items():
                row.setdefault(iso, q)
            out[key] = row
        if writes:
            conn.executemany(
                """INSERT INTO frozen_forecast (scope, key, period_start, qty, status, updated_on)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (scope, key, period_start) DO UPDATE SET
                       qty = excluded.qty, status = excluded.status, updated_on = excluded.updated_on
                   WHERE frozen_forecast.status = 'open'""",
                writes,
            )
        # Running periods locked by the old rule whose keys are not in today's
        # values (e.g. a style without a forecast now): reopen them as well.
        seen = {(k, iso) for k, cells in values.items() for iso in cells}
        for (k, iso), (q, st) in stored.items():
            if st == "locked" and (k, iso) not in seen and iso in period_end and not _completed(iso, period_end[iso], today):
                reopen.append((q, stamp, scope, k, iso))
        if reopen:
            conn.executemany(
                """UPDATE frozen_forecast SET qty = ?, status = 'open', updated_on = ?
                   WHERE scope = ? AND key = ? AND period_start = ?""",
                reopen,
            )
        if starts:
            conn.executemany(
                """UPDATE frozen_forecast SET start_qty = ?
                   WHERE scope = ? AND key = ? AND period_start = ? AND start_qty IS NULL""",
                starts,
            )
        # 2-month freeze: when there is none yet, or the active one's window
        # has ended, freeze today's forecast of the running week and every week
        # starting before today + SNAPSHOT_DAYS. It then stays unchanged.
        active = conn.execute("SELECT MAX(as_of) FROM frozen_snapshot WHERE scope = ?", (scope,)).fetchone()[0]
        if SNAPSHOT_ENABLED and (active is None or date.fromisoformat(active) + timedelta(days=SNAPSHOT_DAYS) <= today):
            first = current_week_start(period_end, today)
            horizon = (today + timedelta(days=SNAPSHOT_DAYS)).isoformat()
            snap = [(scope, k, today.isoformat(), p, float(q)) for k, cells in values.items()
                    for p, q in cells.items() if first and first <= p < horizon]
            if snap:
                conn.executemany("INSERT OR IGNORE INTO frozen_snapshot VALUES (?, ?, ?, ?, ?)", snap)
    return out


def current_week_start(period_end: dict[str, date], today: date) -> str:
    """Start (ISO) of the period containing ``today``, "" if none."""
    for iso, end in period_end.items():
        try:
            if date.fromisoformat(iso) <= today <= end:
                return iso
        except ValueError:
            continue
    return ""


def frozen_values(scope: str) -> tuple[dict[str, dict[str, float]], str, str]:
    """({key: {period_start_iso: qty}}, frozen on, frozen until): every week's
    value from the latest 2-month freeze that covers it (a newer freeze
    overrides an older one for the same week), plus the active freeze's date
    and the end of its window ("" if none yet)."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT key, period_start, qty, as_of FROM frozen_snapshot WHERE scope = ? ORDER BY as_of", (scope,),
        ).fetchall()
    out: dict[str, dict[str, float]] = {}
    latest = ""
    for k, p, q, a in rows:
        out.setdefault(k, {})[p] = q
        latest = max(latest, a)
    if not SNAPSHOT_ENABLED:
        return out, "", ""   # past weeks only - no active 2-month freeze to report
    until = (date.fromisoformat(latest) + timedelta(days=SNAPSHOT_DAYS)).isoformat() if latest else ""
    return out, latest, until


def start_values(scope: str) -> dict[str, dict[str, float]]:
    """{key: {period_start_iso: qty}} - each started period's forecast as it
    was when the period started (recorded from 2026-10-14 on)."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT key, period_start, start_qty FROM frozen_forecast WHERE scope = ? AND start_qty IS NOT NULL",
            (scope,),
        ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for k, p, q in rows:
        out.setdefault(k, {})[p] = q
    return out


def stored(scope: str, keys: set[str] | None = None) -> dict[str, dict[str, float]]:
    """{key: {period_start_iso: qty}} of every saved value in ``scope`` -
    locked (completed) periods and the latest live value of open (running,
    upcoming) ones, i.e. what the report last showed."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT key, period_start, qty FROM frozen_forecast WHERE scope = ?", (scope,),
        ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for k, p, q in rows:
        if keys is None or k in keys:
            out.setdefault(k, {})[p] = q
    return out


def locked(scope: str, keys: set[str] | None = None) -> dict[str, dict[str, float]]:
    """{key: {period_start_iso: qty}} of every locked value in ``scope``
    (optionally only for ``keys``)."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT key, period_start, qty FROM frozen_forecast WHERE scope = ? AND status = 'locked'",
            (scope,),
        ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for k, p, q in rows:
        if keys is None or k in keys:
            out.setdefault(k, {})[p] = q
    return out
