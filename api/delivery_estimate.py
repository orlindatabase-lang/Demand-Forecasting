"""AI delivery-date estimation (Module 3b).

Estimates when each pending production lot will be fully delivered, using two
strategies depending on lot progress:

1. **Fresh lots (0% received)** — Hierarchical median lead time from historical
   completed lots.  Fallback chain (most → least specific, min 3 lots each):
       vendor+design → vendor+route → design → route → global median

2. **Partial lots (some qty received)** — Blends the hierarchical estimate with
   the lot's own delivery rate.  If the lot is overdue (age > expected lead),
   the rate-based estimate takes priority.

Data source: ``job_work_issue_receive_raw.csv``
  (View_Dboard_Trans_Production_And_Job_Work_All_Data_For_BI)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

_ISSUE_PROCESSES = {
    "Cut to Pack Issue",
    "Cut to Stitching Issue",
    "Only Stitching Issue",
}
_ISSUE_TO_ROUTE = {
    "Cut to Pack Issue":      "CutToPack",
    "Cut to Stitching Issue": "CutToStitch",
    "Only Stitching Issue":   "OnlyStitching",
}
_GRN_PROCESSES = {"Cut To Pack Dispatch", "Job Work Stitching GRN"}
_ROUTE_GRN = {
    "CutToPack":     "Cut To Pack Dispatch",
    "CutToStitch":   "Job Work Stitching GRN",
    "OnlyStitching": "Job Work Stitching GRN",
}

MIN_LOTS = 3


def _ensure_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Add Issue_Date / GRN_Date columns when the CSV pre-computed ones are absent."""
    if "Issue_Date" not in df.columns:
        issue_dates = (
            df[df["PROCESS"].isin(_ISSUE_PROCESSES)]
            .groupby("LOT_NO")["VOUCHER_DATE"].min()
        )
        fallback_dates = df.groupby("LOT_NO")["VOUCHER_DATE"].min()
        issue_dates = issue_dates.reindex(fallback_dates.index).fillna(fallback_dates)
        df["Issue_Date"] = df["LOT_NO"].map(issue_dates)
    if "GRN_Date" not in df.columns:
        grn_dates = (
            df[df["PROCESS"].isin(_GRN_PROCESSES)]
            .groupby("LOT_NO")["VOUCHER_DATE"].max()
        )
        df["GRN_Date"] = df["LOT_NO"].map(grn_dates)
    return df


def _build_completed(df: pd.DataFrame) -> pd.DataFrame:
    """Roll raw rows into one-row-per-lot for completed lots with lead times."""
    issue = df[df["PROCESS"].isin(_ISSUE_PROCESSES)].copy()
    if issue.empty:
        return pd.DataFrame()

    gi = issue.groupby("LOT_NO")
    lots = pd.DataFrame({
        "vendor":     gi["PARTY_NAME"].first(),
        "design":     gi["DESIGN_NO"].agg(lambda s: s.mode().iat[0] if len(s) else ""),
        "section":    gi["SECTION"].agg(lambda s: s.mode().iat[0] if len(s) else ""),
        "route":      gi["PROCESS"].apply(
            lambda s: next((_ISSUE_TO_ROUTE[p] for p in s if p in _ISSUE_TO_ROUTE), "Unknown")
        ),
        "issue_date": gi["Issue_Date"].min(),
        "grn_date":   gi["GRN_Date"].max(),
        "total_qty":  gi["ISSUE_QTY"].sum(),
    }).reset_index()

    lots = lots[lots["grn_date"].notna() & lots["issue_date"].notna()]
    lots["lead_days"] = (lots["grn_date"] - lots["issue_date"]).dt.days
    lots = lots[(lots["lead_days"] > 0) & (lots["lead_days"] <= 365)]
    return lots


def _build_lookup_tables(completed: pd.DataFrame) -> dict:
    """Pre-compute median lead times at each hierarchy level."""
    g_med = float(completed["lead_days"].median()) if not completed.empty else 45.0

    def _med_cnt(grp):
        med = grp["lead_days"].median()
        cnt = grp.size()
        return med, cnt

    vd_grp = completed.groupby(["vendor", "design"])
    vd_med = vd_grp["lead_days"].median()
    vd_cnt = vd_grp.size()

    vr_grp = completed.groupby(["vendor", "route"])
    vr_med = vr_grp["lead_days"].median()
    vr_cnt = vr_grp.size()

    d_med = completed.groupby("design")["lead_days"].median()
    d_cnt = completed.groupby("design").size()

    r_med = completed.groupby("route")["lead_days"].median()

    return {
        "global":  g_med,
        "vd_med":  vd_med, "vd_cnt": vd_cnt,
        "vr_med":  vr_med, "vr_cnt": vr_cnt,
        "d_med":   d_med,  "d_cnt":  d_cnt,
        "r_med":   r_med,
    }


def _hierarchical_lead(
    vendor: str, design: str, route: str, tables: dict,
) -> tuple[float, str]:
    """Return (estimated_lead_days, source_level)."""
    vd_key = (vendor, design)
    if vd_key in tables["vd_cnt"] and tables["vd_cnt"][vd_key] >= MIN_LOTS:
        return float(tables["vd_med"][vd_key]), "vendor+design"

    vr_key = (vendor, route)
    if vr_key in tables["vr_cnt"] and tables["vr_cnt"][vr_key] >= MIN_LOTS:
        return float(tables["vr_med"][vr_key]), "vendor+route"

    if design in tables["d_cnt"] and tables["d_cnt"][design] >= MIN_LOTS:
        return float(tables["d_med"][design]), "design"

    if route in tables["r_med"].index:
        return float(tables["r_med"][route]), "route"

    return tables["global"], "global"


def compute(df_raw: pd.DataFrame | None = None) -> dict | None:
    """Compute delivery estimates for all pending lots.

    Returns
    -------
    dict with keys:
        estimates : dict[lot_no, {estDelivery, estLeadDays, source, method}]
        metrics   : {completedLots, globalMedian, ...}

    Returns ``None`` if data is missing or insufficient.
    """
    root = Path(__file__).resolve().parent.parent

    if df_raw is None or df_raw.empty:
        csv_path = root / "job_work_issue_receive_raw.csv"
        if not csv_path.exists():
            return None
        df_raw = pd.read_csv(csv_path)

    if df_raw.empty:
        return None

    df = df_raw.copy()

    # Normalise column aliases — CSV uses different names than the ERP view
    col_aliases = {}
    if "DESIGN_NAME" in df.columns and "DESIGN_NO" not in df.columns:
        col_aliases["DESIGN_NAME"] = "DESIGN_NO"
    if "QTY" in df.columns and "ISSUE_QTY" not in df.columns:
        col_aliases["QTY"] = "ISSUE_QTY"
    if "PENDING_QTY" in df.columns and "ISSUE_BAL_QTY" not in df.columns:
        col_aliases["PENDING_QTY"] = "ISSUE_BAL_QTY"
    if col_aliases:
        df = df.rename(columns=col_aliases)

    df["VOUCHER_DATE"]  = pd.to_datetime(df["VOUCHER_DATE"],  errors="coerce")
    df["ISSUE_QTY"]     = pd.to_numeric(df.get("ISSUE_QTY"),      errors="coerce").fillna(0)
    df["ISSUE_BAL_QTY"] = pd.to_numeric(df.get("ISSUE_BAL_QTY"),  errors="coerce").fillna(0)
    df["LOT_NO"]        = df["LOT_NO"].astype(str).str.strip()
    df["PROCESS"]       = df["PROCESS"].astype(str).str.strip() if "PROCESS" in df.columns else ""
    if "PARTY_NAME" in df.columns:
        df["PARTY_NAME"] = df["PARTY_NAME"].astype(str).str.strip()
    df["DESIGN_NO"] = df["DESIGN_NO"].astype(str).str.strip() if "DESIGN_NO" in df.columns else ""
    if "SECTION" in df.columns:
        df["SECTION"] = df["SECTION"].astype(str).str.strip()
    else:
        df["SECTION"] = ""

    # Derive Issue_Date / GRN_Date from VOUCHER_DATE if not pre-computed
    df = _ensure_dates(df)
    df["Issue_Date"] = pd.to_datetime(df["Issue_Date"], errors="coerce")
    df["GRN_Date"]   = pd.to_datetime(df["GRN_Date"],   errors="coerce")

    # ── Step 1: Build lookup tables from completed lots ────────────────── #
    completed = _build_completed(df)
    if len(completed) < 20:
        return None

    tables = _build_lookup_tables(completed)
    today  = pd.Timestamp.now().normalize()

    # ── Step 2: Identify ALL pending lots ────────────────────────────── #
    g_all = df.groupby("LOT_NO")

    # Build from all rows so no lot is missed
    pn_col = "PARTY_NAME" if "PARTY_NAME" in df.columns else None
    pending_agg = pd.DataFrame({
        "vendor":      g_all[pn_col].first() if pn_col else "",
        "design":      g_all["DESIGN_NO"].agg(lambda s: s.mode().iat[0] if len(s) else ""),
        "issue_date":  g_all["Issue_Date"].min(),
        "total_qty":   g_all["ISSUE_QTY"].sum(),
        "pending_qty": g_all["ISSUE_BAL_QTY"].sum().clip(lower=0),
    }).reset_index()

    # Route from issue rows (where available)
    issue_rows = df[df["PROCESS"].isin(_ISSUE_PROCESSES)]
    if not issue_rows.empty:
        lot_route = (
            issue_rows.groupby("LOT_NO")["PROCESS"]
            .apply(lambda s: next((_ISSUE_TO_ROUTE[p] for p in s if p in _ISSUE_TO_ROUTE), "Unknown"))
            .rename("route")
        )
        pending_agg = pending_agg.join(lot_route, on="LOT_NO", how="left")
    if "route" not in pending_agg.columns:
        pending_agg["route"] = "Unknown"
    pending_agg["route"] = pending_agg["route"].fillna("Unknown")

    pending_agg = pending_agg[pending_agg["pending_qty"] > 0].copy()

    if pending_agg.empty:
        return {"estimates": {}, "metrics": {"completedLots": len(completed)}}

    pending_agg["received_qty"] = (
        pending_agg["total_qty"] - pending_agg["pending_qty"]
    ).clip(lower=0)
    pending_agg["progress_pct"] = (
        pending_agg["received_qty"] / pending_agg["total_qty"].clip(lower=1)
    ).clip(upper=1.0)
    pending_agg["age_days"] = (today - pending_agg["issue_date"]).dt.days.fillna(0).astype(int)

    # ── Step 3: GRN events for rate-based estimation ───────────────────── #
    grn_rows = df[df["PROCESS"].isin(_GRN_PROCESSES)]
    lot_grn = grn_rows.groupby("LOT_NO").agg(
        grn_qty=("ISSUE_QTY", "sum"),
        first_grn=("VOUCHER_DATE", "min"),
        last_grn=("VOUCHER_DATE", "max"),
    )
    pending_agg = pending_agg.join(lot_grn, on="LOT_NO", how="left")

    # ── Step 4: Estimate each lot ──────────────────────────────────────── #
    estimates: dict[str, dict] = {}

    for _, r in pending_agg.iterrows():
        lot_no      = str(r["LOT_NO"])
        vendor      = str(r["vendor"]) if pd.notna(r["vendor"]) else ""
        design      = str(r["design"])
        route       = str(r["route"])
        issue_date  = r["issue_date"]
        age         = int(r["age_days"])
        progress    = float(r["progress_pct"])
        received    = float(r["received_qty"])
        pending     = float(r["pending_qty"])

        if pd.isna(issue_date):
            continue

        # Hierarchical expected lead days
        exp_lead, source = _hierarchical_lead(vendor, design, route, tables)
        exp_lead = max(1.0, exp_lead)

        if progress > 0 and age > 0 and received > 0:
            # ── Partial delivery: blend rate-based with hierarchical ──── #
            daily_rate = received / max(1, age)
            rate_remaining = pending / max(0.1, daily_rate)
            rate_est = age + rate_remaining

            if age > exp_lead:
                # Overdue: trust the lot's own rate more
                est_lead = rate_est
                method = "rate (overdue)"
            else:
                # On track: blend — weight hierarchical higher early,
                # shift toward rate-based as more data arrives
                w = min(1.0, progress * 2)  # at 50% progress, w=1.0
                est_lead = exp_lead * (1 - w) + rate_est * w
                method = "blended"
        else:
            # ── Fresh lot: pure hierarchical ──────────────────────────── #
            est_lead = exp_lead
            method = "hierarchical"

        est_lead = max(1.0, round(est_lead))
        est_delivery = issue_date + pd.Timedelta(days=int(est_lead))

        # Don't estimate delivery in the past for active lots
        if est_delivery < today:
            remaining_pct = pending / max(1, pending + received)
            est_delivery = today + pd.Timedelta(days=max(7, int(exp_lead * remaining_pct)))

        estimates[lot_no] = {
            "estDelivery":  est_delivery.strftime("%d %b %Y"),
            "estLeadDays":  int(est_lead),
            "source":       source,
            "method":       method,
            "progressPct":  round(progress * 100, 1),
        }

    method_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for v in estimates.values():
        method_counts[v["method"]] = method_counts.get(v["method"], 0) + 1
        source_counts[v["source"]] = source_counts.get(v["source"], 0) + 1

    return {
        "estimates": estimates,
        "metrics": {
            "completedLots":   int(len(completed)),
            "pendingLots":     int(len(estimates)),
            "globalMedianDays": round(tables["global"], 1),
            "methods":         method_counts,
            "sources":         source_counts,
        },
    }
