"""
Embroidery production-delay module.

Fetches data from 2 ERP views:
  Issue : View_Dboard_Trans_FAB_JOB_ISS_EMB_NEW_Data_For_Test_BI
  GRN   : View_Dboard_Trans_FAB_JOB_GRN_Data_For_Test_BI

Output columns per lot:
  lotNo, design, section, vendor,
  issueQty, issueDate,
  receiveQty, receiveDate, pendingQty,
  ageDays
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import threading
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

# ── ERP connection ────────────────────────────────────────────────────────── #
_ERP_URL          = "http://190.92.175.131:8080/DigiBizzErpApi/api/UnknownCallerApi/GetPowerBiReports"
_API_TOKEN        = "aaaqqqwww111"
# Comma-separated list of ERP company-year IDs to fetch and combine.
# "83" = FY2025-26, "84" = FY2026-27.  Both are needed so open lots that
# were issued in the previous year but not yet received still appear.
_COMPANY_YEAR_IDS: list[str] = [
    y.strip()
    for y in os.getenv("ERP_COMPANY_YEAR_IDS", "83").split(",")
    if y.strip()
]

# ── ERP view names ────────────────────────────────────────────────────────── #
_VIEW_ISSUE = "View_Dboard_Trans_FAB_JOB_ISS_EMB_NEW_Data_For_Test_BI"
_VIEW_GRN   = "View_Dboard_Trans_FAB_JOB_GRN_Data_For_Test_BI"

# ── Module-level cache ───────────────────────────────────────────────────── #
_LOTS: list[dict] = []
_FETCHED_AT: float = 0.0
_REFRESH_SECS: int = 300        # 5-minute TTL
_REFRESHING: bool = False
_LOCK = threading.Lock()

# ── Delay prediction ──────────────────────────────────────────────────────── #
_EMB_EXPECTED_DAYS = 10   # typical embroidery turnaround in calendar days
_RISK_SCORE: dict[str, int] = {"Delayed": 3, "At Risk": 2, "On Track": 1, "Completed": 0}


def _risk_level(age_days: int) -> str:
    if age_days > _EMB_EXPECTED_DAYS:
        return "Delayed"
    if age_days > int(_EMB_EXPECTED_DAYS * 0.75):
        return "At Risk"
    return "On Track"

# ── Disk cache ────────────────────────────────────────────────────────────── #
_CACHE_DIR  = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "emb_lots.json"
_CACHE_MAX_AGE: int = int(os.getenv("CACHE_MAX_AGE_SECS", str(24 * 3600)))  # 24 h default


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"fetchedAt": _FETCHED_AT, "lots": _LOTS}))
        print(f"[emb] cache saved ({len(_LOTS)} lots)", file=sys.stderr)
    except Exception as exc:
        print(f"[emb] cache save failed: {exc}", file=sys.stderr)


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
            print(f"[emb] disk cache too old ({age / 3600:.1f}h), ignoring", file=sys.stderr)
            return
        with _LOCK:
            _LOTS       = payload["lots"]
            _FETCHED_AT = cached_at
        print(f"[emb] loaded {len(_LOTS)} lots from disk cache ({age / 60:.0f}m old)", file=sys.stderr)
    except Exception as exc:
        print(f"[emb] cache load failed: {exc}", file=sys.stderr)


# ── Helpers ──────────────────────────────────────────────────────────────── #

def _design_from_article(article: str) -> str:
    """Extract design number from ARTICLE_NAME.

    ERP format: "417-04 - EMB DUPATTA BUTTI - 77"  →  "417-04"
    """
    s = str(article).strip()
    # Take the part before the first " - "
    m = re.match(r"^([^-]+(?:-\d+)?)\s*-", s)
    if m:
        return m.group(1).strip()
    return s.split(" ")[0] if s else ""


def _section_from_article(article: str) -> str:
    """Infer Top / Bottom / Dupatta from ARTICLE_NAME text."""
    a = str(article).upper()
    if "DUPATTA" in a:
        return "Dupatta"
    for kw in ("PLAZZO", "PALAZZO", "BOTTOM", "PANT", "TROUSER", "SKIRT"):
        if kw in a:
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


def _num(series: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


# ── Build ─────────────────────────────────────────────────────────────────── #

def _build_lots(issue: pd.DataFrame, grn: pd.DataFrame) -> list[dict]:
    today = date.today()
    if issue.empty:
        print("[emb] Issue view empty — no lots to build", file=sys.stderr)
        return []

    def _up(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.upper() for c in df.columns]
        return df

    issue = _up(issue)
    grn   = _up(grn) if not grn.empty else grn

    # ── Exclude completed/closed vouchers so only open jobs are shown ────── #
    _CLOSED = {"completed", "closed", "cancelled", "rejected"}
    if "STATUS" in issue.columns:
        before = len(issue)
        issue = issue[~issue["STATUS"].astype(str).str.strip().str.lower().isin(_CLOSED)]
        print(f"[emb] STATUS filter: {before} → {len(issue)} rows (removed {before - len(issue)} completed)", file=sys.stderr)
        if issue.empty:
            print("[emb] All issue rows are completed — no open lots", file=sys.stderr)
            return []

    # ── Issue: lot, design, section, vendor, issue qty/date ─────────────── #
    issue["LOT_NO"] = issue["LOT_NO"].astype(str).str.strip()

    qty_col    = "QTY_SIZE"     # issue qty column name in this view
    date_col   = "VOUCHER_DATE"
    vendor_col = "PARTY_NAME"
    art_col    = "ARTICLE_NAME"

    if qty_col  in issue.columns: issue[qty_col]  = _num(issue[qty_col])
    if date_col in issue.columns: issue[date_col] = pd.to_datetime(issue[date_col], errors="coerce")

    # Group by (LOT_NO, ARTICLE_NAME) — NOT lot alone. A lot commonly carries
    # several distinct embroidery components at once (e.g. lot LT-00008 has
    # separate "EMB NECK" (446 pcs), "EMB SLEEVE AND PLAZZO BUTTI" (611 pcs)
    # and "EMB DAMAN SLEEVE AND PLAZZO LACE" (2310 pcs) articles — 83% of all
    # lots have more than one distinct article). Grouping by lot alone kept
    # only the "first" article's design/section and summed every other
    # article's qty into it, discarding which component was which and
    # collapsing their genuinely different receive status into one.
    group_keys = ["LOT_NO"] + ([art_col] if art_col in issue.columns else [])
    agg: dict = {}
    if qty_col  in issue.columns: agg["issueQty"]  = (qty_col,  "sum")
    if date_col in issue.columns: agg["issueDate"]  = (date_col, "min")
    if vendor_col in issue.columns: agg["vendor"]   = (vendor_col, "first")

    iss_g = (
        issue.groupby(group_keys, sort=False).agg(**agg).reset_index()
        if agg
        else issue[group_keys].drop_duplicates()
    )

    # Derive design + section from this row's own ARTICLE_NAME
    if art_col in iss_g.columns:
        iss_g["design"]  = iss_g[art_col].apply(_design_from_article)
        iss_g["section"] = iss_g[art_col].apply(_section_from_article)

    # ── GRN: received qty and date, matched at the same (lot, article) grain #
    rcv_g = pd.DataFrame()
    if not grn.empty:
        grn["LOT_NO"] = grn["LOT_NO"].astype(str).str.strip()
        g_qty  = "GRN_QTY"
        g_date = "VOUCHER_DATE"
        if g_qty  in grn.columns: grn[g_qty]  = _num(grn[g_qty])
        if g_date in grn.columns: grn[g_date] = pd.to_datetime(grn[g_date], errors="coerce")
        grn_keys = ["LOT_NO"] + ([art_col] if art_col in grn.columns else [])
        ga: dict = {}
        if g_qty  in grn.columns: ga["receiveQty"]  = (g_qty,  "sum")
        if g_date in grn.columns: ga["receiveDate"] = (g_date, "max")
        if ga:
            rcv_g = grn.groupby(grn_keys, sort=False).agg(**ga).reset_index()

    # ── Merge ────────────────────────────────────────────────────────────── #
    merged = iss_g.copy()
    if not rcv_g.empty:
        merge_keys = [k for k in ("LOT_NO", art_col) if k in iss_g.columns and k in rcv_g.columns]
        merged = merged.merge(rcv_g, on=merge_keys, how="left")

    # Defaults
    for col in ("issueQty", "receiveQty"):
        if col in merged.columns:
            merged[col] = _num(merged[col]).clip(lower=0).round(0).astype(int)
        else:
            merged[col] = 0

    for col in ("receiveDate", "issueDate"):
        if col not in merged.columns:
            merged[col] = pd.NaT

    # Pending = issue - receive (clipped to 0)
    merged["pendingQty"] = (merged.get("issueQty", 0) - merged.get("receiveQty", 0)).clip(lower=0).astype(int)

    # ── Build output rows: last 365 days (open and completed lots) ────────── #
    cutoff = pd.Timestamp(today - timedelta(days=365))
    if "issueDate" in merged.columns:
        merged = merged[merged["issueDate"].isna() | (merged["issueDate"] >= cutoff)]

    lots: list[dict] = []
    for _, r in merged.iterrows():
        issue_dt = r.get("issueDate", pd.NaT)
        age_days = 0
        try:
            if pd.notna(issue_dt):
                age_days = max(0, (today - pd.to_datetime(issue_dt).date()).days)
        except Exception:
            pass

        pending    = int(r.get("pendingQty", 0))
        issue_qty  = int(r.get("issueQty",  0))
        receive_qty = int(r.get("receiveQty", 0))
        is_open    = (receive_qty / issue_qty < 0.90) if issue_qty > 0 else (pending > 0)
        risk       = _risk_level(age_days) if is_open else "Completed"

        lots.append({
            "lotNo":       str(r["LOT_NO"]),
            "design":      str(r.get("design",  "")),
            "section":     str(r.get("section", "Top")),
            "vendor":      str(r.get("vendor",  "")),
            "issueQty":    int(r.get("issueQty",   0)),
            "issueDate":   _fmt(issue_dt),
            "receiveQty":  int(r.get("receiveQty", 0)),
            "receiveDate": _fmt(r.get("receiveDate", pd.NaT)),
            "pendingQty":  pending,
            "ageDays":     age_days,
            "riskLevel":   risk,
            "lotStatus":   "Open" if is_open else "Completed",
        })

    lots.sort(key=lambda x: (_RISK_SCORE.get(x["riskLevel"], 0), x["ageDays"]), reverse=True)
    print(f"[emb] built {len(lots)} open lots (from {len(merged)} total)", file=sys.stderr)
    return lots


# ── Fetch + cache ─────────────────────────────────────────────────────────── #

def fetch_view(view_name: str, company_year_id: str, timeout: int = 120) -> pd.DataFrame:
    headers = {
        "Report-Api-Token": _API_TOKEN,
        "ViewName": view_name,
        "CompanyYearId": str(company_year_id),
        "Accept": "application/json",
    }
    print(f"[emb] Fetching '{view_name}' (year={company_year_id})...", file=sys.stderr)
    try:
        r = requests.get(_ERP_URL, headers=headers, timeout=timeout)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
        print(f"[emb] ERP error: {exc}", file=sys.stderr)
        return pd.DataFrame()
    try:
        df = pd.DataFrame(r.json())
        print(f"[emb] ✓ year={company_year_id} {len(df):,} rows × {df.shape[1]} cols", file=sys.stderr)
        return df
    except ValueError:
        print(f"[emb] Non-JSON response:\n{r.text[:300]}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_combined(view_name: str, timeout: int = 120) -> pd.DataFrame:
    """Fetch a view across all configured company-year IDs and concatenate rows."""
    frames = [fetch_view(view_name, cy, timeout) for cy in _COMPANY_YEAR_IDS]
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)


def _fetch_all() -> tuple[pd.DataFrame, pd.DataFrame]:
    df_fab_job_iss_emb = _fetch_combined(_VIEW_ISSUE)
    df_fab_job_grn     = _fetch_combined(_VIEW_GRN)
    return df_fab_job_iss_emb, df_fab_job_grn


def _merge_delay_scores(rows: list[dict], issue_df: pd.DataFrame, grn_df: pd.DataFrame) -> None:
    """Run the AI delay-prediction model and attach scores to lot rows in-place."""
    for r in rows:
        r["delayProb"]   = None
        r["riskBand"]    = None
        r["alreadyLate"] = False
    try:
        import delay_predict_emb
        result = delay_predict_emb.compute(issue_df=issue_df, grn_df=grn_df)
        if result and result.get("lots"):
            scored: dict[str, dict] = result["lots"]
            for r in rows:
                pred = scored.get(r["lotNo"])
                if pred:
                    r["delayProb"]   = pred["delayProb"]
                    r["riskBand"]    = pred["riskBand"]
                    r["alreadyLate"] = pred["alreadyLate"]
            print(
                f"[emb] delay scores merged ({len(scored)} open lots scored, "
                f"AUC={result['metrics'].get('auc', 'n/a')})",
                file=sys.stderr,
            )
        else:
            print("[emb] delay prediction produced no scores", file=sys.stderr)
    except Exception as exc:
        print(f"[emb] delay prediction failed: {exc!r}", file=sys.stderr)


def refresh() -> None:
    global _LOTS, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        issue, grn = _fetch_all()
        lots = _build_lots(issue, grn)
        if lots or not _LOTS:
            _merge_delay_scores(lots, issue, grn)
            with _LOCK:
                _LOTS = lots
            _save_cache()
        else:
            # Empty result with good data already cached almost always means the
            # ERP fetch itself failed/timed out — keep serving the last good
            # lots instead of wiping the tracker until the next refresh succeeds.
            print(
                f"[emb] refresh returned 0 lots — keeping {len(_LOTS)} cached lots",
                file=sys.stderr,
            )
        _FETCHED_AT = time.time()
    except Exception as exc:
        print(f"[emb] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS:
        return
    if _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="emb-refresh").start()


# ── Public API ────────────────────────────────────────────────────────────── #

def get_data(limit: int = 1000) -> dict:
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
threading.Thread(target=refresh, daemon=True, name="emb-init").start()