"""
Daily stock history (2026-10-20, user-requested: "start saving daily stock
snapshots"). The ERP only gives TODAY's stock and WIP, so a week in which a
style sold little because it was out of stock looks exactly like a week
nobody wanted it - the forecast then learns demand that is too low,
especially for best-sellers. One row per SKU per day of its stock (finished
goods, PENDING_QTY_PIECES) and WIP (TOTAL_WIP_QTY) lets later work tell the
two apart (stock-out weeks treated as lost demand, not low demand).

Saved on every data refresh; the last refresh of a day wins. Nothing is
saved when the ERP stock or WIP view failed to load (their 0s would look
like stock-outs). Stored as a SQLite table in api/.cache/ - synced to Cloud
Storage like the other caches (cache_sync.py). Read-only for now: nothing in
the forecast uses it yet.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date
from pathlib import Path

_DB = Path(__file__).resolve().parent / ".cache" / "stock_history.sqlite"
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_daily (
    day       TEXT    NOT NULL,  -- ISO date the ERP stock was read
    sku       TEXT    NOT NULL,
    style     TEXT,              -- DESIGN_NO
    stock_qty INTEGER NOT NULL,  -- finished goods (ERP PENDING_QTY_PIECES)
    wip_qty   INTEGER NOT NULL,  -- open production (ERP TOTAL_WIP_QTY)
    PRIMARY KEY (day, sku)
)
"""


def _connect() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB)
    conn.execute(_SCHEMA)
    return conn


def save(rows: list[tuple[str, str, int, int]], day: date) -> int:
    """Save one day's snapshot: ``rows`` = (sku, style, stock_qty, wip_qty).
    Replaces that day's earlier snapshot, so the latest refresh of the day
    wins. Returns the number of SKUs saved."""
    iso = day.isoformat()
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM stock_daily WHERE day = ?", (iso,))
        conn.executemany(
            "INSERT INTO stock_daily (day, sku, style, stock_qty, wip_qty) VALUES (?, ?, ?, ?, ?)",
            [(iso, sku, style, int(stock), int(wip)) for sku, style, stock, wip in rows],
        )
    return len(rows)


def read(style: str = "", sku: str = "") -> list[dict]:
    """Stored snapshots, oldest day first, optionally for one style / SKU."""
    sql, args = "SELECT day, sku, style, stock_qty, wip_qty FROM stock_daily WHERE 1=1", []
    if style:
        sql += " AND style = ?"
        args.append(style)
    if sku:
        sql += " AND sku = ?"
        args.append(sku)
    with _LOCK, _connect() as conn:
        rows = conn.execute(sql + " ORDER BY day, sku", args).fetchall()
    return [dict(zip(("day", "sku", "style", "stock_qty", "wip_qty"), r)) for r in rows]


def days() -> list[str]:
    """Every day with a snapshot."""
    with _LOCK, _connect() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT day FROM stock_daily ORDER BY day")]
