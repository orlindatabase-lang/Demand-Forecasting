"""
FOB (Free On Board) production tracking module.

ERP view: View_Dboard_Trans_Production_And_Job_Work_All_Data_For_BI

FOB Issue   → shown as table row
FOB Receive → used only as receive data on the matching FOB Issue row
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

import closed_lot

_ERP_URL   = "http://190.92.175.131:8080/DigiBizzErpApi/api/UnknownCallerApi/GetPowerBiReports"
_API_TOKEN = "aaaqqqwww111"
_COMPANY_YEAR_IDS: list[str] = [
    y.strip()
    for y in os.getenv("ERP_COMPANY_YEAR_IDS", "83").split(",")
    if y.strip()
]

_VIEW = "View_Dboard_Trans_Production_And_Job_Work_All_Data_For_BI"

ALL_PROCESSES    = ["FOB Issue"]
_FOB_ISSUE       = "FOB Issue"
_FOB_RECEIVE     = "FOB Receive"
_FOB_EXPECTED    = 21   # expected turnaround days

_RISK_SCORE: dict[str, int] = {"Delayed": 3, "At Risk": 2, "On Track": 1, "Completed": 0}


def _risk_level(age_days: int) -> str:
    if age_days > _FOB_EXPECTED:
        return "Delayed"
    if age_days > int(_FOB_EXPECTED * 0.75):
        return "At Risk"
    return "On Track"


# ── Cache ─────────────────────────────────────────────────────────────────── #
_RAW_ROWS:   list[dict] = []
_FETCHED_AT: float      = 0.0
_REFRESH_SECS: int      = 300
_REFRESHING:   bool     = False
_LOCK = threading.Lock()

_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "fob_rows.json"
_CACHE_MAX_AGE: int = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "rows": _RAW_ROWS}))
        print(f"[fob] cache saved ({len(_RAW_ROWS)} rows)", file=sys.stderr)
    except Exception as exc:
        print(f"[fob] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> None:
    global _RAW_ROWS, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return
    try:
        payload   = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age       = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            print(f"[fob] disk cache too old ({age / 3600:.1f}h), ignoring", file=sys.stderr)
            return
        with _LOCK:
            _RAW_ROWS   = payload["rows"]
            _FETCHED_AT = cached_at
        print(f"[fob] loaded {len(_RAW_ROWS)} rows from disk cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[fob] cache load failed: {exc}", file=sys.stderr)


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


# ── Build ─────────────────────────────────────────────────────────────────── #

def _build_rows(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []

    today  = date.today()
    cutoff = pd.Timestamp(today - timedelta(days=365))
    df     = df.copy()

    lot_col    = _find_col(df, "Lot No",   "LOT_NO",    "LOTNO")
    proc_col   = _find_col(df, "Process",  "PROCESS",   "PROCESS_NAME")
    design_col = _find_col(df, "Design No","DESIGN_NO", "DESIGN_NAME")
    vendor_col = _find_col(df, "Vendor",   "VENDOR",    "PARTY_NAME",  "EMPLOYEE_NAME")
    date_col   = _find_col(df, "Issue Date","ISSUE_DATE","ISSUEDATE",   "VOUCHER_DATE", "Voucher Date")
    pend_col   = _find_col(df, "BAL_PIECES","BAL PIECES","Pending","PENDING","PENDING_QTY","BALANCE_QTY")
    mtr_col    = _find_col(df, "BAL_MTR",  "BAL MTR",   "BALANCE_MTR", "BAL_METER")
    age_col    = _find_col(df, "Age",      "AGE",        "AGE_DAYS",   "AGEDAYS")

    print(f"[fob] columns: {list(df.columns)}", file=sys.stderr)

    if lot_col is None or proc_col is None:
        print(f"[fob] Missing Lot No or Process column", file=sys.stderr)
        return []

    erp_processes = df[proc_col].dropna().astype(str).str.strip().unique().tolist()
    print(f"[fob] ERP process names: {erp_processes}", file=sys.stderr)

    if pend_col: df[pend_col] = _num(df[pend_col])
    if mtr_col:  df[mtr_col]  = _num(df[mtr_col])
    if age_col:  df[age_col]  = _num(df[age_col])
    if date_col: df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    # ── Pass 1: collect receive qty + date from FOB Receive rows, keyed by
    # (lot_no, design) ────────────────────────────────────────────────────── #
    # Was keyed by lot_no alone — every FOB lot in this ERP view carries
    # several distinct design/section variants (100% of sampled lots had >1
    # design; e.g. lot LT-00030 has "008-01" (Top), "008-01-PLAZZO" (Bottom),
    # "008-01-DUPATTA" (Dupatta) as separate rows with their own piece
    # counts), so a lot-only key blended every design's qty/date together and
    # discarded which design was which beyond a comma-joined label.
    fob_rcv_qty:  dict[tuple, int] = {}
    fob_rcv_date: dict[tuple, str] = {}
    for _, r in df.iterrows():
        lot_no   = _s(r[lot_col])
        proc_raw = _s(r[proc_col])
        if not lot_no or not proc_raw:
            continue
        if _FOB_RECEIVE.lower() in proc_raw.lower():
            design_r = _s(r[design_col]) if design_col else ""
            rqty = int(r[pend_col]) if pend_col else 0
            key = (lot_no, design_r)
            fob_rcv_qty[key] = fob_rcv_qty.get(key, 0) + rqty
            row_dt = r[date_col] if date_col else pd.NaT
            if pd.notna(row_dt):
                d_str = _fmt(row_dt)
                if key not in fob_rcv_date or d_str > fob_rcv_date[key]:
                    fob_rcv_date[key] = d_str

    # ── Pass 2: accumulate FOB Issue rows by (lot_no, design) ─────────────── #
    accum: dict[tuple, dict] = {}
    for _, r in df.iterrows():
        lot_no   = _s(r[lot_col])
        proc_raw = _s(r[proc_col])
        if not lot_no or _FOB_ISSUE.lower() not in proc_raw.lower():
            continue

        row_dt = r[date_col] if date_col else pd.NaT

        # 1-year filter
        if pd.notna(row_dt):
            if row_dt < cutoff:
                continue
        else:
            age_chk = int(r[age_col]) if age_col else 0
            if age_chk > 365:
                continue

        bal_qty = int(r[pend_col])             if pend_col   else 0
        bal_mtr = round(float(r[mtr_col]), 2) if mtr_col    else 0.0
        design  = _s(r[design_col])   if design_col else ""
        vendor  = _s(r[vendor_col])   if vendor_col else ""

        key = (lot_no, design)
        if key not in accum:
            accum[key] = {
                "vendors": [],
                "balQty":  0,
                "balMtr":  0.0,
                "dates":   [],
            }
        g = accum[key]
        if vendor:
            g["vendors"].append(vendor)
        g["balQty"] += bal_qty
        g["balMtr"] += bal_mtr
        if pd.notna(row_dt):
            g["dates"].append(row_dt)

    # ── Emit one row per (lot, design) ─────────────────────────────────────── #
    rows: list[dict] = []
    for (lot_no, design), g in accum.items():
        earliest_dt = min(g["dates"]) if g["dates"] else None
        age_val = 0
        if earliest_dt is not None:
            try:
                age_val = max(0, (today - pd.to_datetime(earliest_dt).date()).days)
            except Exception:
                pass

        bal_qty   = g["balQty"]
        bal_mtr   = round(g["balMtr"], 2)
        rcv_qty   = fob_rcv_qty.get((lot_no, design), 0)
        rcv_date  = fob_rcv_date.get((lot_no, design), "")
        total_qty = bal_qty + rcv_qty           # original issued = remaining balance + received

        vendor = max(set(g["vendors"]), key=g["vendors"].count) if g["vendors"] else ""

        is_open = (rcv_qty / total_qty < 0.90) if total_qty > 0 else (bal_mtr > 0)

        # ERP's own authoritative per-lot status (closed_lot.py, the "Open Lot
        # Production" view) — takes precedence over the qty-ratio heuristic
        # above wherever it has a signal for this lot's FOB Issue voucher, in
        # either direction.
        authoritative = closed_lot.is_closed(lot_no, _FOB_ISSUE)
        if authoritative is not None:
            is_open = not authoritative

        risk    = "Completed" if not is_open else _risk_level(age_val)

        rows.append({
            "lotNo":        lot_no,
            "design":       design,
            "vendor":       vendor,
            "process":      _FOB_ISSUE,
            "issueQty":     total_qty,
            "issueDate":    _fmt(earliest_dt) if earliest_dt is not None else "",
            "receiveQty":   rcv_qty,
            "receiveDate":  rcv_date,
            "balMtr":       bal_mtr,
            "ageDays":      age_val,
            "riskLevel":    risk,
            "expectedDays": _FOB_EXPECTED,
            "lotStatus":    "Open" if is_open else "Completed",
        })

    rows.sort(key=lambda x: (_RISK_SCORE.get(x["riskLevel"], 0), x["ageDays"]), reverse=True)
    open_count = sum(1 for r in rows if r["lotStatus"] == "Open")
    print(f"[fob] {len(rows)} rows: {open_count} open, {len(rows) - open_count} completed", file=sys.stderr)
    return rows


# ── Fetch ─────────────────────────────────────────────────────────────────── #

def _fetch_view(company_year_id: str, timeout: int = 120) -> pd.DataFrame:
    headers = {
        "Report-Api-Token": _API_TOKEN,
        "ViewName": _VIEW,
        "CompanyYearId": str(company_year_id),
        "Accept": "application/json",
    }
    print(f"[fob] Fetching (year={company_year_id})...", file=sys.stderr)
    try:
        r = requests.get(_ERP_URL, headers=headers, timeout=timeout)
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        print(f"[fob] ✓ {len(df):,} rows × {df.shape[1]} cols", file=sys.stderr)
        return df
    except Exception as exc:
        print(f"[fob] fetch error: {exc}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_combined() -> pd.DataFrame:
    frames    = [_fetch_view(cy) for cy in _COMPANY_YEAR_IDS]
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)


# Raw FOB Issue/Receive rows from the last successful fetch, kept for
# delay_predict_jw.py to pool into the Job Work model (see its module
# docstring) — exposed via get_raw_rows() so job_work.py's refresh cycle
# doesn't have to re-fetch this same ~300k-row ERP view itself.
_RAW_FOB_ROWS: "pd.DataFrame" = pd.DataFrame()


def get_raw_rows() -> pd.DataFrame:
    """FOB Issue/Receive rows only, from the last successful ERP fetch."""
    with _LOCK:
        return _RAW_FOB_ROWS.copy()


def _merge_delay_scores(rows: list[dict]) -> None:
    """Attach AI delay scores computed by job_work.py's pooled model (FOB has
    too few lots of its own to train a model — see delay_predict_jw.py)."""
    for r in rows:
        r["delayProb"]   = None
        r["riskBand"]    = None
        r["alreadyLate"] = False
    try:
        import job_work
        scored = job_work.get_scored_lots()
        if not scored:
            print("[fob] no pooled delay scores available yet", file=sys.stderr)
            return
        matched = 0
        for r in rows:
            pred = scored.get(r["lotNo"])
            if pred:
                r["delayProb"]   = pred["delayProb"]
                r["riskBand"]    = pred["riskBand"]
                r["alreadyLate"] = pred["alreadyLate"]
                matched += 1
        print(f"[fob] delay scores merged ({matched} of {len(rows)} rows matched)", file=sys.stderr)
    except Exception as exc:
        print(f"[fob] delay score merge failed: {exc!r}", file=sys.stderr)


def refresh() -> None:
    global _RAW_ROWS, _FETCHED_AT, _REFRESHING, _RAW_FOB_ROWS
    _REFRESHING = True
    try:
        import debit_note
        df   = _fetch_combined()
        rows = _build_rows(df)
        if rows or not _RAW_ROWS:
            _merge_delay_scores(rows)
            debit_note.attach(rows)
            proc_col = _find_col(df, "Process", "PROCESS", "PROCESS_NAME") if not df.empty else None
            with _LOCK:
                _RAW_ROWS = rows
                if proc_col is not None:
                    mask = df[proc_col].astype(str).str.strip().str.lower().str.contains("fob", na=False)
                    _RAW_FOB_ROWS = df[mask].copy()
            _save_cache()
        else:
            # Empty result with good data already cached almost always means the
            # ERP fetch itself failed/timed out — keep serving the last good
            # rows instead of wiping the tracker until the next refresh succeeds.
            print(
                f"[fob] refresh returned 0 rows — keeping {len(_RAW_ROWS)} cached rows",
                file=sys.stderr,
            )
        _FETCHED_AT = time.time()
    except Exception as exc:
        print(f"[fob] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS or _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="fob-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def get_data(limit: int = 5000) -> dict:
    _maybe_refresh_bg()
    with _LOCK:
        all_rows = list(_RAW_ROWS)
    # Only open (still-running) lots are ever shown to the user — completed
    # lots stay in `all_rows` for the summary stats below but are dropped here.
    open_rows = [r for r in all_rows if r.get("lotStatus") == "Open"]

    return {
        "available": _FETCHED_AT > 0,
        "total":     len(all_rows),
        "filtered":  len(open_rows),
        "delayed":   sum(1 for r in all_rows if r.get("riskLevel") == "Delayed"),
        "atRisk":    sum(1 for r in all_rows if r.get("riskLevel") == "At Risk"),
        "onTrack":   sum(1 for r in all_rows if r.get("riskLevel") == "On Track"),
        "completed": sum(1 for r in all_rows if r.get("riskLevel") == "Completed"),
        "processes": ALL_PROCESSES,
        "counts":    {"FOB Issue": len(all_rows)},
        "asOf":      str(date.today()),
        "items":     open_rows[:limit],
    }


_load_cache()
_REFRESHING = True
threading.Thread(target=refresh, daemon=True, name="fob-init").start()
