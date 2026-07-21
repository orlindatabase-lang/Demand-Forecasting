"""
Job Work Issue/Receive module.

ERP view: View_Dboard_Trans_JOB_WORK_ISSUE_RECEIVE_For_Test_BI

Each row is one process event. The "Process" column identifies the type:

  Issue rows (lot start):
    "Cut to Pack Issue"      → starts a Cut to Pack lot
    "Cut to Stitching Issue" → starts a Cut to Stitch lot
    "Only Stitching Issue"   → starts an Only Stitch lot

  Receive / completion rows:
    "Cut To Pack Dispatch"   → closes Cut to Pack lots
    "Job Work Stitching GRN" → closes Cut to Stitch and Only Stitch lots

  Post-completion:
    "Job QC Process"         → quality check after GRN

The module exposes a FLAT view: every row from the ERP view becomes one
record, with an optional process-filter. This lets the dashboard show
all process types in a single table and let users drill into any one.
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

_VIEW = "View_Dboard_Trans_JOB_WORK_ISSUE_RECEIVE_For_Test_BI"

# Issue processes that appear as rows in the table.
# GRN/Dispatch processes are receive-side events — they enrich issue rows but
# are not shown as separate rows.
ALL_PROCESSES = [
    "Cut to Pack Issue",
    "Cut to Stitching Issue",
    "Only Stitching Issue",
    "Job QC Process",
]

# Expected calendar days from issue to receive for each issue process.
_EXPECTED_DAYS: dict[str, int] = {
    "Cut to Pack Issue":      15,
    "Cut to Stitching Issue": 12,
    "Only Stitching Issue":   10,
    "Job QC Process":          7,
}
_RISK_SCORE: dict[str, int] = {"Delayed": 3, "At Risk": 2, "On Track": 1, "Completed": 0}


def _risk_level(age_days: int, expected_days: int) -> str:
    if age_days > expected_days:
        return "Delayed"
    if age_days > int(expected_days * 0.75):
        return "At Risk"
    return "On Track"

_RAW_ROWS:   list[dict] = []
_FETCHED_AT: float      = 0.0
_REFRESH_SECS: int      = 300
_REFRESHING:   bool     = False
_LOCK = threading.Lock()

# ── Disk cache ────────────────────────────────────────────────────────────── #
_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "jw_rows.json"
_CACHE_MAX_AGE: int = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))  # 24 h default


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "rows": _RAW_ROWS}))
        print(f"[jw] cache saved ({len(_RAW_ROWS)} rows)", file=sys.stderr)
    except Exception as exc:
        print(f"[jw] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> None:
    """Pre-populate _RAW_ROWS from disk so the first API call is never empty on restart."""
    global _RAW_ROWS, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return
    try:
        payload   = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age       = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            print(f"[jw] disk cache too old ({age / 3600:.1f}h), ignoring", file=sys.stderr)
            return
        with _LOCK:
            _RAW_ROWS   = payload["rows"]
            _FETCHED_AT = cached_at
        print(f"[jw] loaded {len(_RAW_ROWS)} rows from disk cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[jw] cache load failed: {exc}", file=sys.stderr)


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
    """First column that matches any candidate (case-insensitive, space=underscore)."""
    cols_upper = {c.upper().replace(" ", "_"): c for c in df.columns}
    for cand in candidates:
        key = cand.upper().replace(" ", "_")
        if key in cols_upper:
            return cols_upper[key]
    return None


# ── Build flat rows ───────────────────────────────────────────────────────── #

def _build_flat_rows(df: pd.DataFrame) -> list[dict]:
    """Return one dict per ERP row for the last 365 days.

    Open lots  → Delayed / At Risk / On Track based on age vs expected days.
    Closed lots → Completed (issued and fully received within the year).
    Issue rows are enriched with the corresponding GRN receive qty/date so
    every row shows both when the lot was sent AND when it came back.
    """
    if df.empty:
        return []

    today   = date.today()
    cutoff  = pd.Timestamp(today - timedelta(days=365))

    df = df.copy()

    lot_col     = _find_col(df, "Lot No",      "LOT_NO",       "LOTNO")
    proc_col    = _find_col(df, "Process",     "PROCESS",      "PROCESS_NAME")
    design_col  = _find_col(df, "Design No",   "DESIGN_NO",    "DESIGN_NAME")
    section_col = _find_col(df, "Section",     "SECTION")
    vendor_col  = _find_col(df, "Vendor",      "VENDOR",       "PARTY_NAME",    "EMPLOYEE_NAME")
    iqty_col    = _find_col(df, "Issue Qty",   "ISSUE_QTY",    "ISSUEQTY",      "QTY_SIZE",  "QTY")
    # DiGiBizz uses VOUCHER_DATE as the single date column for every row type
    idate_col   = _find_col(df, "Issue Date",  "ISSUE_DATE",   "ISSUEDATE",     "VOUCHER_DATE", "Voucher Date")
    rqty_col    = _find_col(df, "Receive Qty", "RECEIVE_QTY",  "RECEIVEQTY",    "GRN_QTY",   "GRN Qty")
    rdate_col   = _find_col(df, "Receive Date","RECEIVE_DATE", "RECEIVEDATE",   "GRN_DATE",  "GRN Date")
    pend_col    = _find_col(df, "Pending", "PENDING", "PENDING_QTY", "PENDINGQTY", "ISSUE_BAL_QTY", "ISSUEBALQTY")
    age_col     = _find_col(df, "Age",         "AGE",          "AGE_DAYS",      "AGEDAYS")

    print(f"[jw] ERP columns: {list(df.columns)}", file=sys.stderr)

    if lot_col is None or proc_col is None:
        print(f"[jw] Missing Lot No or Process column", file=sys.stderr)
        return []

    if iqty_col:  df[iqty_col]  = _num(df[iqty_col])
    if rqty_col:  df[rqty_col]  = _num(df[rqty_col])
    if pend_col:  df[pend_col]  = _num(df[pend_col])
    if age_col:   df[age_col]   = _num(df[age_col])
    # DiGiBizz uses a single VOUCHER_DATE for both issue and receive rows;
    # parse it once and use context (process type) to determine meaning.
    if idate_col: df[idate_col] = pd.to_datetime(df[idate_col], errors="coerce")
    if rdate_col and rdate_col != idate_col:
        df[rdate_col] = pd.to_datetime(df[rdate_col], errors="coerce")

    _ISSUE_PROCS = {"Cut to Pack Issue", "Cut to Stitching Issue", "Only Stitching Issue"}
    _GRN_PROCS   = {"Cut To Pack Dispatch", "Job Work Stitching GRN"}

    # Maps each issue process to its corresponding GRN process
    _PROC_TO_GRN: dict[str, str] = {
        "Cut to Pack Issue":      "Cut To Pack Dispatch",
        "Cut to Stitching Issue": "Job Work Stitching GRN",
        "Only Stitching Issue":   "Job Work Stitching GRN",
    }

    # ── Pass 1: build GRN + issue-qty lookups across ALL rows (no date filter) #
    # Keyed by (lot, proc, design) — NOT just (lot, proc). ISSUE_QTY and GRN
    # qty are real per-size, per-design piece counts (confirmed against live
    # data: e.g. a matched 3-piece-suit lot issues equal, independently
    # meaningful quantities for its Top/Bottom/Dupatta designs — 675/675/675 —
    # not a shared lot-wide total), and GRN rows carry their own DESIGN_NO too
    # (100% populated). Without design in the key, one design's real qty/date
    # silently overwrote/blended with every other design sharing that lot+process.
    lot_iss_qty:       dict[str, int]   = {}   # lot → total issued (all procs) — lot-wide on purpose, only feeds the base open/closed heuristic below
    lot_grn_qty_total: dict[tuple, int] = {}   # (lot, grn_proc) → total received, all designs — same purpose
    lot_proc_iss_qty:  dict[tuple, int] = {}   # (lot, proc, design) → total issued
    lot_grn_qty:       dict[tuple, int] = {}   # (lot, grn_proc, design) → total received
    lot_grn_date:      dict[tuple, str] = {}   # (lot, grn_proc, design) → latest receive date

    for _, r in df.iterrows():
        lot_no = _s(r[lot_col])
        proc   = _s(r[proc_col])
        if not lot_no or not proc:
            continue
        design_r = _s(r[design_col]) if design_col else ""
        if proc in _ISSUE_PROCS:
            qty = int(r[iqty_col]) if iqty_col else 0
            lot_iss_qty[lot_no] = lot_iss_qty.get(lot_no, 0) + qty
            key = (lot_no, proc, design_r)
            lot_proc_iss_qty[key] = lot_proc_iss_qty.get(key, 0) + qty
        elif proc in _GRN_PROCS:
            rqty = int(r[rqty_col]) if rqty_col else (int(r[iqty_col]) if iqty_col else 0)
            tot_key = (lot_no, proc)
            lot_grn_qty_total[tot_key] = lot_grn_qty_total.get(tot_key, 0) + rqty
            key  = (lot_no, proc, design_r)
            lot_grn_qty[key] = lot_grn_qty.get(key, 0) + rqty
            grn_dt = pd.NaT
            if rdate_col and pd.notna(r.get(rdate_col, pd.NaT)):
                grn_dt = r[rdate_col]
            elif idate_col and pd.notna(r.get(idate_col, pd.NaT)):
                grn_dt = r[idate_col]
            if pd.notna(grn_dt):
                rdate_str = _fmt(grn_dt)
                if rdate_str:
                    if key not in lot_grn_date or rdate_str > lot_grn_date[key]:
                        lot_grn_date[key] = rdate_str

    open_lot_nos: set[str] = {
        lot for lot in lot_iss_qty
        if lot_iss_qty[lot] > sum(
            lot_grn_qty_total.get((lot, gp), 0) for gp in _GRN_PROCS
        )
    }

    # ── Pass 2: accumulate issue rows by (lot_no, process, design) ────────── #
    # One group per distinct design/section a lot+process actually issued.
    accum: dict[tuple, dict] = {}
    for _, r in df.iterrows():
        lot_no = _s(r[lot_col])
        proc   = _s(r[proc_col])
        if not lot_no or not proc:
            continue
        if proc in _GRN_PROCS:
            continue

        issue_dt = r[idate_col] if idate_col else pd.NaT

        # Date filter: keep rows issued within the last year
        if pd.notna(issue_dt):
            if issue_dt < cutoff:
                continue
        else:
            age_val_check = int(r[age_col]) if age_col else 0
            if age_val_check > 365:
                continue

        design  = _s(r[design_col])  if design_col  else ""
        section = _s(r[section_col]) if section_col else ""
        vendor  = _s(r[vendor_col])  if vendor_col  else ""
        iqty    = int(r[iqty_col])            if iqty_col    else 0
        pending = int(r[pend_col])            if pend_col    else 0

        key = (lot_no, proc, design)
        if key not in accum:
            accum[key] = {
                "sections": [],
                "vendors":  [],
                "issueQty": 0,
                "pending":  0,
                "dates":    [],
            }
        g = accum[key]
        if section:
            g["sections"].append(section)
        if vendor:
            g["vendors"].append(vendor)
        g["issueQty"] += iqty
        g["pending"]  += pending
        if pd.notna(issue_dt):
            g["dates"].append(issue_dt)

    # ── Emit one row per (lot, process, design) ────────────────────────────── #
    rows: list[dict] = []
    for (lot_no, proc, design), g in accum.items():
        earliest_dt = min(g["dates"]) if g["dates"] else None

        age_val = 0
        if earliest_dt is not None:
            try:
                age_val = max(0, (today - pd.to_datetime(earliest_dt).date()).days)
            except Exception:
                pass

        expected = _EXPECTED_DAYS.get(proc, 14)
        is_open  = lot_no in open_lot_nos

        grn_proc  = _PROC_TO_GRN.get(proc, "")
        row_rqty  = lot_grn_qty.get((lot_no, grn_proc, design), 0)  if grn_proc else 0
        row_rdate = lot_grn_date.get((lot_no, grn_proc, design), "") if grn_proc else ""

        # Real per-design piece total: the design's own issue-side sum, or the
        # design's own GRN receive sum if that's larger (a handful of designs
        # understate their issue-side qty vs. what was actually later received
        # — same "receipt is a hard lower bound" fix used elsewhere).
        raw_issue   = lot_proc_iss_qty.get((lot_no, proc, design), g["issueQty"])
        total_issue = max(raw_issue, row_rqty)

        section = max(set(g["sections"]), key=g["sections"].count) if g["sections"] else ""
        vendor  = max(set(g["vendors"]),  key=g["vendors"].count)  if g["vendors"]  else ""

        # ── Completion overrides (applied before risk is set) ─────────────── #
        # Override 1: 90%+ of piece-count received
        if is_open and total_issue > 0 and row_rqty >= 0.90 * total_issue:
            is_open = False

        # Override 2: ERP-reported balance = 0 means fully received per ERP
        # Applies to issue procs AND Job QC Process (post-completion QC rows)
        if is_open and pend_col and g["pending"] == 0:
            is_open = False

        # Override 3: ERP's own authoritative per-voucher status (closed_lot.py,
        # a separate "Open Lot Production" view) — takes precedence over the
        # qty-based heuristics above wherever it has a signal for this exact
        # (lot, process), in either direction.
        authoritative = closed_lot.is_closed(lot_no, proc)
        if authoritative is not None:
            is_open = not authoritative

        # Set risk AFTER all is_open overrides
        risk = "Completed" if not is_open else _risk_level(age_val, expected)

        rows.append({
            "lotNo":        lot_no,
            "design":       design,
            "section":      section,
            "vendor":       vendor,
            "process":      proc,
            "issueQty":     total_issue,
            "issueDate":    _fmt(earliest_dt) if earliest_dt is not None else "",
            "receiveQty":   row_rqty,
            "receiveDate":  row_rdate,
            "pending":      g["pending"],
            "ageDays":      age_val,
            "riskLevel":    risk,
            "expectedDays": expected,
            "lotStatus":    "Open" if is_open else "Completed",
        })

    # Delayed → At Risk → On Track → Completed, ties broken by ageDays desc
    rows.sort(key=lambda x: (_RISK_SCORE.get(x["riskLevel"], 0), x["ageDays"]), reverse=True)
    open_count = sum(1 for r in rows if r["lotStatus"] == "Open")
    print(
        f"[jw] {len(rows)} rows (last 365 days): {open_count} open, "
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
    print(f"[jw] Fetching '{_VIEW}' (year={company_year_id})...", file=sys.stderr)
    try:
        r = requests.get(_ERP_URL, headers=headers, timeout=timeout)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
        print(f"[jw] ERP error: {exc}", file=sys.stderr)
        return pd.DataFrame()
    try:
        df = pd.DataFrame(r.json())
        print(f"[jw] ✓ year={company_year_id} {len(df):,} rows × {df.shape[1]} cols", file=sys.stderr)
        return df
    except ValueError:
        print(f"[jw] Non-JSON response:\n{r.text[:300]}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_combined() -> pd.DataFrame:
    frames    = [_fetch_view(cy) for cy in _COMPANY_YEAR_IDS]
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)


# Last computed delay scores, keyed by lotNo — fob.py reads this instead of
# re-training its own model, since FOB (~60 lots total) is pooled into this
# same model as an extra route rather than trained standalone (see
# delay_predict_jw.py's module docstring).
_LAST_SCORED_LOTS: dict[str, dict] = {}


def get_scored_lots() -> dict[str, dict]:
    return dict(_LAST_SCORED_LOTS)


def _merge_delay_scores(rows: list[dict], df: pd.DataFrame) -> None:
    """Run the AI delay-prediction model and attach scores to open lots' rows in-place."""
    global _LAST_SCORED_LOTS
    for r in rows:
        r["delayProb"]   = None
        r["riskBand"]    = None
        r["alreadyLate"] = False
    try:
        import delay_predict_jw

        # Pool FOB Issue/Receive rows into training — FOB has too few lots
        # (~60) to train a model of its own; this borrows Job Work's much
        # larger sample while keeping FOB distinguishable via the "route"
        # feature. get_raw_rows() reads fob.py's own last fetch rather than
        # triggering a second live fetch of that ~300k-row ERP view here.
        combined_df = df
        try:
            import fob
            fob_raw = fob.get_raw_rows()
            if not fob_raw.empty:
                combined_df = pd.concat([df, fob_raw], ignore_index=True)
        except Exception as exc:
            print(f"[jw] FOB pooling skipped, scoring Job Work only: {exc!r}", file=sys.stderr)

        result = delay_predict_jw.compute(df_raw=combined_df)
        if result and result.get("lots"):
            scored: dict[str, dict] = result["lots"]
            _LAST_SCORED_LOTS = scored
            for r in rows:
                pred = scored.get(r["lotNo"])
                if pred:
                    r["delayProb"]   = pred["delayProb"]
                    r["riskBand"]    = pred["riskBand"]
                    r["alreadyLate"] = pred["alreadyLate"]
            print(
                f"[jw] delay scores merged ({len(scored)} open lots scored, "
                f"AUC={result['metrics'].get('auc', 'n/a')})",
                file=sys.stderr,
            )
        else:
            print("[jw] delay prediction produced no scores", file=sys.stderr)
    except Exception as exc:
        print(f"[jw] delay prediction failed: {exc!r}", file=sys.stderr)


def refresh() -> None:
    global _RAW_ROWS, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        import debit_note
        df   = _fetch_combined()
        rows = _build_flat_rows(df)
        if rows or not _RAW_ROWS:
            # Real data came back (or we had nothing cached anyway) — adopt it.
            _merge_delay_scores(rows, df)
            debit_note.attach(rows)
            with _LOCK:
                _RAW_ROWS = rows
            _save_cache()
        else:
            # Empty result with good data already cached almost always means the
            # ERP fetch itself failed/timed out — keep serving the last good
            # rows instead of wiping the tracker until the next refresh succeeds.
            print(
                f"[jw] refresh returned 0 rows — keeping {len(_RAW_ROWS)} cached rows",
                file=sys.stderr,
            )
        _FETCHED_AT = time.time()
    except Exception as exc:
        print(f"[jw] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS:
        return
    if _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="jw-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def get_data(limit: int = 5000, process: str | None = None) -> dict:
    """Return flat rows from the job-work view, optionally filtered by process."""
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

    # Per-process row counts (always computed over the full dataset)
    counts: dict[str, int] = {}
    for r in all_rows:
        counts[r["process"]] = counts.get(r["process"], 0) + 1

    delayed   = sum(1 for r in all_rows if r.get("riskLevel") == "Delayed")
    at_risk   = sum(1 for r in all_rows if r.get("riskLevel") == "At Risk")
    on_track  = sum(1 for r in all_rows if r.get("riskLevel") == "On Track")
    completed = sum(1 for r in all_rows if r.get("riskLevel") == "Completed")

    return {
        "available": _FETCHED_AT > 0,
        "total":     len(all_rows),
        "filtered":  len(open_filtered),
        "delayed":   delayed,
        "atRisk":    at_risk,
        "onTrack":   on_track,
        "completed": completed,
        "processes": ALL_PROCESSES,
        "counts":    counts,
        "asOf":      str(date.today()),
        "items":     open_filtered[:limit],
    }


# Load disk cache first so any waiting request is served immediately,
# then kick off a background refresh to get the latest ERP data.
_load_cache()
_REFRESHING = True
threading.Thread(target=refresh, daemon=True, name="jw-init").start()
