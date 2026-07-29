"""
Inhouse production tracking module.

ERP view: View_Dboard_Trans_Production_And_Job_Work_All_Data_For_BI

Processes shown (each row is an independent production event):
  Cutting, Stitching, Thread Cutting Store, General Store,
  Final Barcode Generator
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from collections import Counter
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

ALL_PROCESSES = [
    "Cutting",
    "Stitching",
    "Thread Cutting Store",
    "General Store",
    "Final Barcode Generator",
]
# Pipeline position of each process — used to infer completion of an earlier
# stage when a lot already has a row at a later one (see _build_rows).
_PROC_ORDER: dict[str, int] = {p: i for i, p in enumerate(ALL_PROCESSES)}
# Lower-cased lookup for case-insensitive matching against ERP process names.
# Maps lowercase ERP value → canonical display name.
_PROC_LOWER: dict[str, str] = {p.lower(): p for p in ALL_PROCESSES}
# Sorted longest-first so "thread cutting store" matches before "cutting"
_PROC_LOWER_SORTED: list[str] = sorted(_PROC_LOWER.keys(), key=len, reverse=True)
# Receive-process lookup: maps "stitching receive" → "Stitching", etc.
_RECV_LOWER: dict[str, str] = {(p.lower() + " receive"): p for p in ALL_PROCESSES}
# "General Store" is the odd one out: its receive/completion voucher isn't
# named "General Store Receive" like every other stage — the ERP calls it
# "General Store Out". Issue qty comes off "General Store" rows, receive qty
# off "General Store Out" rows.
_RECV_LOWER["general store out"] = "General Store"


def _is_receive_proc(proc_lc: str) -> str | None:
    """Return canonical base-process name if this is a receive row, else None."""
    if proc_lc in _RECV_LOWER:
        return _RECV_LOWER[proc_lc]
    # Partial: ends with "receive" and the prefix matches a canonical process
    if proc_lc.endswith(" receive") or proc_lc.endswith("receive"):
        base = proc_lc.rsplit("receive", 1)[0].strip()
        for canon_lc in _PROC_LOWER_SORTED:
            if canon_lc == base or canon_lc in base:
                return _PROC_LOWER[canon_lc]
    return None


def _match_proc(proc_raw: str) -> str | None:
    """Return canonical process name for Issue rows; returns None for Receive rows."""
    lc = proc_raw.lower().strip()
    # Skip receive rows — they are handled in Pass 1, not emitted as table rows
    if _is_receive_proc(lc) is not None:
        return None
    if lc in _PROC_LOWER:
        return _PROC_LOWER[lc]
    for canon_lc in _PROC_LOWER_SORTED:
        if canon_lc in lc:
            return _PROC_LOWER[canon_lc]
    return None

# closed_lot.py's "Open Lot Production" view only carries a STATUS signal for
# the Cutting stage of this tracker (raw ERP process name "Cutting Issue") —
# Stitching/Thread Cutting Store/General Store/Final Barcode Generator
# have no coverage there, so they keep using the heuristic below untouched.
_CLOSED_LOT_PROCESS_MAP: dict[str, str] = {"Cutting": "Cutting Issue"}

_EXPECTED_DAYS: dict[str, int] = {
    "Cutting":                  5,
    "Stitching":               10,
    "Thread Cutting Store":     2,
    "General Store":            7,
    "Final Barcode Generator":  2,
}
_RISK_SCORE: dict[str, int] = {"Delayed": 3, "At Risk": 2, "On Track": 1, "Completed": 0}

# Populated by _build_rows; returned by get_data() for column diagnostics.
_DETECTED_COLS: dict[str, str | None] = {}
_ALL_ERP_COLS:  list[str]             = []
_RECEIVE_STATS: dict[str, int]        = {}   # counts receive rows found per base process


def _risk_level(age_days: int, expected_days: int) -> str:
    if age_days > expected_days:
        return "Delayed"
    if age_days > int(expected_days * 0.75):
        return "At Risk"
    return "On Track"


def _section_from_design(design_name: str) -> str:
    """Infer Top / Bottom / Dupatta from the DESIGN_NAME suffix.

    This ERP view has no separate Section column (unlike job_work/PO), but
    design names carry the same suffix convention embroidery.py already
    parses, e.g. "038-02-DUPATTA", "417-03-PANT".
    """
    d = str(design_name).upper()
    if "DUPATTA" in d:
        return "Dupatta"
    for kw in ("PLAZZO", "PALAZZO", "BOTTOM", "PANT", "TROUSER", "SKIRT"):
        if kw in d:
            return "Bottom"
    return "Top"


_DESIGN_SECTION_SUFFIXES = ("-DUPATTA", "-PLAZZO", "-PALAZZO", "-BOTTOM", "-PANT", "-TROUSER", "-SKIRT", "-BLOUSE")


def _base_design(design: str) -> str:
    """Strip a known section suffix to recover the base design number, e.g.
    "417-03-PANT" -> "417-03" — used to match a lot's design against the
    SKU Production Plan's designNo for the bottleneck demand-priority sort."""
    d = str(design).strip().upper()
    for sfx in _DESIGN_SECTION_SUFFIXES:
        if d.endswith(sfx):
            return d[: -len(sfx)]
    return d


# ── Cache ─────────────────────────────────────────────────────────────────── #
_RAW_ROWS:   list[dict] = []
_FETCHED_AT: float      = 0.0
_REFRESH_SECS: int      = 300
_REFRESHING:   bool     = False
_LOCK = threading.Lock()

_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "inhouse_rows.json"
_CACHE_MAX_AGE: int = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "rows": _RAW_ROWS}))
        print(f"[inhouse] cache saved ({len(_RAW_ROWS)} rows)", file=sys.stderr)
    except Exception as exc:
        print(f"[inhouse] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> None:
    global _RAW_ROWS, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return
    try:
        payload   = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age       = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            print(f"[inhouse] disk cache too old ({age / 3600:.1f}h), ignoring", file=sys.stderr)
            return
        with _LOCK:
            _RAW_ROWS   = payload["rows"]
            _FETCHED_AT = cached_at
        print(f"[inhouse] loaded {len(_RAW_ROWS)} rows from disk cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[inhouse] cache load failed: {exc}", file=sys.stderr)


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

    lot_col     = _find_col(df, "Lot No",    "LOT_NO",     "LOTNO")
    proc_col    = _find_col(df, "Process",   "PROCESS",    "PROCESS_NAME")
    design_col  = _find_col(df, "Design No", "DESIGN_NO",  "DESIGN_NAME")
    section_col = _find_col(df, "Section",   "SECTION")
    vendor_col  = _find_col(df, "Vendor",    "VENDOR",     "PARTY_NAME", "EMPLOYEE_NAME")
    date_col    = _find_col(df, "Issue Date","ISSUE_DATE", "ISSUEDATE",  "VOUCHER_DATE", "Voucher Date")
    pend_col    = _find_col(df, "BAL_PIECES","BAL PIECES", "Pending",    "PENDING",      "PENDING_QTY", "PENDINGQTY", "BALANCE_QTY")
    mtr_col     = _find_col(df, "BAL_MTR",   "BAL MTR",   "BALANCE_MTR","BAL_METER")
    age_col     = _find_col(df, "Age",       "AGE",        "AGE_DAYS",   "AGEDAYS")

    print(f"[inhouse] columns: {list(df.columns)}", file=sys.stderr)

    _ALL_ERP_COLS.clear()
    _ALL_ERP_COLS.extend(list(df.columns))
    _DETECTED_COLS.update({
        "lot": lot_col, "proc": proc_col, "design": design_col,
        "section": section_col, "vendor": vendor_col, "date": date_col,
        "pending": pend_col, "mtr": mtr_col, "age": age_col,
    })
    print(f"[inhouse] detected cols: {_DETECTED_COLS}", file=sys.stderr)

    if proc_col is None or design_col is None:
        print("[inhouse] Missing Process or Design column", file=sys.stderr)
        return []

    erp_processes = df[proc_col].dropna().astype(str).str.strip().unique().tolist()
    print(f"[inhouse] ERP process names: {erp_processes}", file=sys.stderr)

    if pend_col: df[pend_col] = _num(df[pend_col])
    if mtr_col:  df[mtr_col]  = _num(df[mtr_col])
    if age_col:  df[age_col]  = _num(df[age_col])
    if date_col: df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    # ── Pass 1: collect receive qty + date, keyed by (lot_no, base_process,
    # design) ──────────────────────────────────────────────────────────────
    # Was keyed by (lot_no, base_process) only — a single lot commonly carries
    # SEVERAL distinct design/section variants through the same process at
    # once (e.g. lot LT-00008's "Cut to Stitching Issue" covers "058-01" (Top),
    # "058-01-PLAZZO" (Bottom) AND "058-01-DUPATTA" (Dupatta) as three separate
    # ERP rows) — every receive-side voucher carries its own DESIGN_NAME too,
    # so without design in the key, one design's receive qty/date silently
    # bled onto every other design sharing that lot+process. Keying by the
    # actual lot number (present on every row of this view) already fixed a
    # cross-LOT version of this same bug; this closes the cross-DESIGN gap.
    rcv_qty_map:  dict[tuple, int] = {}
    rcv_date_map: dict[tuple, str] = {}
    recv_counter: dict[str, int]   = {}
    all_proc_names: set[str]       = set()
    for _, r in df.iterrows():
        lot_no_r    = _s(r[lot_col]) if lot_col else ""
        proc_raw    = _s(r[proc_col])
        if not proc_raw:
            continue
        all_proc_names.add(proc_raw)
        base = _is_receive_proc(proc_raw.lower())
        if base is None:
            continue
        design_name_r = _s(r[design_col]) if design_col else ""
        recv_counter[proc_raw] = recv_counter.get(proc_raw, 0) + 1
        key = (lot_no_r, base, design_name_r)
        qty = int(r[pend_col]) if pend_col else 0
        rcv_qty_map[key] = rcv_qty_map.get(key, 0) + qty
        row_dt = r[date_col] if date_col else pd.NaT
        if pd.notna(row_dt):
            d_str = _fmt(row_dt)
            if key not in rcv_date_map or d_str > rcv_date_map[key]:
                rcv_date_map[key] = d_str
    print(f"[inhouse] all proc names: {sorted(all_proc_names)}", file=sys.stderr)
    print(f"[inhouse] receive rows found: {recv_counter}", file=sys.stderr)
    _RECEIVE_STATS.clear()
    _RECEIVE_STATS.update(recv_counter)

    # ── Pass 2: accumulate Issue rows by (lot_no, process, design) ──────────
    # One output row per unique (lot, process, design) triple — so a lot's
    # Top/Bottom/Dupatta pieces (or any other distinct design variants issued
    # under the same lot+process) each keep their own separate Issue/Receive
    # Qty instead of being summed into one blended figure.
    accum: dict[tuple, dict] = {}
    for _, r in df.iterrows():
        proc_raw = _s(r[proc_col])
        if not proc_raw:
            continue
        proc = _match_proc(proc_raw)
        if proc is None:
            continue

        design_name = _s(r[design_col])
        if not design_name:
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

        lot_no    = _s(r[lot_col]) if lot_col else ""
        issue_qty = int(r[pend_col]) if pend_col else 0
        bal_mtr   = round(float(r[mtr_col]), 2) if mtr_col else 0.0
        section   = _s(r[section_col]) if section_col else ""
        vendor    = _s(r[vendor_col])  if vendor_col  else ""

        key = (lot_no, proc, design_name)
        if key not in accum:
            accum[key] = {
                "lotNo":    lot_no,
                "process":  proc,
                "issueQty": 0,
                "balMtr":   0.0,
                "dates":    [],
                "sections": [],
                "vendors":  [],
            }
        g = accum[key]
        g["issueQty"] += issue_qty
        g["balMtr"]   += bal_mtr
        if pd.notna(row_dt):
            g["dates"].append(row_dt)
        if section:
            g["sections"].append(section)
        if vendor:
            g["vendors"].append(vendor)

    # "General Store Out" (receive side) commonly shows up for a (lot, design)
    # with no matching "General Store" issue voucher at all — confirmed in
    # live ERP data (e.g. lot LT-07071's base design "044-02" has four
    # "General Store Out" rows and zero "General Store" rows). An issue-
    # anchored build like the loop above would silently drop these — they'd
    # never get an accum entry, so no row, ever. Backfill one directly from
    # the receive side so the lot still shows up; with no issue qty to divide
    # by, the emit loop's total_qty = 0 + rcv_qty makes the ratio 1.0, so it
    # correctly lands as Completed rather than looking stuck.
    for (lot_no_bf, base_bf, design_bf) in rcv_qty_map:
        if base_bf != "General Store" or not design_bf:
            continue
        key_bf = (lot_no_bf, base_bf, design_bf)
        if key_bf in accum:
            continue
        accum[key_bf] = {
            "lotNo":    lot_no_bf,
            "process":  base_bf,
            "issueQty": 0,
            "balMtr":   0.0,
            "dates":    [],
            "sections": [],
            "vendors":  [],
        }

    # The ERP's "Cutting Receive"/"Stitching Receive" vouchers report a piece
    # quantity on only ~3-7% of rows (the rest are 0/blank — the ERP simply
    # doesn't record a qty on most receive transactions, only a date). Since
    # is_open below leans on that near-always-zero qty, a lot that has
    # genuinely finished Cutting/Stitching and moved on can still show those
    # earlier stages as "Open". Fix: if this lot+design already has a row at a
    # LATER pipeline stage, the earlier one must be done — a hard logical
    # fact, not a guess about the ERP's qty field. Scoped per (lot, design),
    # not lot alone, so one design's progress can't force another design's
    # still-genuinely-open row closed.
    lot_design_max_stage: dict[tuple, int] = {}
    for (lot_no, proc, design_name) in accum.keys():
        idx = _PROC_ORDER.get(proc, -1)
        dkey = (lot_no, design_name)
        if idx > lot_design_max_stage.get(dkey, -1):
            lot_design_max_stage[dkey] = idx

    # ── Emit one row per (lot, process) ───────────────────────────────────── #
    rows: list[dict] = []
    for (lot_no, proc, design_name), g in accum.items():
        earliest_dt = min(g["dates"]) if g["dates"] else None
        age_val = 0
        if earliest_dt is not None:
            try:
                age_val = max(0, (today - pd.to_datetime(earliest_dt).date()).days)
            except Exception:
                pass

        bal_qty   = g["issueQty"]          # BAL_PIECES on issue rows = remaining balance
        bal_mtr   = round(g["balMtr"], 2)
        # Pass 1 keyed by (lot_no, base_process, design); use that to look up receive data
        rcv_qty   = rcv_qty_map.get((lot_no, proc, design_name), 0)
        rcv_date  = rcv_date_map.get((lot_no, proc, design_name), "")
        total_qty = bal_qty + rcv_qty      # original issued = balance + received

        is_open  = (rcv_qty / total_qty < 0.90) if total_qty > 0 else (bal_mtr > 0)
        if is_open and lot_design_max_stage.get((lot_no, design_name), -1) > _PROC_ORDER.get(proc, -1):
            is_open = False   # this design already progressed past this stage

        # ERP's own authoritative per-voucher status (closed_lot.py) — takes
        # precedence over the heuristics above wherever it has a signal for
        # this exact (lot, process), in either direction.
        closed_lot_proc = _CLOSED_LOT_PROCESS_MAP.get(proc)
        if closed_lot_proc is not None:
            authoritative = closed_lot.is_closed(lot_no, closed_lot_proc)
            if authoritative is not None:
                is_open = not authoritative

        expected = _EXPECTED_DAYS.get(proc, 5)
        risk     = "Completed" if not is_open else _risk_level(age_val, expected)

        rows.append({
            "design":       design_name,
            "lotNo":        lot_no,
            # Prefer a real per-row Section column's majority vote (rare — this
            # ERP view doesn't have one); otherwise infer from the exact same
            # design string shown in "design" above, so the two columns can
            # never disagree about which design variant they describe.
            "section":      Counter(g["sections"]).most_common(1)[0][0] if g["sections"] else _section_from_design(design_name),
            "vendor":       Counter(g["vendors"]).most_common(1)[0][0]  if g["vendors"]  else "",
            "process":      proc,
            "issueQty":     total_qty,
            "issueDate":    _fmt(earliest_dt) if earliest_dt is not None else "",
            "receiveQty":   rcv_qty,
            "receiveDate":  rcv_date,
            "balQty":       bal_qty,       # BAL_PIECES: issueQty - receiveQty, still pending at this stage
            "balMtr":       bal_mtr,
            "ageDays":      age_val,
            "riskLevel":    risk,
            "expectedDays": expected,
            "lotStatus":    "Open" if is_open else "Completed",
        })

    rows.sort(key=lambda x: (_RISK_SCORE.get(x["riskLevel"], 0), x["ageDays"]), reverse=True)
    open_count = sum(1 for r in rows if r["lotStatus"] == "Open")
    print(
        f"[inhouse] {len(rows)} design-wise rows: {open_count} open, {len(rows) - open_count} completed",
        file=sys.stderr,
    )
    return rows


# ── Fetch ─────────────────────────────────────────────────────────────────── #

def _fetch_view(company_year_id: str, timeout: int = 120) -> pd.DataFrame:
    headers = {
        "Report-Api-Token": _API_TOKEN,
        "ViewName": _VIEW,
        "CompanyYearId": str(company_year_id),
        "Accept": "application/json",
    }
    print(f"[inhouse] Fetching (year={company_year_id})...", file=sys.stderr)
    try:
        r = requests.get(_ERP_URL, headers=headers, timeout=timeout)
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        print(f"[inhouse] ✓ {len(df):,} rows × {df.shape[1]} cols", file=sys.stderr)
        return df
    except Exception as exc:
        print(f"[inhouse] fetch error: {exc}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_combined() -> pd.DataFrame:
    frames    = [_fetch_view(cy) for cy in _COMPANY_YEAR_IDS]
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)


def _merge_delay_scores(rows: list[dict]) -> None:
    """Run the AI delay-prediction model and attach scores to open stage-rows.

    Scored per (lotNo, process) since a single lot has several open stages.
    """
    for r in rows:
        r["delayProb"]   = None
        r["riskBand"]    = None
        r["alreadyLate"] = False
    try:
        import delay_predict_inhouse
        result = delay_predict_inhouse.compute(rows=rows)
        if result and result.get("stages"):
            scored: dict[str, dict] = result["stages"]
            for r in rows:
                pred = scored.get(f"{r['lotNo']}::{r['process']}")
                if pred:
                    r["delayProb"]   = pred["delayProb"]
                    r["riskBand"]    = pred["riskBand"]
                    r["alreadyLate"] = pred["alreadyLate"]
            print(
                f"[inhouse] delay scores merged ({len(scored)} open stages scored, "
                f"AUC={result['metrics'].get('auc', 'n/a')})",
                file=sys.stderr,
            )
        else:
            print("[inhouse] delay prediction produced no scores", file=sys.stderr)
    except Exception as exc:
        print(f"[inhouse] delay prediction failed: {exc!r}", file=sys.stderr)


def refresh() -> None:
    global _RAW_ROWS, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        import debit_note
        rows = _build_rows(_fetch_combined())
        if rows or not _RAW_ROWS:
            # Real data came back (or we had nothing cached anyway) — adopt it.
            _merge_delay_scores(rows)
            debit_note.attach(rows)
            with _LOCK:
                _RAW_ROWS = rows
            _FETCHED_AT = time.time()
            _save_cache()
        else:
            # Empty result with good data already cached almost always means the
            # ERP fetch itself failed/timed out (see the [inhouse] fetch error
            # log line just above this) — keep serving the last good rows
            # instead of wiping the tracker until the next refresh succeeds.
            print(
                f"[inhouse] refresh returned 0 rows — keeping {len(_RAW_ROWS)} cached rows",
                file=sys.stderr,
            )
            _FETCHED_AT = time.time()
    except Exception as exc:
        print(f"[inhouse] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS or _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="inhouse-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def get_data(limit: int = 5000, process: str | None = None) -> dict:
    _maybe_refresh_bg()
    with _LOCK:
        all_rows = list(_RAW_ROWS)

    filtered = (
        [r for r in all_rows if r["process"] == process]
        if process and process != "All"
        else all_rows
    )
    # Only open (still-running) lots are ever shown to the user — completed
    # lots stay in `all_rows` for the summary stats below but are dropped here.
    open_filtered = [r for r in filtered if r.get("lotStatus") == "Open"]

    counts: dict[str, int] = {}
    for r in all_rows:
        counts[r["process"]] = counts.get(r["process"], 0) + 1

    return {
        "available":     _FETCHED_AT > 0,
        "total":         len(all_rows),
        "filtered":      len(open_filtered),
        "delayed":       sum(1 for r in all_rows if r.get("riskLevel") == "Delayed"),
        "atRisk":        sum(1 for r in all_rows if r.get("riskLevel") == "At Risk"),
        "onTrack":       sum(1 for r in all_rows if r.get("riskLevel") == "On Track"),
        "completed":     sum(1 for r in all_rows if r.get("riskLevel") == "Completed"),
        "processes":     ALL_PROCESSES,
        "counts":        counts,
        "asOf":          str(date.today()),
        "items":         open_filtered[:limit],
        "_erpCols":      _ALL_ERP_COLS,
        "_detectedCols": _DETECTED_COLS,
    }


def get_bottlenecks() -> dict:
    """Aggregate per-process bottleneck metrics across Inhouse + Job Work.

    Bottleneck score (0–1) = delayRate×0.45 + overrunScore×0.35 + queueScore×0.20
      delayRate   = delayedLots / openLots
      overrunScore = clamp(avgOverrun / 60, 0, 1)   where overrun = ageDays − expectedDays
      queueScore   = clamp(openLots / 100, 0, 1)
    Severity: High ≥ 0.60 · Medium ≥ 0.30 · Low < 0.30

    Combines the Inhouse floor processes (Cutting, Stitching, ...) with the
    Job Work vendor processes (Cut to Pack Issue, ...) — the two trackers use
    disjoint process names, so lots from both show up in one ranked list.

    Lots are ranked first by risk tier (Delayed > At Risk > On Track — that's
    what makes something a bottleneck at all), then WITHIN each tier by a
    per-LOT demand-impact score (lotDemandImpact = design-level currentDrr,
    summed across all the design's SKU sizes from the SKU Production Plan,
    × this specific lot's own pending pieces). Deliberately lot-wise, not
    design-wise: two lots of the same fast-selling design no longer tie on an
    identical design-wide number — a lot with more pieces actually stuck
    outranks a near-empty one, even for the same design and sale velocity.
    """
    _maybe_refresh_bg()
    with _LOCK:
        rows = list(_RAW_ROWS)

    try:
        import job_work
        rows = rows + job_work.get_data(limit=10000).get("items", [])
        all_processes = ALL_PROCESSES + job_work.ALL_PROCESSES
    except Exception as exc:
        print(f"[inhouse] job_work merge failed for bottlenecks: {exc!r}", file=sys.stderr)
        all_processes = ALL_PROCESSES

    # Design-level demand signal: sum each design's SKU-level currentDrr and
    # forecast35 (from the SKU Production Plan) up across all its sizes.
    design_demand: dict[str, dict] = {}
    try:
        import data as _data
        for pr in _data.PLAN_ROWS:
            key = pr.designNo.strip().upper()
            if not key:
                continue
            dd = design_demand.setdefault(key, {"drr": 0.0, "forecast35": 0})
            dd["drr"] += pr.currentDrr
            dd["forecast35"] += pr.forecast35
    except Exception as exc:
        print(f"[inhouse] demand lookup failed for bottlenecks: {exc!r}", file=sys.stderr)

    # Only consider Open lots issued within the last 90 days
    rows = [
        r for r in rows
        if r.get("lotStatus") == "Open"
        and int(r.get("ageDays", 0) or 0) <= 90
    ]

    # Initialise buckets for every canonical process so even idle ones appear
    stats: dict[str, dict] = {
        p: {
            "openLots": 0, "completedLots": 0,
            "delayedLots": 0, "atRiskLots": 0, "onTrackLots": 0,
            "ages": [], "expectedDays_list": [], "pendingPieces": 0,
        }
        for p in all_processes
    }

    # Group section-level rows back into ONE entry per (lot, process). A
    # single physical lot commonly has several section rows (Top/Bottom/
    # Dupatta — see inhouse.py/job_work.py's design-separation fix) but for
    # bottleneck detection a "stuck lot" means the whole lot: it shouldn't
    # appear 2-3 times in this ranked list just because it has that many
    # section pieces. Quantities are summed across the group; the "worst"
    # (oldest / most overrun) section's own risk, dates and AI score speak
    # for the lot as a whole, since expectedDays is constant per process so
    # the oldest section is always at least as overrun as its siblings.
    lot_groups: dict[tuple, list[dict]] = {}
    for r in rows:
        proc = r.get("process")
        if proc not in stats:
            continue
        lot_groups.setdefault((r.get("lotNo", ""), proc), []).append(r)

    lots: list[dict] = []
    for (lot_no, proc), group in lot_groups.items():
        s = stats[proc]
        worst = max(group, key=lambda r: int(r.get("ageDays", 0) or 0))
        risk = worst.get("riskLevel", "On Track")
        age  = int(worst.get("ageDays", 0) or 0)
        exp  = int(worst.get("expectedDays", 14) or 14)

        issue_qty_total = sum(int(r.get("issueQty", 0) or 0) for r in group)
        pending_total = sum(
            max(0, int(r.get("issueQty", 0) or 0) - int(r.get("receiveQty", 0) or 0))
            for r in group
        )

        s["openLots"] += 1
        s["ages"].append(age)
        s["expectedDays_list"].append(exp)
        s["pendingPieces"] += pending_total
        if risk == "Delayed":
            s["delayedLots"] += 1
        elif risk == "At Risk":
            s["atRiskLots"] += 1
        else:
            s["onTrackLots"] += 1

        base_design = _base_design(worst.get("design", ""))
        demand      = design_demand.get(base_design, {})
        design_drr  = demand.get("drr", 0.0)
        # Per-LOT priority, not per-design: two lots of the same hot-selling
        # design no longer tie on an identical design-wide number — a lot
        # with more pieces actually stuck weighs more than a near-empty one,
        # even for the same design and the same sale velocity.
        lot_demand_impact = round(design_drr * pending_total, 1)

        lots.append({
            "lotNo":            lot_no,
            "design":           base_design,
            "process":          proc,
            "section":          worst.get("section", ""),
            "vendor":           worst.get("vendor", ""),
            "riskLevel":        risk,
            "ageDays":          age,
            "expectedDays":     exp,
            "overrunDays":      max(0, age - exp),
            "issueQty":         issue_qty_total,
            "pendingPieces":    pending_total,
            "issueDate":        worst.get("issueDate", ""),
            "delayProb":        worst.get("delayProb"),
            "riskBand":         worst.get("riskBand"),
            "alreadyLate":      bool(worst.get("alreadyLate", False)),
            "designDrr":        round(design_drr, 2),
            "designForecast35": demand.get("forecast35", 0),
            "lotDemandImpact":  lot_demand_impact,
        })

    result: list[dict] = []
    for proc in all_processes:
        s         = stats[proc]
        open_lots = s["openLots"]
        if open_lots == 0:
            continue

        avg_age    = sum(s["ages"]) / len(s["ages"]) if s["ages"] else 0.0
        avg_exp    = sum(s["expectedDays_list"]) / len(s["expectedDays_list"]) if s["expectedDays_list"] else 14.0
        avg_overrun = max(0.0, avg_age - avg_exp)
        delay_rate  = s["delayedLots"] / max(open_lots, 1)

        score    = round(delay_rate * 0.45 + min(avg_overrun / 60, 1.0) * 0.35 + min(open_lots / 100, 1.0) * 0.20, 3)
        severity = "High" if score >= 0.60 else "Medium" if score >= 0.30 else "Low"

        result.append({
            "process":         proc,
            "severity":        severity,
            "bottleneckScore": score,
            "openLots":        open_lots,
            "delayedLots":     s["delayedLots"],
            "atRiskLots":      s["atRiskLots"],
            "onTrackLots":     s["onTrackLots"],
            "pendingPieces":   s["pendingPieces"],
            "avgAgeDays":      round(avg_age, 1),
            "avgExpectedDays": round(avg_exp, 1),
            "avgOverrunDays":  round(avg_overrun, 1),
            "delayRate":       round(delay_rate, 3),
        })

    result.sort(key=lambda x: x["bottleneckScore"], reverse=True)
    worst = result[0]["process"] if result else None

    lots.sort(
        key=lambda x: (
            _RISK_SCORE.get(x["riskLevel"], 0),
            x["lotDemandImpact"],
            x["overrunDays"],
            x["ageDays"],
        ),
        reverse=True,
    )

    return {
        "available":    _FETCHED_AT > 0,
        "asOf":         str(date.today()),
        "worstProcess": worst,
        "processes":    result,
        "lots":         lots,
    }


_load_cache()
_REFRESHING = True
threading.Thread(target=refresh, daemon=True, name="inhouse-init").start()
