"""
Frozen forecasts for completed report weeks (2026-09-29, user-requested:
"completed weeks and months forecast numbers must not change every time the
data is refreshed - only the upcoming weeks' data changes").

Every forecast cell the Weekly Sales Report (and its channel drill-downs)
shows is keyed by (scope, key, period_start) - e.g. ("grid:style", "417-03",
"2026-09-24"). While a period is still upcoming, its value is re-saved on
every data refresh (status 'open'). Once the period has started (2026-10-06,
user-requested: the running week's forecast must be frozen too, not only
completed weeks), the value last saved before it started is locked (status
'locked') and returned from then on, regardless of what a later retrain or
refresh would compute for it. A period first seen only after it had already
started (e.g. history from before this module existed) is locked at the
value computed at that moment.

Stored as a SQLite table in api/.cache/ (a local cache, like weekly_log.py).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date
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
    PRIMARY KEY (scope, key, period_start)
)
"""


def _connect() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB)
    conn.execute(_SCHEMA)
    return conn


def _started(iso: str, end: date | None, today: date) -> bool:
    """Has the period starting on ``iso`` begun as of ``today``?"""
    try:
        return date.fromisoformat(iso) <= today
    except ValueError:
        return end is not None and end < today


def apply(
    scope: str,
    values: dict[str, dict[str, float]],
    period_end: dict[str, date],
    today: date | None = None,
) -> dict[str, dict[str, float]]:
    """Return ``values`` ({key: {period_start_iso: qty}}) with every started
    period (start date <= ``today``, i.e. running or completed) replaced by
    its locked value, saving upcoming periods' current values and locking
    newly started ones on the way. ``period_end`` is kept for callers; a
    period is also treated as started once its end date has passed."""
    today = today or date.today()
    out: dict[str, dict[str, float]] = {}
    writes: list[tuple] = []
    with _LOCK, _connect() as conn:
        stored = {
            (k, p): (q, s)
            for k, p, q, s in conn.execute(
                "SELECT key, period_start, qty, status FROM frozen_forecast WHERE scope = ?", (scope,))
        }
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
                if _started(iso, end, today):
                    if prev is None:
                        writes.append((scope, key, iso, qty, "locked", stamp))
                    elif prev[1] == "open":
                        qty = prev[0]
                        writes.append((scope, key, iso, qty, "locked", stamp))
                    else:
                        qty = prev[0]
                elif prev is None or prev[0] != qty:
                    writes.append((scope, key, iso, qty, "open", stamp))
                row[iso] = qty
            # A started period locked earlier stays even if today's live
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
