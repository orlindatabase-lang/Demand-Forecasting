"""
Closed-lot status module.

ERP view: View_Dboard_Trans_Open_Lot_Production_Data_Rdp
(served from a separate reporting host — not the main ERP_URL used by the
other trackers)

One row per (lot, process[, article/design line]). STATUS is the ERP's own
authoritative "is this voucher done" flag — "Completed" / "In-Progress" / null.
STATUS is only ever populated for the *issue*-side processes (Purchase Order,
Cutting Issue, Cut to Pack Issue, Cut to Stitching Issue, Only Stitching Issue,
Fab. Job Issue (Emb.) new, FOB Issue) — receive/GRN/dispatch rows never carry
a STATUS, so this view has no closure signal for those processes.

Exposes is_closed(lot_no, process) -> bool | None:
  True/False = authoritative answer for that (lot, process).
  None       = no data here; caller should fall back to its own heuristic.

A (lot, process) is only reported True when EVERY row for that pair has
STATUS == "Completed" — a handful of lots (~0.1-0.6%) have multiple article
lines mid-flight at once, and treating any single "Completed" line as proof
the whole lot+process is done would be wrong.
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from pathlib import Path

import pandas as pd
import requests

# Different host from the other trackers' _ERP_URL — this view is served
# from a separate reporting box, not the main DigiBizz instance.
_ERP_URL   = "http://195.250.31.101/DigiBizzErpApi/api/UnknownCallerApi/GetPowerBiReports"
_API_TOKEN = "aaaqqqwww111"
_COMPANY_YEAR_IDS: list[str] = [
    y.strip()
    for y in os.getenv("ERP_COMPANY_YEAR_IDS", "83").split(",")
    if y.strip()
]
_VIEW = "View_Dboard_Trans_Open_Lot_Production_Data_Rdp"

# ── Cache ─────────────────────────────────────────────────────────────────── #
# (lot_no, process) -> True (all rows Completed) / False (has an open row)
_STATUS: dict[tuple[str, str], bool] = {}
_FETCHED_AT: float = 0.0
_REFRESH_SECS = 300
_REFRESHING   = False
_LOCK = threading.Lock()

_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "closed_lot_status.json"
_CACHE_MAX_AGE = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))


def _build(df: pd.DataFrame) -> dict[tuple[str, str], bool]:
    if df.empty or "LOT_NO" not in df.columns or "PROCESS" not in df.columns:
        return {}

    df = df.copy()
    df["LOT_NO"]  = df["LOT_NO"].astype(str).str.strip()
    df["PROCESS"] = df["PROCESS"].astype(str).str.strip()
    df["STATUS"]  = df["STATUS"].astype(str).str.strip() if "STATUS" in df.columns else ""

    result: dict[tuple[str, str], bool] = {}
    for (lot_no, proc), grp in df.groupby(["LOT_NO", "PROCESS"], sort=False):
        statuses = {s for s in grp["STATUS"] if s and s.lower() not in ("nan", "none")}
        if not statuses:
            continue   # no STATUS data for this (lot, process) — no signal
        result[(lot_no, proc)] = statuses == {"Completed"}

    closed = sum(1 for v in result.values() if v)
    print(f"[closed_lot] {len(result)} (lot, process) pairs with a status signal "
          f"({closed} closed, {len(result) - closed} open)", file=sys.stderr)
    return result


# ── Fetch ─────────────────────────────────────────────────────────────────── #

def _fetch_view(company_year_id: str, timeout: int = 120) -> pd.DataFrame:
    headers = {
        "Report-Api-Token": _API_TOKEN,
        "ViewName": _VIEW,
        "CompanyYearId": str(company_year_id),
        "Accept": "application/json",
    }
    try:
        r = requests.get(_ERP_URL, headers=headers, timeout=timeout)
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        print(f"[closed_lot] ✓ year={company_year_id} {len(df):,} rows", file=sys.stderr)
        return df
    except Exception as exc:
        print(f"[closed_lot] fetch error: {exc}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_combined() -> pd.DataFrame:
    frames    = [_fetch_view(cy) for cy in _COMPANY_YEAR_IDS]
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # JSON keys must be strings — encode the (lot, process) tuple key.
        serial = {f"{lot}␟{proc}": v for (lot, proc), v in _STATUS.items()}
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "status": serial}))
        print(f"[closed_lot] cache saved ({len(_STATUS)} pairs)", file=sys.stderr)
    except Exception as exc:
        print(f"[closed_lot] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> None:
    global _STATUS, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return
    try:
        payload   = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age       = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            return
        with _LOCK:
            _STATUS = {
                tuple(k.split("␟", 1)): v
                for k, v in payload.get("status", {}).items()
                if "␟" in k
            }
            _FETCHED_AT = cached_at
        print(f"[closed_lot] loaded {len(_STATUS)} pairs from cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[closed_lot] cache load failed: {exc}", file=sys.stderr)


def refresh() -> None:
    global _STATUS, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        result = _build(_fetch_combined())
        if result or not _STATUS:
            with _LOCK:
                _STATUS = result
            _save_cache()
        else:
            print(f"[closed_lot] refresh returned 0 pairs — keeping {len(_STATUS)} cached", file=sys.stderr)
        _FETCHED_AT = time.time()
    except Exception as exc:
        print(f"[closed_lot] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS or _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="closed-lot-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def is_closed(lot_no: str, process: str) -> bool | None:
    """True/False = authoritative ERP status for this (lot, process).

    None = this view has no STATUS signal for that process (or that lot isn't
    in it at all) — caller should fall back to its own heuristic.
    """
    _maybe_refresh_bg()
    with _LOCK:
        return _STATUS.get((str(lot_no).strip(), str(process).strip()))


_load_cache()
_REFRESHING = True
threading.Thread(target=refresh, daemon=True, name="closed-lot-init").start()
