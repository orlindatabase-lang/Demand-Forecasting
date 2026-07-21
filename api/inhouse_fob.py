"""
Inhouse & FOB production tracking module.

ERP view: View_Dboard_Trans_Production_And_Job_Work_All_Data_For_BI

Inhouse processes (shown as individual rows):
  Cutting, Stitching, Thread Cutting Store, General Store Out,
  Final Barcode Generator

FOB process (Issue/Receive pair — FOB Receive enriches FOB Issue rows):
  FOB Issue   → shown as table row
  FOB Receive → used only as receive data on FOB Issue rows, not a separate row
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from datetime import date, timedelta
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

_VIEW = "View_Dboard_Trans_Production_And_Job_Work_All_Data_For_BI"

# Inhouse process list (shown as table rows)
INHOUSE_PROCESSES = [
    "Cutting",
    "Stitching",
    "Thread Cutting Store",
    "General Store Out",
    "Final Barcode Generator",
]
_INHOUSE_PROC_SET = set(INHOUSE_PROCESSES)

# FOB process list
FOB_PROCESSES = ["FOB Issue"]
_FOB_PROC_SET = set(FOB_PROCESSES)

# Combined (used internally)
ALL_PROCESSES = INHOUSE_PROCESSES + FOB_PROCESSES

# FOB Receive enriches FOB Issue rows — not shown as a separate row
_FOB_RECEIVE = "FOB Receive"

# Expected calendar days to complete each process; used for risk scoring
_EXPECTED_DAYS: dict[str, int] = {
    "Cutting":                  5,
    "Stitching":               12,
    "Thread Cutting Store":     3,
    "General Store Out":        2,
    "Final Barcode Generator":  2,
    "FOB Issue":               21,
}

_RISK_SCORE: dict[str, int] = {"Delayed": 3, "At Risk": 2, "On Track": 1, "Completed": 0}


def _risk_level(age_days: int, expected_days: int) -> str:
    if age_days > expected_days:
        return "Delayed"
    if age_days > int(expected_days * 0.75):
        return "At Risk"
    return "On Track"


# ── In-memory + disk cache ───────────────────────────────────────────────── #
_RAW_ROWS:   list[dict] = []
_FETCHED_AT: float      = 0.0
_REFRESH_SECS: int      = 300
_REFRESHING:   bool     = False
_LOCK = threading.Lock()

_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "inhouse_fob_rows.json"
_CACHE_MAX_AGE: int = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "rows": _RAW_ROWS}))
        print(f"[ihf] cache saved ({len(_RAW_ROWS)} rows)", file=sys.stderr)
    except Exception as exc:
        print(f"[ihf] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> None:
    global _RAW_ROWS, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return
    try:
        payload   = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age       = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            print(f"[ihf] disk cache too old ({age / 3600:.1f}h), ignoring", file=sys.stderr)
            return
        with _LOCK:
            _RAW_ROWS   = payload["rows"]
            _FETCHED_AT = cached_at
        print(f"[ihf] loaded {len(_RAW_ROWS)} rows from disk cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[ihf] cache load failed: {exc}", file=sys.stderr)


# ── Helpers ──────────────────────────────────────────────────────────────── #

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


def _num(series: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _s(val) -> str:
    """NaN-safe str() + strip. A blank source cell is a real NaN float, and
    plain str(nan) produces the literal text "nan" — which then renders as
    the word "nan" in the UI instead of an empty cell."""
    try:
        if val is None or pd.isna(val):
            return ""
    except Exception:
        pass
    return str(val).strip()


def _find_col(df: pd.DataFrame, *candidates: str) -> str | None:
    cols_upper = {c.upper().replace(" ", "_"): c for c in df.columns}
    for cand in candidates:
        key = cand.upper().replace(" ", "_")
        if key in cols_upper:
            return cols_upper[key]
    return None


# ── Build rows ────────────────────────────────────────────────────────────── #

def _build_rows(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []

    today  = date.today()
    cutoff = pd.Timestamp(today - timedelta(days=365))
    df     = df.copy()

    lot_col     = _find_col(df, "Lot No",      "LOT_NO",       "LOTNO")
    proc_col    = _find_col(df, "Process",     "PROCESS",      "PROCESS_NAME")
    design_col  = _find_col(df, "Design No",   "DESIGN_NO",    "DESIGN_NAME")
    section_col = _find_col(df, "Section",     "SECTION")
    vendor_col  = _find_col(df, "Vendor",      "VENDOR",       "PARTY_NAME",   "EMPLOYEE_NAME")
    iqty_col    = _find_col(df, "Issue Qty",   "ISSUE_QTY",    "ISSUEQTY",     "QTY_SIZE",  "QTY")
    date_col    = _find_col(df, "Issue Date",  "ISSUE_DATE",   "ISSUEDATE",    "VOUCHER_DATE", "Voucher Date")
    rqty_col    = _find_col(df, "Receive Qty", "RECEIVE_QTY",  "RECEIVEQTY",   "GRN_QTY",   "GRN Qty")
    rdate_col   = _find_col(df, "Receive Date","RECEIVE_DATE", "RECEIVEDATE",  "GRN_DATE",  "GRN Date")
    pend_col    = _find_col(df, "Pending",     "PENDING",      "PENDING_QTY",  "PENDINGQTY")
    age_col     = _find_col(df, "Age",         "AGE",          "AGE_DAYS",     "AGEDAYS")

    print(f"[ihf] ERP columns: {list(df.columns)}", file=sys.stderr)

    if lot_col is None or proc_col is None:
        print(f"[ihf] Missing Lot No or Process column", file=sys.stderr)
        return []

    if iqty_col:  df[iqty_col]  = _num(df[iqty_col])
    if rqty_col:  df[rqty_col]  = _num(df[rqty_col])
    if pend_col:  df[pend_col]  = _num(df[pend_col])
    if age_col:   df[age_col]   = _num(df[age_col])
    if date_col:  df[date_col]  = pd.to_datetime(df[date_col], errors="coerce")
    if rdate_col and rdate_col != date_col:
        df[rdate_col] = pd.to_datetime(df[rdate_col], errors="coerce")

    # ── Pass 1: build FOB Receive lookup (qty + date) by lot ─────────────── #
    fob_rcv_qty:  dict[str, int] = {}
    fob_rcv_date: dict[str, str] = {}
    fob_iss_qty:  dict[str, int] = {}

    for _, r in df.iterrows():
        lot_no = _s(r[lot_col])
        proc   = _s(r[proc_col])
        if not lot_no or not proc:
            continue
        if proc == "FOB Issue":
            fob_iss_qty[lot_no] = fob_iss_qty.get(lot_no, 0) + (int(r[iqty_col]) if iqty_col else 0)
        elif proc == _FOB_RECEIVE:
            rqty = 0
            if rqty_col:
                rqty = int(r[rqty_col])
            elif iqty_col:
                rqty = int(r[iqty_col])
            fob_rcv_qty[lot_no] = fob_rcv_qty.get(lot_no, 0) + rqty
            # Receive date: try dedicated column, fall back to VOUCHER_DATE
            grn_dt = pd.NaT
            if rdate_col and pd.notna(r.get(rdate_col, pd.NaT)):
                grn_dt = r[rdate_col]
            elif date_col and pd.notna(r.get(date_col, pd.NaT)):
                grn_dt = r[date_col]
            if pd.notna(grn_dt):
                d_str = _fmt(grn_dt)
                if d_str:
                    fob_rcv_date[lot_no] = d_str

    fob_open_lots: set[str] = {
        lot for lot in fob_iss_qty
        if lot not in fob_rcv_qty or fob_iss_qty[lot] > fob_rcv_qty.get(lot, 0)
    }

    # ── Pass 2: emit one row per process event (skip FOB Receive) ─────────── #
    rows: list[dict] = []
    for _, r in df.iterrows():
        lot_no = _s(r[lot_col])
        proc   = _s(r[proc_col])
        if not lot_no or not proc:
            continue
        if proc == _FOB_RECEIVE:
            continue   # enrichment only, not a table row

        # Row date (VOUCHER_DATE = transaction date for this row)
        row_dt = r[date_col] if date_col else pd.NaT

        # 1-year filter
        if pd.notna(row_dt):
            if row_dt < cutoff:
                continue
        else:
            age_chk = int(r[age_col]) if age_col else 0
            if age_chk > 365:
                continue

        # Age
        age_val = int(r[age_col]) if age_col else 0
        if age_val == 0 and pd.notna(row_dt):
            try:
                age_val = max(0, (today - pd.to_datetime(row_dt).date()).days)
            except Exception:
                pass

        expected = _EXPECTED_DAYS.get(proc, 7)

        # Receive data
        row_rqty  = 0
        row_rdate = ""
        if proc == "FOB Issue":
            # Receive comes from FOB Receive lookup
            if lot_no in fob_rcv_qty:
                row_rqty  = fob_rcv_qty[lot_no]
                row_rdate = fob_rcv_date.get(lot_no, "")
            is_open = lot_no in fob_open_lots
        else:
            # Inhouse: use row's own receive columns if present
            if rqty_col:
                row_rqty = int(r[rqty_col])
            if rdate_col and pd.notna(r.get(rdate_col, pd.NaT)):
                row_rdate = _fmt(r[rdate_col])
            elif date_col and rdate_col is None and row_rqty > 0 and pd.notna(row_dt):
                row_rdate = _fmt(row_dt)
            # Inhouse row is "open" if no receive qty
            is_open = row_rqty == 0

        # Risk
        if not is_open:
            risk = "Completed"
        else:
            risk = _risk_level(age_val, expected)

        rows.append({
            "lotNo":        lot_no,
            "design":       _s(r[design_col])  if design_col  else "",
            "section":      _s(r[section_col]) if section_col else "",
            "vendor":       _s(r[vendor_col])  if vendor_col  else "",
            "process":      proc,
            "issueQty":     int(r[iqty_col]) if iqty_col else 0,
            "issueDate":    _fmt(row_dt) if pd.notna(row_dt) else "",
            "receiveQty":   row_rqty,
            "receiveDate":  row_rdate,
            "pending":      int(r[pend_col]) if pend_col else 0,
            "ageDays":      age_val,
            "riskLevel":    risk,
            "expectedDays": expected,
            "lotStatus":    "Open" if is_open else "Completed",
        })

    rows.sort(key=lambda x: (_RISK_SCORE.get(x["riskLevel"], 0), x["ageDays"]), reverse=True)
    open_count = sum(1 for r in rows if r["lotStatus"] == "Open")
    print(
        f"[ihf] {len(rows)} rows (last 365 days): {open_count} open, "
        f"{len(rows) - open_count} completed",
        file=sys.stderr,
    )
    return rows


# ── Fetch + cache ─────────────────────────────────────────────────────────── #

def _fetch_view(company_year_id: str, timeout: int = 120) -> pd.DataFrame:
    headers = {
        "Report-Api-Token": _API_TOKEN,
        "ViewName": _VIEW,
        "CompanyYearId": str(company_year_id),
        "Accept": "application/json",
    }
    print(f"[ihf] Fetching '{_VIEW}' (year={company_year_id})...", file=sys.stderr)
    try:
        r = requests.get(_ERP_URL, headers=headers, timeout=timeout)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
        print(f"[ihf] ERP error: {exc}", file=sys.stderr)
        return pd.DataFrame()
    try:
        df = pd.DataFrame(r.json())
        print(f"[ihf] ✓ year={company_year_id} {len(df):,} rows × {df.shape[1]} cols", file=sys.stderr)
        return df
    except ValueError:
        print(f"[ihf] Non-JSON response:\n{r.text[:300]}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_combined() -> pd.DataFrame:
    frames    = [_fetch_view(cy) for cy in _COMPANY_YEAR_IDS]
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)


def refresh() -> None:
    global _RAW_ROWS, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        df   = _fetch_combined()
        rows = _build_rows(df)
        with _LOCK:
            _RAW_ROWS   = rows
            _FETCHED_AT = time.time()
        _save_cache()
    except Exception as exc:
        print(f"[ihf] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS:
        return
    if _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="ihf-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def get_data(limit: int = 5000, process: str | None = None, category: str | None = None) -> dict:
    """Return rows filtered by category ('inhouse' | 'fob') and optionally by process."""
    _maybe_refresh_bg()
    with _LOCK:
        all_rows = list(_RAW_ROWS)

    # Category filter determines which process set is in scope
    if category == "inhouse":
        base      = [r for r in all_rows if r["process"] in _INHOUSE_PROC_SET]
        proc_list = INHOUSE_PROCESSES
    elif category == "fob":
        base      = [r for r in all_rows if r["process"] in _FOB_PROC_SET]
        proc_list = FOB_PROCESSES
    else:
        base      = all_rows
        proc_list = ALL_PROCESSES

    # Optional single-process drill-down within the category
    filtered = (
        [r for r in base if r["process"] == process]
        if process and process != "All"
        else base
    )

    counts: dict[str, int] = {}
    for r in base:
        counts[r["process"]] = counts.get(r["process"], 0) + 1

    delayed   = sum(1 for r in base if r.get("riskLevel") == "Delayed")
    at_risk   = sum(1 for r in base if r.get("riskLevel") == "At Risk")
    on_track  = sum(1 for r in base if r.get("riskLevel") == "On Track")
    completed = sum(1 for r in base if r.get("riskLevel") == "Completed")

    return {
        "available": _FETCHED_AT > 0,
        "total":     len(base),
        "filtered":  len(filtered),
        "delayed":   delayed,
        "atRisk":    at_risk,
        "onTrack":   on_track,
        "completed": completed,
        "processes": proc_list,
        "counts":    counts,
        "asOf":      str(date.today()),
        "items":     filtered[:limit],
    }


_load_cache()
_REFRESHING = True
threading.Thread(target=refresh, daemon=True, name="ihf-init").start()
