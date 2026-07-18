"""
Debit Note module.

ERP view: View_Dboard_Trans_Debit_Note_Data_For_BI

Fetches all debit notes and exposes:
  - by_lot()  → dict[lot_no, list[debit_note_dict]]
  - summary() → total debit note stats
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from datetime import date
from pathlib import Path

import pandas as pd
import requests

_ERP_URL   = "http://190.92.175.131:8080/DigiBizzErpApi/api/UnknownCallerApi/GetPowerBiReports"
_API_TOKEN = "aaaqqqwww111"
_COMPANY_YEAR_IDS: list[str] = [
    y.strip()
    for y in os.getenv("ERP_COMPANY_YEAR_IDS", "83").split(",")
    if y.strip()
]
_VIEW = "View_Dboard_Trans_Debit_Note_Data_For_BI"

# ── Cache ─────────────────────────────────────────────────────────────────── #
_BY_LOT:     dict[str, list[dict]] = {}   # lot_no → [debit_note, ...]
_FETCHED_AT: float = 0.0
_REFRESH_SECS = 300
_REFRESHING   = False
_LOCK = threading.Lock()

_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "debit_notes.json"
_CACHE_MAX_AGE = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))


def _fmt(val) -> str:
    try:
        if val is None or pd.isna(val):
            return ""
    except Exception:
        pass
    try:
        dt = pd.to_datetime(val)
        return f"{dt.day} {dt.strftime('%b')} {dt.year}"
    except Exception:
        return str(val)


def _find_col(df: pd.DataFrame, *candidates: str) -> str | None:
    cols_upper = {c.upper().replace(" ", "_"): c for c in df.columns}
    for cand in candidates:
        key = cand.upper().replace(" ", "_")
        if key in cols_upper:
            return cols_upper[key]
    return None


def _build(df: pd.DataFrame) -> dict[str, list[dict]]:
    if df.empty:
        return {}

    lot_col    = _find_col(df, "LOT_NO",       "Lot No")
    date_col   = _find_col(df, "VOUCHER_DATE",  "Voucher Date")
    vno_col    = _find_col(df, "VOUCHER_NO",    "Voucher No")
    party_col  = _find_col(df, "PAREY_NAME",    "PARTY_NAME",   "Party Name")
    article_col= _find_col(df, "ARTICLE",       "Article")
    qty_col    = _find_col(df, "QTY",           "Qty")
    rate_col   = _find_col(df, "RATE",          "Rate")
    amt_col    = _find_col(df, "AMOUNT",        "Amount")
    net_col    = _find_col(df, "NET_AMOUNT",    "Net Amount")

    print(f"[dn] columns: {list(df.columns)}", file=sys.stderr)

    if lot_col is None:
        print("[dn] LOT_NO column not found", file=sys.stderr)
        return {}

    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    by_lot: dict[str, list[dict]] = {}

    for _, r in df.iterrows():
        lot_no = str(r[lot_col]).strip() if lot_col else ""
        if not lot_no or lot_no.lower() in ("nan", "none", ""):
            continue

        entry = {
            "voucherNo":   str(r[vno_col]).strip()     if vno_col    else "",
            "voucherDate": _fmt(r[date_col])            if date_col   else "",
            "partyName":   str(r[party_col]).strip()    if party_col  else "",
            "article":     str(r[article_col]).strip()  if article_col else "",
            "qty":         round(float(r[qty_col]),  2) if qty_col    else 0.0,
            "rate":        round(float(r[rate_col]), 2) if rate_col   else 0.0,
            "amount":      round(float(r[amt_col]),  2) if amt_col    else 0.0,
            "netAmount":   round(float(r[net_col]),  2) if net_col    else 0.0,
        }
        by_lot.setdefault(lot_no, []).append(entry)

    print(f"[dn] {sum(len(v) for v in by_lot.values())} debit note rows across {len(by_lot)} lots", file=sys.stderr)
    return by_lot


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
        print(f"[dn] ✓ {len(df):,} rows", file=sys.stderr)
        return df
    except Exception as exc:
        print(f"[dn] fetch error: {exc}", file=sys.stderr)
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
        # Convert to serialisable form: {lot_no: [entries...]}
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "byLot": _BY_LOT}))
        print(f"[dn] cache saved ({len(_BY_LOT)} lots)", file=sys.stderr)
    except Exception as exc:
        print(f"[dn] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> None:
    global _BY_LOT, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return
    try:
        payload   = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age       = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            return
        with _LOCK:
            _BY_LOT     = payload.get("byLot", {})
            _FETCHED_AT = cached_at
        print(f"[dn] loaded {len(_BY_LOT)} lots from cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[dn] cache load failed: {exc}", file=sys.stderr)


def refresh() -> None:
    global _BY_LOT, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        result = _build(_fetch_combined())
        with _LOCK:
            _BY_LOT     = result
            _FETCHED_AT = time.time()
        _save_cache()
    except Exception as exc:
        print(f"[dn] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS or _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="dn-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def by_lot() -> dict[str, list[dict]]:
    """Return the full lot → debit-notes mapping (triggers background refresh if stale)."""
    _maybe_refresh_bg()
    with _LOCK:
        return dict(_BY_LOT)


def attach(rows: list[dict]) -> None:
    """
    In-place: add debitNotes, debitNoteCount, debitNoteAmount to each row dict.
    Keyed by row["lotNo"].
    """
    _maybe_refresh_bg()
    with _LOCK:
        lookup = dict(_BY_LOT)

    for row in rows:
        lot_no = row.get("lotNo", "")
        dns    = lookup.get(lot_no, [])
        row["debitNotes"]       = dns
        row["debitNoteCount"]   = len(dns)
        row["debitNoteAmount"]  = round(sum(d["netAmount"] for d in dns), 2)


_load_cache()
_REFRESHING = True
threading.Thread(target=refresh, daemon=True, name="dn-init").start()
