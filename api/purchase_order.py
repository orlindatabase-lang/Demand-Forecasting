"""
Purchase Order production-delay module.

Fetches data from 4 ERP views:
  PO      : View_Dboard_Trans_Purchase_Order_Summary_Test_BI
  GRN     : View_Dboard_Trans_Purchase_GRN_Data_For_Test_BI
  FCM     : View_Dboard_Trans_Fabric_FCM_Detail_For_Test_BI
  Alloc   : View_Dboard_Trans_Fabric_Allocation_Data_For_BI

Output columns per lot:
  lotNo, design, section, vendor,
  issueQty, issueDate,
  receiveQty, receiveDate, pendingQty,
  fcmStatus, fcmQty,
  allocQty, allocDate,
  estDelivery, delayStatus, ageDays
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

# ── ERP connection ────────────────────────────────────────────────────────── #
_ERP_URL   = "http://190.92.175.131:8080/DigiBizzErpApi/api/UnknownCallerApi/GetPowerBiReports"
_API_TOKEN = "aaaqqqwww111"
# Comma-separated list of ERP company-year IDs to fetch and combine.
# "83" = FY2025-26, "84" = FY2026-27.  Both are needed so open lots that
# were issued in the previous year but not yet received still appear.
_COMPANY_YEAR_IDS: list[str] = [
    y.strip()
    for y in os.getenv("ERP_COMPANY_YEAR_IDS", "83").split(",")
    if y.strip()
]

# ── ERP view names ────────────────────────────────────────────────────────── #
_VIEW_PO    = "View_Dboard_Trans_Purchase_Order_Summary_Test_BI"
_VIEW_GRN   = "View_Dboard_Trans_Purchase_GRN_Data_For_Test_BI"
_VIEW_FCM   = "View_Dboard_Trans_Fabric_FCM_Detail_For_Test_BI"
_VIEW_ALLOC = "View_Dboard_Trans_Fabric_Allocation_Data_For_BI"

# ── Module-level cache ───────────────────────────────────────────────────── #
_LOTS: list[dict] = []
_FETCHED_AT: float = 0.0
_REFRESH_SECS: int = 300        # 5-minute TTL
_REFRESHING: bool = False
_LOCK = threading.Lock()

# ── Disk cache ────────────────────────────────────────────────────────────── #
_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "po_lots.json"
_CACHE_MAX_AGE: int = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))  # 24 h default


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "lots": _LOTS}))
        print(f"[po] cache saved ({len(_LOTS)} lots)", file=sys.stderr)
    except Exception as exc:
        print(f"[po] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> None:
    """Pre-populate _LOTS from disk so the first API call is never empty on restart."""
    global _LOTS, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return
    try:
        payload   = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age       = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            print(f"[po] disk cache too old ({age / 3600:.1f}h), ignoring", file=sys.stderr)
            return
        with _LOCK:
            _LOTS       = payload["lots"]
            _FETCHED_AT = cached_at
        print(f"[po] loaded {len(_LOTS)} lots from disk cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[po] cache load failed: {exc}", file=sys.stderr)


# ── Helpers ──────────────────────────────────────────────────────────────── #

def _section_from_design(design: str) -> str:
    d = str(design).strip().upper()
    if d.endswith("-DUPATTA"):
        return "Dupatta"
    for sfx in ("-PLAZZO", "-PALAZZO", "-BOTTOM", "-PANT", "-TROUSER", "-SKIRT"):
        if d.endswith(sfx):
            return "Bottom"
    return "Top"


def _fmt(val) -> str:
    try:
        if val is None or pd.isna(val):
            return ""
    except Exception:
        pass
    try:
        dt = pd.to_datetime(val)
        return f"{dt.day} {dt.strftime('%b')} {dt.year}"   # e.g. "1 Aug 2025" (no leading zero, cross-platform)
    except Exception:
        return str(val)


def _num(series: pd.Series | None, default=0) -> pd.Series:
    if series is None:
        return pd.Series([], dtype=float)
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _first_col(df: pd.DataFrame, *names: str):
    """Return the first matching column name, or None."""
    for n in names:
        if n in df.columns:
            return n
    return None


_RECEIVE_COMPLETE_THRESHOLD = 0.90   # receiveQty / issueQty >= this → lot is received


def _delay_status(issue_qty: int, receive_qty: int, est_delivery, today: date) -> str:
    recv_frac = receive_qty / max(issue_qty, 1)
    if recv_frac >= _RECEIVE_COMPLETE_THRESHOLD:
        return "Received"
    try:
        est = pd.to_datetime(est_delivery).date() if pd.notna(est_delivery) else None
    except Exception:
        est = None
    if est is None:
        return "On Track"
    delta = (est - today).days
    if delta < 0:
        return "Delayed"
    if delta <= 6:
        return "At Risk"
    return "On Track"


# ── Build ─────────────────────────────────────────────────────────────────── #

def _build_lots(
    po: pd.DataFrame,
    grn: pd.DataFrame,
    fcm: pd.DataFrame,
    alloc: pd.DataFrame,
) -> list[dict]:
    today = date.today()
    if po.empty:
        print("[po] PO view empty — no lots to build", file=sys.stderr)
        return []

    # normalise column names → UPPER
    def _up(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.upper() for c in df.columns]
        return df

    po    = _up(po)
    grn   = _up(grn)   if not grn.empty   else grn
    fcm   = _up(fcm)   if not fcm.empty   else fcm
    alloc = _up(alloc) if not alloc.empty else alloc

    # ── PO: issue qty, pending, issue date, vendor, design, est delivery ─── #
    po["LOT_NO"] = po["LOT_NO"].astype(str).str.strip()
    po = po[po["LOT_NO"].str.len() > 0]   # drop rows with no lot number (bulk fabric POs)

    qty_col    = _first_col(po, "QTY",             "PO_QTY")
    pend_col   = _first_col(po, "PENDING_QTY",     "ISSUE_BAL_QTY")
    vdate_col  = _first_col(po, "VOUCHER_DATE")
    est_col    = _first_col(po, "EST_DELIVERY_DATE")
    vendor_col = _first_col(po, "EMPLOYEE_NAME",   "PARTY_NAME")   # ERP uses EMPLOYEE_NAME for vendor
    design_col = _first_col(po, "DESIGN_NAME",     "DESIGN_NO")
    group_col  = _first_col(po, "ARTICLE_GROUP")

    if qty_col:   po[qty_col]   = _num(po[qty_col])
    if pend_col:  po[pend_col]  = _num(po[pend_col])
    if vdate_col: po[vdate_col] = pd.to_datetime(po[vdate_col], errors="coerce")
    if est_col:   po[est_col]   = pd.to_datetime(po[est_col],   errors="coerce")
    if design_col: po[design_col] = po[design_col].fillna("").astype(str).str.strip()
    if vendor_col: po[vendor_col] = po[vendor_col].fillna("").astype(str).str.strip()
    if group_col:  po[group_col]  = po[group_col].fillna("").astype(str).str.strip()

    # One row per distinct (lot, article group, design, vendor) purchase line.
    # 79% of lots source fabric from more than one Article Group/vendor (a
    # lot commonly needs main fabric + lining + dupatta fabric, etc., often
    # from different suppliers) — grouping by LOT_NO alone and taking "first"
    # for design/vendor silently discarded every purchase line but one.
    group_keys = ["LOT_NO"]
    if group_col:  group_keys.append(group_col)
    if design_col: group_keys.append(design_col)
    if vendor_col: group_keys.append(vendor_col)

    agg: dict = {}
    if qty_col:    agg["issueQty"]    = (qty_col,    "sum")
    if pend_col:   agg["pendingQty"]  = (pend_col,   "sum")
    if vdate_col:  agg["issueDate"]   = (vdate_col,  "min")
    if est_col:    agg["estDelivery"] = (est_col,    "max")

    po_g = po.groupby(group_keys, sort=False).agg(**agg).reset_index() if agg else po[group_keys].drop_duplicates()
    po_g = po_g.rename(columns={
        **({group_col: "articleGroup"} if group_col else {}),
        **({design_col: "design"}      if design_col else {}),
        **({vendor_col: "vendor"}      if vendor_col else {}),
    })

    # ── GRN: actual received qty and date, matched at the same (lot, design,
    # vendor) granularity as the PO split above — GRN has no Article Group
    # column, so that part of the key is dropped for this join only. ──────── #
    rcv_g = pd.DataFrame()
    if not grn.empty:
        grn["LOT_NO"] = grn["LOT_NO"].astype(str).str.strip()
        g_qty    = _first_col(grn, "QTY", "GRN_QTY")   # QTY = received qty per transaction
        g_date   = _first_col(grn, "VOUCHER_DATE")
        g_design = _first_col(grn, "DESIGN_NAME", "DESIGN_NO")
        g_vendor = _first_col(grn, "EMPLOYEE_NAME", "PARTY_NAME")
        if g_qty:    grn[g_qty]    = _num(grn[g_qty])
        if g_date:   grn[g_date]   = pd.to_datetime(grn[g_date], errors="coerce")
        if g_design: grn[g_design] = grn[g_design].fillna("").astype(str).str.strip()
        if g_vendor: grn[g_vendor] = grn[g_vendor].fillna("").astype(str).str.strip()

        grn_keys = ["LOT_NO"]
        if g_design: grn_keys.append(g_design)
        if g_vendor: grn_keys.append(g_vendor)

        ga: dict = {}
        if g_qty:  ga["receiveQty"]  = (g_qty,  "sum")
        if g_date: ga["receiveDate"] = (g_date, "max")
        if ga:
            rcv_g = grn.groupby(grn_keys, sort=False).agg(**ga).reset_index()
            rcv_g = rcv_g.rename(columns={
                **({g_design: "design"} if g_design else {}),
                **({g_vendor: "vendor"} if g_vendor else {}),
            })

    # ── FCM: Pass / Fail / Partial per lot ─────────────────────────────── #
    fcm_g = pd.DataFrame()
    if not fcm.empty:
        fcm["LOT_NO"] = fcm["LOT_NO"].astype(str).str.strip()
        qc_col  = _first_col(fcm, "QC_STATUS")
        fcm_qty = _first_col(fcm, "QTY_MTR", "QTY", "QUANTITY")
        if fcm_qty: fcm[fcm_qty] = _num(fcm[fcm_qty])

        # QC_STATUS may be bool True/False or strings "True"/"False" or 1/0
        def _is_pass(v) -> bool:
            if isinstance(v, bool): return v
            return str(v).strip().lower() in ("true", "1", "pass", "yes")

        def _is_fail(v) -> bool:
            if isinstance(v, bool): return not v
            return str(v).strip().lower() in ("false", "0", "fail", "no")

        fcm_rows = []
        for lot, grp in fcm.groupby("LOT_NO", sort=False):
            status = "Pending"
            if qc_col and qc_col in grp.columns:
                vals = grp[qc_col].dropna()
                passes = sum(_is_pass(v) for v in vals)
                fails  = sum(_is_fail(v) for v in vals)
                if passes > 0 and fails > 0:  status = "Partial"
                elif passes > 0:               status = "Pass"
                elif fails > 0:                status = "Fail"
            row: dict = {"LOT_NO": lot, "fcmStatus": status}
            if fcm_qty:
                row["fcmQty"] = float(grp[fcm_qty].sum())
            fcm_rows.append(row)
        if fcm_rows:
            fcm_g = pd.DataFrame(fcm_rows)

    # ── Allocation ──────────────────────────────────────────────────────── #
    # Matched by (LOT_NO, design) — this view has no vendor/Article Group
    # column, so it can't be disambiguated any further than that, but design
    # alone already fixes the worst of it: a lot's PO rows are split by
    # (Article Group, design, vendor) above, and without a design-level match
    # here every one of those split rows — even ones for completely
    # different fabrics — would show the exact same lot-wide allocation
    # total, which is what was happening before this fix.
    alloc_g = pd.DataFrame()
    if not alloc.empty:
        # LOT_NO lives in TEXT_VALUE1 in this view
        if "TEXT_VALUE1" in alloc.columns and "LOT_NO" not in alloc.columns:
            alloc = alloc.rename(columns={"TEXT_VALUE1": "LOT_NO"})
        if "LOT_NO" in alloc.columns:
            alloc["LOT_NO"] = alloc["LOT_NO"].astype(str).str.strip()
            a_qty    = _first_col(alloc, "QTY", "ALLOCATION_QTY")   # allocated qty; pending is PENDNG_QTY (typo in ERP)
            a_date   = _first_col(alloc, "VOUCHER_DATE")
            a_design = _first_col(alloc, "DESIGN_NO", "DESIGN_NAME")
            if a_qty:    alloc[a_qty]    = _num(alloc[a_qty])
            if a_date:   alloc[a_date]   = pd.to_datetime(alloc[a_date], errors="coerce")
            if a_design: alloc[a_design] = alloc[a_design].fillna("").astype(str).str.strip()

            alloc_keys = ["LOT_NO"] + ([a_design] if a_design else [])
            aa: dict = {}
            if a_qty:  aa["allocQty"]  = (a_qty,  "sum")
            if a_date: aa["allocDate"] = (a_date, "min")
            if aa:
                alloc_g = alloc.groupby(alloc_keys, sort=False).agg(**aa).reset_index()
                if a_design:
                    alloc_g = alloc_g.rename(columns={a_design: "design"})

    # ── Merge all four sources ────────────────────────────────────────────── #
    merged = po_g.copy()
    if not rcv_g.empty:
        # GRN has no Article Group column, so join on whichever of
        # (LOT_NO, design, vendor) both sides actually share.
        rcv_keys = [k for k in ("LOT_NO", "design", "vendor") if k in po_g.columns and k in rcv_g.columns]
        merged = merged.merge(rcv_g, on=rcv_keys, how="left")
    if not fcm_g.empty:
        merged = merged.merge(fcm_g, on="LOT_NO", how="left")
    if not alloc_g.empty:
        alloc_keys_join = [k for k in ("LOT_NO", "design") if k in po_g.columns and k in alloc_g.columns]
        merged = merged.merge(alloc_g, on=alloc_keys_join, how="left")

    # defaults for missing merge columns
    for col, default in [
        ("issueQty", 0), ("pendingQty", 0), ("receiveQty", 0),
        ("fcmQty", 0), ("allocQty", 0),
    ]:
        if col in merged.columns:
            merged[col] = _num(merged[col]).clip(lower=0).round(0).astype(int)
        else:
            merged[col] = 0

    for col in ("fcmStatus",):
        if col not in merged.columns:
            merged[col] = "Pending"
        else:
            merged[col] = merged[col].fillna("Pending")

    for col in ("receiveDate", "allocDate", "estDelivery", "issueDate"):
        if col not in merged.columns:
            merged[col] = pd.NaT

    # ── Build output rows: last 365 days (open and received lots) ───────── #
    cutoff = pd.Timestamp(today - timedelta(days=365))
    if "issueDate" in merged.columns:
        merged = merged[merged["issueDate"].isna() | (merged["issueDate"] >= cutoff)]

    lots: list[dict] = []
    for _, r in merged.iterrows():
        lot_no      = str(r["LOT_NO"])
        design      = str(r.get("design", ""))
        vendor      = str(r.get("vendor", ""))
        article_grp = str(r.get("articleGroup", ""))
        pending     = int(r.get("pendingQty",  0))
        receive_qty = int(r.get("receiveQty",  0))
        # A handful of PO lines (~0.1%) carry a near-zero QTY (e.g. 0.2 Mtr)
        # yet the same (lot, design, vendor) later received a much larger real
        # GRN delivery (e.g. 655 pcs) — a genuine ERP data-entry quirk (the PO
        # quantity on record understates what was actually ordered). You can
        # never receive more than was truly ordered, so the receipt itself is
        # a hard lower bound on the real issued qty — same fix job_work.py
        # already applies for its own "issue qty column sometimes undercounts"
        # problem.
        issue_qty   = max(int(r.get("issueQty", 0)), receive_qty)
        est_del     = r.get("estDelivery", pd.NaT)
        # The ERP uses "1 Jan 1900" (and similar epoch placeholders) as a null
        # sentinel instead of a real NULL when no estimate was ever entered —
        # treat any pre-2000 date as "no estimate" rather than a real, wildly
        # overdue delivery date (it was making a handful of lots show as
        # Delayed, and "1 Jan 1900" as a displayed date, for no real reason).
        if pd.notna(est_del) and pd.Timestamp(est_del).year < 2000:
            est_del = pd.NaT
        is_open     = (receive_qty / max(issue_qty, 1)) < _RECEIVE_COMPLETE_THRESHOLD

        # ERP's own authoritative per-lot status (closed_lot.py, a separate
        # "Open Lot Production" view) — takes precedence over the qty-ratio
        # heuristic above whenever it has a signal for this lot's PO line, in
        # either direction. Matched at LOT_NO grain only (that view has no
        # Article Group/design column for PO rows to match more precisely).
        authoritative = closed_lot.is_closed(lot_no, "Purchase Order")
        if authoritative is not None:
            is_open = not authoritative

        issue_dt = r.get("issueDate", pd.NaT)
        age_days = 0
        try:
            if pd.notna(issue_dt):
                age_days = max(0, (today - pd.to_datetime(issue_dt).date()).days)
        except Exception:
            pass

        status    = _delay_status(issue_qty, receive_qty, est_del, today)
        risk      = status if is_open else "Completed"
        lots.append({
            "lotNo":       lot_no,
            "design":      design,
            "section":     _section_from_design(design),
            "articleGroup": article_grp,
            "vendor":      vendor,
            "issueQty":    issue_qty,
            "issueDate":   _fmt(issue_dt),
            "receiveQty":  int(r.get("receiveQty", 0)),
            "receiveDate": _fmt(r.get("receiveDate", pd.NaT)),
            "pendingQty":  pending,
            "fcmStatus":   str(r.get("fcmStatus", "Pending")),
            "fcmQty":      int(r.get("fcmQty",    0)),
            "allocQty":    int(r.get("allocQty",  0)),
            "allocDate":   _fmt(r.get("allocDate", pd.NaT)),
            "estDelivery": _fmt(est_del),
            "delayStatus": status,
            "riskLevel":   risk,
            "lotStatus":   "Open" if is_open else "Completed",
            "ageDays":     age_days,
        })

    _PO_RISK_SCORE = {"Delayed": 3, "At Risk": 2, "On Track": 1, "Completed": 0}
    lots.sort(key=lambda x: (_PO_RISK_SCORE.get(x["riskLevel"], 0), x["ageDays"]), reverse=True)
    print(f"[po] built {len(lots)} lots (from {len(merged)} total, last 365 days)", file=sys.stderr)
    return lots


# ── Fetch + cache ─────────────────────────────────────────────────────────── #

def _fetch_view(view_name: str, company_year_id: str, timeout: int = 120) -> pd.DataFrame:
    headers = {
        "Report-Api-Token": _API_TOKEN,
        "ViewName": view_name,
        "CompanyYearId": company_year_id,
        "Accept": "application/json",
    }
    try:
        r = requests.get(_ERP_URL, headers=headers, timeout=timeout)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
        print(f"[po] ERP error fetching {view_name!r} year={company_year_id}: {exc}", file=sys.stderr)
        return pd.DataFrame()
    try:
        return pd.DataFrame(r.json())
    except ValueError:
        print(f"[po] Non-JSON response for {view_name!r}: {r.text[:200]}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_combined(view_name: str, timeout: int = 120) -> pd.DataFrame:
    """Fetch a view across all configured company-year IDs and concatenate rows."""
    frames = [_fetch_view(view_name, cy, timeout) for cy in _COMPANY_YEAR_IDS]
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame()
    combined = pd.concat(non_empty, ignore_index=True)
    print(f"[po] {view_name}: {len(combined):,} rows total across {_COMPANY_YEAR_IDS}", file=sys.stderr)
    return combined


def _fetch_all() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    results = []
    for view, label, timeout in [
        (_VIEW_PO,    "PO",    120),
        (_VIEW_GRN,   "GRN",   300),   # 115k rows — needs longer timeout
        (_VIEW_FCM,   "FCM",   300),   # 115k rows — needs longer timeout
        (_VIEW_ALLOC, "Alloc", 120),
    ]:
        df = _fetch_combined(view, timeout=timeout)
        print(f"[po] {label}: {len(df):,} rows", file=sys.stderr)
        results.append(df)
    return tuple(results)  # type: ignore[return-value]


def _merge_delay_scores(lots: list[dict], po: pd.DataFrame, grn: pd.DataFrame) -> None:
    """Run the delay-prediction model and attach scores to lot dicts in-place."""
    try:
        import delay_predict_po
        result = delay_predict_po.compute(po_df=po, grn_df=grn)
        if result and result.get("lots"):
            scored: dict[str, dict] = result["lots"]
            for lot in lots:
                pred = scored.get(lot["lotNo"])
                if pred:
                    lot["delayProb"]  = pred["delayProb"]
                    lot["riskBand"]   = pred["riskBand"]
                    lot["alreadyLate"] = pred["alreadyLate"]
                    continue
            print(
                f"[po] delay scores merged ({len(scored)} open lots scored, "
                f"AUC={result['metrics'].get('auc', 'n/a')})",
                file=sys.stderr,
            )
        else:
            print("[po] delay prediction produced no scores", file=sys.stderr)
    except Exception as exc:
        print(f"[po] delay prediction failed: {exc!r}", file=sys.stderr)


def refresh() -> None:
    """Synchronously fetch all 4 views, rebuild the lots table, and attach delay scores."""
    global _LOTS, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        po, grn, fcm, alloc = _fetch_all()

        # A single empty source view (e.g. GRN timing out) still leaves the
        # other 3 views intact, so _build_lots() below produces a non-empty
        # `lots` list — the old "adopt unless totally empty" guard let that
        # through, silently wiping every lot's receiveQty/receiveDate/fcm/
        # alloc fields back to zero/blank for that field alone. All 4 views
        # normally return tens/hundreds of thousands of rows, so an empty
        # result (while we already have good cached lots) is almost always a
        # fetch failure, never genuine "no data" — treat it as one.
        sources_ok = True
        if _LOTS:
            for label, df in (("PO", po), ("GRN", grn), ("FCM", fcm), ("Alloc", alloc)):
                if df.empty:
                    print(f"[po] {label} view came back empty — treating refresh as a partial failure", file=sys.stderr)
                    sources_ok = False

        lots = _build_lots(po, grn, fcm, alloc) if sources_ok else []
        if lots or not _LOTS:
            _merge_delay_scores(lots, po, grn)
            with _LOCK:
                _LOTS = lots
            _FETCHED_AT = time.time()
            _save_cache()
        else:
            # Empty result with good data already cached almost always means the
            # ERP fetch itself failed/timed out — keep serving the last good
            # lots instead of wiping the tracker until the next refresh succeeds.
            print(
                f"[po] refresh returned 0 lots — keeping {len(_LOTS)} cached lots",
                file=sys.stderr,
            )
            _FETCHED_AT = time.time()
    except Exception as exc:
        print(f"[po] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    """Start a background refresh if the cache is stale."""
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS:
        return
    if _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="po-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def get_data(limit: int = 1000) -> dict:
    """Return the purchase-order lots for the API endpoint."""
    _maybe_refresh_bg()
    with _LOCK:
        all_lots = list(_LOTS)
    # Only open (still-running) lots are ever shown to the user — completed
    # lots stay in `_LOTS` for the summary stats below but are dropped here.
    lots = [r for r in all_lots if r.get("lotStatus") == "Open"][:limit]
    total     = len(_LOTS)
    over30    = sum(1 for r in _LOTS if r["ageDays"] > 30)
    delayed   = sum(1 for r in _LOTS if r.get("riskLevel") == "Delayed")
    at_risk   = sum(1 for r in _LOTS if r.get("riskLevel") == "At Risk")
    on_track  = sum(1 for r in _LOTS if r.get("riskLevel") == "On Track")
    completed = sum(1 for r in _LOTS if r.get("riskLevel") == "Completed")
    return {
        "available": _FETCHED_AT > 0,
        "total":     total,
        "over30":    over30,
        "delayed":   delayed,
        "atRisk":    at_risk,
        "onTrack":   on_track,
        "completed": completed,
        "asOf":      str(date.today()),
        "items":     lots,
    }


# Load disk cache first so any waiting request is served immediately,
# then kick off a background refresh to get the latest ERP data.
_load_cache()
_REFRESHING = True
threading.Thread(target=refresh, daemon=True, name="po-init").start()
