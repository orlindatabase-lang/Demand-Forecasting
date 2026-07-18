"""Job-work production delay risk (Module 3).

Primary data source: View_Dboard_Trans_JOB_WORK_ISSUE_RECEIVE_For_Test_BI

FOB (Free On Board) is a small outsourced-vendor route — same kind of work
as Job Work, just too small on its own (~60 lots total vs ~2600 for Job
Work) to train a standalone model. Its rows live in a different ERP view
(View_Dboard_Trans_Production_And_Job_Work_All_Data_For_BI, shared with
inhouse.py) but are pooled into this same model as an extra route so FOB
lots borrow statistical strength from Job Work's much larger sample while
still being distinguishable via the "route" feature. See fob.py.

Process roles across both views:
  ISSUE (lot start)   : Cut to Pack Issue, Cut to Stitching Issue, Only Stitching Issue, FOB Issue
  GRN   (completion)  : Cut To Pack Dispatch, Job Work Stitching GRN, FOB Receive
  Post-GRN (info)     : Job QC Process

Lead time = first GRN date − first Issue date per lot.
Route is inferred from which Issue process the lot used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

TARGET_DAYS = int(os.getenv("JW_DELAY_TARGET_DAYS", "60"))
_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
_CACHE_VERSION = "jw_v4"  # v4: pooled FOB Issue/Receive in as an extra route

# Issue → route label
_ISSUE_TO_ROUTE = {
    "Cut to Pack Issue":      "CutToPack",
    "Cut to Stitching Issue": "CutToStitch",
    "Only Stitching Issue":   "OnlyStitching",
    "FOB Issue":              "FOB",
}
_ISSUE_PROCESSES = set(_ISSUE_TO_ROUTE.keys())
_GRN_PROCESSES   = {"Cut To Pack Dispatch", "Job Work Stitching GRN", "FOB Receive"}

# Each route's matching GRN process — completion is route-specific
_ROUTE_GRN = {
    "CutToPack":     "Cut To Pack Dispatch",
    "CutToStitch":   "Job Work Stitching GRN",
    "OnlyStitching": "Job Work Stitching GRN",
    "FOB":           "FOB Receive",
}

_FEATURES = [
    "section", "route", "lot_type_code",
    "first_issue_month", "first_issue_woy",
    "log_qty", "has_stitching", "has_qc", "n_sizes",
    "design_lead", "design_rate", "design_cnt", "section_rate",
]
_CAT_FEATURES = ["section", "route"]

_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def _cache_path(snapshot: str) -> Path:
    return _CACHE_DIR / f"delay_{_CACHE_VERSION}_{snapshot}.json"


def load_cache(snapshot: str) -> dict | None:
    p = _cache_path(snapshot)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            return None
    return None


def save_cache(snapshot: str, payload: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(snapshot).write_text(json.dumps(payload))


# --------------------------------------------------------------------------- #
# Feature engineering
# --------------------------------------------------------------------------- #
def _lot_type_code(lot_no: str) -> int:
    prefix = str(lot_no).strip().upper()[:2]
    if prefix == "LT":
        return 1
    if prefix == "JW":
        return 2
    return 0


def _coalesce(df: pd.DataFrame, *cols: str) -> pd.Series:
    out = None
    for c in cols:
        if c in df.columns:
            out = df[c] if out is None else out.fillna(df[c])
    return out if out is not None else pd.Series("", index=df.index)


def _normalize(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Unify the two source views onto one column set.

    Job Work's own view and the FOB rows pooled in from the Inhouse/
    Production view (see module docstring) use different column names for
    the same concepts. Once concatenated, both names can be present in the
    same frame at once, each populated only for its own rows — coalesce
    rather than blind-rename, or a plain rename would silently skip
    whichever column name arrived second. Shared by build_lots() and
    _build_inflight() so both see identical values.
    """
    di = df_raw.copy()
    di["VOUCHER_DATE"]  = pd.to_datetime(di["VOUCHER_DATE"], errors="coerce")
    di["LOT_NO"]        = di["LOT_NO"].astype(str).str.strip()
    # fillna("") BEFORE astype(str): pandas' string dtype leaves NaN as a true
    # null rather than stringifying it to "nan" — a column entirely missing
    # from one of the two pooled source views (e.g. SECTION on FOB rows)
    # produces an all-null group per lot, and .mode() on an all-null series
    # returns EMPTY (not a "" mode), which crashed .mode().iat[0] below.
    di["PROCESS"]       = di["PROCESS"].fillna("").astype(str).str.strip() if "PROCESS" in di.columns else ""
    di["SECTION"]       = di["SECTION"].fillna("").astype(str).str.strip() if "SECTION" in di.columns else ""
    di["DESIGN_NO"]     = _coalesce(di, "DESIGN_NO", "DESIGN_NAME").fillna("").astype(str).str.strip()
    di["SIZE"]          = di["SIZE"].fillna("").astype(str).str.strip() if "SIZE" in di.columns else ""
    # FOB rows carry BAL_PIECES instead of ISSUE_QTY/ISSUE_BAL_QTY (same field
    # doubles as both issued qty and outstanding balance there — see fob.py).
    di["ISSUE_QTY"]     = pd.to_numeric(_coalesce(di, "ISSUE_QTY", "QTY", "BAL_PIECES"), errors="coerce").fillna(0)
    di["ISSUE_BAL_QTY"] = pd.to_numeric(_coalesce(di, "ISSUE_BAL_QTY", "PENDING_QTY", "BAL_PIECES"), errors="coerce").fillna(0)
    return di


def build_lots(df_raw: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """Roll the single view up to one row per lot with features + label inputs.

    Start date = first Issue event; completion date = first GRN event.
    """
    di = _normalize(df_raw)

    issue_rows = di[di["PROCESS"].isin(_ISSUE_PROCESSES)]
    grn_rows   = di[di["PROCESS"].isin(_GRN_PROCESSES)]

    if issue_rows.empty:
        return pd.DataFrame()

    def _safe_mode(s: pd.Series) -> str:
        m = s.mode()
        return m.iat[0] if not m.empty else ""

    gi = issue_rows.groupby("LOT_NO")
    lot = pd.DataFrame({
        "first_issue":   gi["VOUCHER_DATE"].min(),
        "section":       gi["SECTION"].agg(_safe_mode),
        "design":        gi["DESIGN_NO"].agg(_safe_mode),
        "total_issued":  gi["ISSUE_QTY"].sum(),
        "has_stitching": gi["PROCESS"].apply(
            lambda s: int(s.isin(["Cut to Stitching Issue", "Only Stitching Issue"]).any())
        ),
        "n_sizes": gi["SIZE"].nunique(),
        "route":   gi["PROCESS"].apply(
            lambda s: next((_ISSUE_TO_ROUTE[p] for p in s if p in _ISSUE_TO_ROUTE), "Unknown")
        ),
    }).reset_index()

    # Outstanding balance (across all row types)
    lot_bal = di.groupby("LOT_NO")["ISSUE_BAL_QTY"].sum().rename("total_outstanding")
    lot = lot.join(lot_bal, on="LOT_NO")
    lot["total_outstanding"] = lot["total_outstanding"].fillna(0)

    # QC flag — Job QC Process rows exist in this view post-GRN
    qc_flag = (
        di[di["PROCESS"] == "Job QC Process"]
        .groupby("LOT_NO").size().gt(0).rename("has_qc")
    )
    lot = lot.join(qc_flag, on="LOT_NO")
    lot["has_qc"] = lot["has_qc"].fillna(0).astype(int)

    # Completion: route-specific GRN only
    # CutToPack lots complete on Cut To Pack Dispatch;
    # CutToStitch / OnlyStitching lots complete on Job Work Stitching GRN.
    grn_per_proc = (
        grn_rows.groupby(["LOT_NO", "PROCESS"])
        .agg(grn_date=("VOUCHER_DATE", "max"), grn_qty=("ISSUE_QTY", "sum"))
        .reset_index()
        .rename(columns={"PROCESS": "_grn_proc"})
    )
    lot["_grn_proc"] = lot["route"].map(_ROUTE_GRN)
    lot = lot.merge(grn_per_proc, on=["LOT_NO", "_grn_proc"], how="left")
    lot = lot.drop(columns=["_grn_proc"])
    lot = lot.rename(columns={"grn_date": "last_date", "grn_qty": "total_grn"})
    lot["total_grn"] = lot["total_grn"].fillna(0)
    # Completed = 90%+ of issued pieces received back — matches job_work.py's own
    # open/closed rule. A lone partial GRN (e.g. 33% received) must NOT mark a lot
    # "completed", or the model silently stops scoring exactly the oldest, most
    # outstanding lots (they get a GRN date from the partial receipt, so a naive
    # "any GRN exists" check wrongly excludes them from training AND scoring).
    received_frac = lot["total_grn"] / lot["total_issued"].where(lot["total_issued"] > 0)
    lot["completed"] = (received_frac >= 0.90).fillna(False)

    lot["lead_days"] = (lot["last_date"] - lot["first_issue"]).dt.days
    lot["age"]       = (today - lot["first_issue"]).dt.days
    lot = lot[(lot["age"] >= 0) & (lot["age"] <= 500)].copy()

    lot["lot_type_code"]     = lot["LOT_NO"].apply(_lot_type_code)
    lot["first_issue_month"] = lot["first_issue"].dt.month.astype(int)
    lot["first_issue_woy"]   = lot["first_issue"].dt.isocalendar().week.astype(int)
    lot["log_qty"]           = np.log1p(lot["total_issued"].clip(lower=0))

    return lot


def _label(lot: pd.DataFrame, target_days: int) -> pd.Series:
    y = pd.Series(np.nan, index=lot.index)
    done = lot["completed"]
    y[done] = (lot.loc[done, "lead_days"] > target_days).astype(float)
    y[(~done) & (lot["age"] > target_days)] = 1.0
    return y


def _group_stats(tr: pd.DataFrame) -> dict:
    comp    = tr[tr["lead_days"].notna()]
    g_lead  = float(comp["lead_days"].mean()) if not comp.empty else 45.0
    return {
        "design_lead":  comp.groupby("design")["lead_days"].mean(),
        "design_rate":  tr.groupby("design")["y"].mean(),
        "design_cnt":   tr.groupby("design").size(),
        "section_rate": tr.groupby("section")["y"].mean(),
        "g_lead": g_lead,
        "g_rate": float(tr["y"].mean()),
    }


def _attach_group_feats(x: pd.DataFrame, stats: dict) -> pd.DataFrame:
    x = x.copy()
    x["design_lead"]  = x["design"].map(stats["design_lead"]).fillna(stats["g_lead"])
    x["design_rate"]  = x["design"].map(stats["design_rate"]).fillna(stats["g_rate"])
    x["design_cnt"]   = x["design"].map(stats["design_cnt"]).fillna(0).astype(float)
    x["section_rate"] = x["section"].map(stats["section_rate"]).fillna(stats["g_rate"])
    for c in _CAT_FEATURES:
        x[c] = x[c].astype("category")
    return x


def _align_cats(x: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    x = x.copy()
    for c in _CAT_FEATURES:
        x[c] = pd.Categorical(x[c].astype(str), categories=list(ref[c].cat.categories))
    return x


def _fit(tr: pd.DataFrame):
    from xgboost import XGBClassifier
    m = XGBClassifier(
        n_estimators=300, learning_rate=0.05, max_depth=4,
        subsample=0.85, colsample_bytree=0.85, reg_lambda=1.0,
        min_child_weight=5, enable_categorical=True, tree_method="hist",
        eval_metric="auc",
    )
    m.fit(tr[_FEATURES], tr["y"])
    return m


def _risk_band(p: float) -> str:
    return "High" if p >= 0.66 else "Medium" if p >= 0.40 else "Low"


# --------------------------------------------------------------------------- #
# In-flight annotations for open lots
# --------------------------------------------------------------------------- #
def _build_inflight(
    df_norm: pd.DataFrame,
    open_lot_nos: set,
    today: pd.Timestamp,
) -> dict[str, dict]:
    """Current process stage, days at stage, progress %, outstanding qty per open lot.

    ``df_norm`` must already be run through ``_normalize()`` — this reads
    ISSUE_QTY/ISSUE_BAL_QTY directly, which for FOB rows only exist after
    coalescing with BAL_PIECES.
    """
    sub = df_norm[df_norm["LOT_NO"].isin(open_lot_nos)].copy()

    # Stage order for progress: Issue=1, GRN=2, QC=3
    _stage = {p: 1 for p in _ISSUE_PROCESSES}
    _stage.update({p: 2 for p in _GRN_PROCESSES})
    _stage["Job QC Process"] = 3
    sub["_stage"] = sub["PROCESS"].map(_stage).fillna(0).astype(int)

    # Pre-compute route per lot (from issue rows) so we use the matching GRN process
    lot_route: dict[str, str] = (
        sub[sub["PROCESS"].isin(_ISSUE_PROCESSES)]
        .groupby("LOT_NO")["PROCESS"]
        .apply(lambda s: next((_ISSUE_TO_ROUTE[p] for p in s if p in _ISSUE_TO_ROUTE), "Unknown"))
        .to_dict()
    )

    result: dict[str, dict] = {}
    for lot_no, grp in sub.groupby("LOT_NO"):
        issue_grp = grp[grp["PROCESS"].isin(_ISSUE_PROCESSES)]
        route     = lot_route.get(str(lot_no), "Unknown")
        grn_proc  = _ROUTE_GRN.get(route)
        grn_grp   = grp[grp["PROCESS"] == grn_proc] if grn_proc else grp.iloc[0:0]

        total_issued   = float(issue_grp["ISSUE_QTY"].sum())
        total_received = float(grn_grp["ISSUE_QTY"].sum())
        progress_pct   = round(
            min(1.0, total_received / total_issued) if total_issued > 0 else 0.0, 3
        )

        # Current process = most recent event with outstanding balance, else latest event
        outstanding = grp[grp["ISSUE_BAL_QTY"] > 0]
        if not outstanding.empty:
            bottleneck       = outstanding.sort_values("_stage", ascending=False).iloc[0]
            current_process  = str(bottleneck["PROCESS"])
            last_moved       = grp[grp["PROCESS"] == bottleneck["PROCESS"]]["VOUCHER_DATE"].max()
            days_at          = int((today - last_moved).days) if pd.notna(last_moved) else 0
        else:
            latest           = grp.sort_values("VOUCHER_DATE").iloc[-1]
            current_process  = str(latest["PROCESS"])
            days_at          = 0

        result[str(lot_no)] = {
            "currentProcess":     current_process,
            "currentProcessDays": max(0, days_at),
            "progressPct":        progress_pct,
            "outstandingQty":     round(float(grp["ISSUE_BAL_QTY"].sum()), 0),
        }
    return result


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def compute(
    df_raw:     pd.DataFrame | None = None,
    issue_csv:  str | Path | None = None,
    grn_csv:    str | Path | None = None,   # kept for API compat; unused
    target_days: int = TARGET_DAYS,
) -> dict | None:
    """Train on labeled lots and score open lots.

    Parameters
    ----------
    df_raw      : DataFrame already fetched from the ERP view (preferred)
    issue_csv   : fallback path to ``job_work_issue_receive_raw.csv``
    grn_csv     : ignored (completion is derived from the view itself)
    target_days : days threshold for "delayed" label

    Returns ``None`` if data is missing or insufficient.
    Compatible output format with the existing data.py / API / dashboard.
    """
    try:
        from sklearn.metrics import accuracy_score, roc_auc_score
    except Exception:  # noqa: BLE001
        return None

    if df_raw is None or df_raw.empty:
        if issue_csv is None:
            issue_csv = _ROOT / "job_work_issue_receive_raw.csv"
        issue_csv = Path(issue_csv)
        if not issue_csv.exists():
            return None
        df_raw = pd.read_csv(issue_csv)

    if df_raw.empty:
        return None

    today = pd.Timestamp.now().normalize()
    lot   = build_lots(df_raw, today)
    if lot.empty:
        return None

    lot["y"] = _label(lot, target_days)

    labeled = lot.dropna(subset=["y"]).copy()
    labeled["y"] = labeled["y"].astype(int)
    if len(labeled) < 100 or labeled["y"].nunique() < 2:
        return None

    # Time-based 70/30 holdout
    labeled = labeled.sort_values("first_issue").reset_index(drop=True)
    cut = labeled["first_issue"].quantile(0.70)
    tr  = labeled[labeled["first_issue"] <= cut]
    te  = labeled[labeled["first_issue"] > cut]

    as_of   = today.date()
    metrics: dict = {"targetDays": target_days, "labeledLots": int(len(labeled))}

    if len(te) >= 30 and tr["y"].nunique() == 2 and te["y"].nunique() == 2:
        st  = _group_stats(tr)
        trf = _attach_group_feats(tr, st)
        tef = _attach_group_feats(te, st)
        tef = _align_cats(tef, trf)
        mdl = _fit(trf)
        p   = mdl.predict_proba(tef[_FEATURES])[:, 1]
        metrics["auc"]          = round(float(roc_auc_score(te["y"], p)), 3)
        metrics["accuracy"]     = round(float(accuracy_score(te["y"], (p >= 0.5).astype(int))), 3)
        metrics["testBaseRate"] = round(float(te["y"].mean()), 3)

    # Final model on all labeled lots
    stats = _group_stats(labeled)
    full  = _attach_group_feats(labeled, stats)
    model = _fit(full)

    # Score open lots (no GRN yet or still outstanding)
    open_lots = lot[~lot["completed"] & (lot["total_outstanding"] > 0)].copy()

    inflight = _build_inflight(_normalize(df_raw), set(open_lots["LOT_NO"].astype(str)), today)

    scored: dict[str, dict] = {}
    if not open_lots.empty:
        of    = _attach_group_feats(open_lots, stats)
        of    = _align_cats(of, full)
        probs = model.predict_proba(of[_FEATURES])[:, 1]
        for (_, r), prob_val in zip(open_lots.iterrows(), probs):
            already    = bool(r["age"] > target_days)
            prob       = 1.0 if already else float(round(prob_val, 4))
            lot_no_str = str(r["LOT_NO"])
            grn_ts   = r.get("last_date")
            grn_date = str(grn_ts.date()) if pd.notna(grn_ts) else None
            entry: dict = {
                "design":           r["design"],
                "section":          r["section"],
                "route":            str(r["route"]),
                "ageDays":          int(r["age"]),
                "expectedLeadDays": int(round(
                    float(stats["design_lead"].get(r["design"], stats["g_lead"]) or stats["g_lead"])
                )),
                "grnDate":     grn_date,
                "delayProb":   prob,
                "riskBand":    "High" if already else _risk_band(prob_val),
                "alreadyLate": already,
            }
            if lot_no_str in inflight:
                entry.update(inflight[lot_no_str])
            scored[lot_no_str] = entry

    metrics["openLots"]   = int(len(scored))
    metrics["atRiskHigh"] = int(sum(1 for v in scored.values() if v["riskBand"] == "High"))

    comp        = lot[lot["completed"]]
    design_lead = {
        str(k): int(round(v)) for k, v in
        comp.groupby("design")["lead_days"].median().items()
        if v == v
    }
    global_lead = int(round(comp["lead_days"].median())) if not comp.empty else 0

    return {
        "asOf":           str(as_of),
        "metrics":        metrics,
        "targetDays":     target_days,
        "lots":           scored,
        "designLeadDays": design_lead,
        "globalLeadDays": global_lead,
        "source":         "jobwork_single_view",
    }
