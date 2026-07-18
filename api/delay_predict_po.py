"""Purchase-Order delay prediction (Module 3B).

Data sources (preferred):
  po_df  : View_Dboard_Trans_Purchase_Order_Summary_Test_BI
  grn_df : View_Dboard_Trans_Purchase_GRN_Data_For_Test_BI

Fallback: Purchase_Order_issue.csv  (STATUS / PENDING_QTY used as completion proxy;
          grn_df is required for actual lead-time labels so CSV-only training is
          skipped gracefully when no GRN data is available.)

Training : completed lots ONLY (grn_date is known or STATUS == "Completed")
Label    : (grn_date - issue_date).days > est_delivery_days  — variable per lot
Features : vendor, article_group, log_qty, est_delivery_days,
           issue_month, issue_woy,
           vendor_delay_rate, vendor_delay_cnt, article_delay_rate
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

_CACHE_DIR     = Path(__file__).resolve().parent / ".cache"
_CACHE_VERSION = "po_v1"

_FEATURES = [
    "vendor", "article_group",
    "log_qty", "est_delivery_days",
    "issue_month", "issue_woy",
    "vendor_delay_rate", "vendor_delay_cnt", "article_delay_rate",
]
_CAT_FEATURES = ["vendor", "article_group"]

_ROOT = Path(__file__).resolve().parent.parent

_RECEIVE_COMPLETE_THRESHOLD = 0.90   # grn_qty / issue_qty >= this → lot is "completed"


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
def build_lots(
    po_df:  pd.DataFrame,
    grn_df: pd.DataFrame | None,
    today:  pd.Timestamp,
) -> pd.DataFrame:
    """One row per PO lot with features + label inputs.

    Completion date comes from grn_df (preferred).
    When grn_df is None the STATUS / PENDING_QTY columns in po_df are used as
    a completion proxy, but lead_days will be NaN so those lots are excluded
    from the labeled training set.
    """
    po = po_df.copy()
    po.columns = [c.upper() for c in po.columns]

    # Column aliases: ERP uses EMPLOYEE_NAME for the vendor
    for src, dst in [("EMPLOYEE_NAME", "VENDOR"), ("DESIGN_NAME", "DESIGN")]:
        if src in po.columns and dst not in po.columns:
            po = po.rename(columns={src: dst})

    po["LOT_NO"]            = po["LOT_NO"].astype(str).str.strip()
    po["VOUCHER_DATE"]      = pd.to_datetime(po.get("VOUCHER_DATE"),       errors="coerce")
    po["VENDOR"]            = po.get("VENDOR",           pd.Series("Unknown", index=po.index)).fillna("Unknown").astype(str).str.strip()
    po["ARTICLE_GROUP"]     = po.get("ARTICLE_GROUP",    pd.Series("Unknown", index=po.index)).fillna("Unknown").astype(str).str.strip()
    po["QTY"]               = pd.to_numeric(po.get("QTY"),                 errors="coerce").fillna(0)
    po["PENDING_QTY"]       = pd.to_numeric(po.get("PENDING_QTY"),         errors="coerce").fillna(0)
    po["EST_DELIVERY_DAYS"] = pd.to_numeric(po.get("EST_DELIVERY_DAYS"),   errors="coerce").fillna(7)
    po["STATUS"]            = po.get("STATUS", pd.Series("", index=po.index)).fillna("").astype(str)

    po = po[po["LOT_NO"].str.len() > 0]
    if po.empty:
        return pd.DataFrame()

    gi = po.groupby("LOT_NO")
    lot = pd.DataFrame({
        "issue_date":        gi["VOUCHER_DATE"].min(),
        "vendor":            gi["VENDOR"].agg(lambda s: s.mode().iat[0] if not s.empty else "Unknown"),
        "article_group":     gi["ARTICLE_GROUP"].agg(lambda s: s.mode().iat[0] if not s.empty else "Unknown"),
        "total_qty":         gi["QTY"].sum(),
        "pending_qty":       gi["PENDING_QTY"].sum(),
        "est_delivery_days": gi["EST_DELIVERY_DAYS"].max(),   # conservative: use the longest per lot
        "status":            gi["STATUS"].agg(
            lambda s: "Completed" if (s == "Completed").any() else s.iloc[0]
        ),
    }).reset_index()

    # ── Completion date from GRN (preferred) ──────────────────────────────── #
    if grn_df is not None and not grn_df.empty:
        grn = grn_df.copy()
        grn.columns = [c.upper() for c in grn.columns]
        grn["LOT_NO"]       = grn["LOT_NO"].astype(str).str.strip()
        grn["VOUCHER_DATE"] = pd.to_datetime(grn.get("VOUCHER_DATE"), errors="coerce")
        grn_qty_col         = next((c for c in ("QTY", "GRN_QTY") if c in grn.columns), None)
        grn["_qty"]         = pd.to_numeric(grn[grn_qty_col], errors="coerce").fillna(0) if grn_qty_col else 0
        grn_agg = grn.groupby("LOT_NO").agg(
            grn_date=("VOUCHER_DATE", "max"),
            grn_qty=("_qty", "sum"),
        ).reset_index()
        lot = lot.merge(grn_agg, on="LOT_NO", how="left")
        lot["grn_qty"]   = lot["grn_qty"].fillna(0.0)
        recv_frac        = lot["grn_qty"] / lot["total_qty"].clip(lower=1)
        lot["completed"] = recv_frac >= _RECEIVE_COMPLETE_THRESHOLD
    else:
        # CSV / no-GRN fallback: mark completed via pending balance.
        # lead_days will be NaN → these lots are excluded from labeled training.
        lot["grn_date"]  = pd.NaT
        lot["grn_qty"]   = 0.0
        recv_frac        = (lot["total_qty"] - lot["pending_qty"]).clip(lower=0) / lot["total_qty"].clip(lower=1)
        lot["completed"] = recv_frac >= _RECEIVE_COMPLETE_THRESHOLD

    lot["lead_days"] = (lot["grn_date"] - lot["issue_date"]).dt.days
    lot["age"]       = (today - lot["issue_date"]).dt.days
    lot = lot[(lot["age"] >= 0) & (lot["age"] <= 730)].copy()

    lot["issue_month"]       = lot["issue_date"].dt.month.astype(int)
    lot["issue_woy"]         = lot["issue_date"].dt.isocalendar().week.astype(int)
    lot["log_qty"]           = np.log1p(lot["total_qty"].clip(lower=0))
    lot["est_delivery_days"] = lot["est_delivery_days"].clip(lower=1)

    return lot


def _label(lot: pd.DataFrame) -> pd.Series:
    """Variable threshold: each lot's own est_delivery_days is its target.

    Completed lots (lead_days known): label = lead_days > est_delivery_days.
    Open lots already past their threshold:  label = 1  (censoring-aware).
    All other open lots: NaN (excluded from training; scored via model).
    """
    y = pd.Series(np.nan, index=lot.index)
    done = lot["completed"] & lot["lead_days"].notna()
    y[done] = (lot.loc[done, "lead_days"] > lot.loc[done, "est_delivery_days"]).astype(float)
    y[(~lot["completed"]) & (lot["age"] > lot["est_delivery_days"])] = 1.0
    return y


def _group_stats(tr: pd.DataFrame) -> dict:
    comp   = tr[tr["lead_days"].notna()]
    g_lead = float(comp["lead_days"].mean()) if not comp.empty else 7.0
    return {
        "vendor_delay_rate":  tr.groupby("vendor")["y"].mean(),
        "vendor_delay_cnt":   tr.groupby("vendor").size(),
        "article_delay_rate": tr.groupby("article_group")["y"].mean(),
        "g_lead": g_lead,
        "g_rate": float(tr["y"].mean()),
    }


def _attach_group_feats(x: pd.DataFrame, stats: dict) -> pd.DataFrame:
    x = x.copy()
    x["vendor_delay_rate"]  = x["vendor"].map(stats["vendor_delay_rate"]).fillna(stats["g_rate"])
    x["vendor_delay_cnt"]   = x["vendor"].map(stats["vendor_delay_cnt"]).fillna(0).astype(float)
    x["article_delay_rate"] = x["article_group"].map(stats["article_delay_rate"]).fillna(stats["g_rate"])
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
# Entry point
# --------------------------------------------------------------------------- #
def compute(
    po_df:     pd.DataFrame | None = None,
    grn_df:    pd.DataFrame | None = None,
    issue_csv: str | Path | None = None,
) -> dict | None:
    """Train on completed PO lots and score open (in-progress) lots.

    Parameters
    ----------
    po_df     : live PO view DataFrame (preferred)
    grn_df    : live GRN view DataFrame — provides actual receive dates for labels
    issue_csv : fallback path to Purchase_Order_issue.csv (used when po_df is None)

    Notes
    -----
    Training requires completed lots with known lead times (grn_df).
    Without grn_df the model cannot be built (returns None gracefully).
    """
    try:
        from sklearn.metrics import accuracy_score, roc_auc_score
    except Exception:  # noqa: BLE001
        return None

    # ── Load data ─────────────────────────────────────────────────────────── #
    if po_df is None or po_df.empty:
        if issue_csv is None:
            issue_csv = _ROOT / "Purchase_Order_issue.csv"
        issue_csv = Path(issue_csv)
        if not issue_csv.exists():
            return None
        po_df  = pd.read_csv(issue_csv)
        grn_df = None  # CSV has no separate GRN data

    if po_df.empty:
        return None

    today = pd.Timestamp.now().normalize()
    lot   = build_lots(po_df, grn_df, today)
    if lot.empty:
        return None

    lot["y"] = _label(lot)

    # ── Training: completed lots only (as per project requirement) ─────────── #
    labeled = lot[lot["completed"] & lot["lead_days"].notna() & lot["y"].notna()].copy()
    labeled["y"] = labeled["y"].astype(int)

    if len(labeled) < 100 or labeled["y"].nunique() < 2:
        return None

    # Time-based 70 / 30 holdout
    labeled = labeled.sort_values("issue_date").reset_index(drop=True)
    cut = labeled["issue_date"].quantile(0.70)
    tr  = labeled[labeled["issue_date"] <= cut]
    te  = labeled[labeled["issue_date"] > cut]

    as_of   = today.date()
    metrics: dict = {"labeledLots": int(len(labeled))}

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

    # Final model trained on all labeled lots
    stats = _group_stats(labeled)
    full  = _attach_group_feats(labeled, stats)
    model = _fit(full)

    # ── Score open (in-progress) lots ─────────────────────────────────────── #
    open_lots = lot[~lot["completed"] & (lot["pending_qty"] > 0)].copy()

    scored: dict[str, dict] = {}
    if not open_lots.empty:
        of    = _attach_group_feats(open_lots, stats)
        of    = _align_cats(of, full)
        probs = model.predict_proba(of[_FEATURES])[:, 1]
        for (_, r), prob_val in zip(open_lots.iterrows(), probs):
            already    = bool(r["age"] > r["est_delivery_days"])
            prob       = 1.0 if already else float(round(prob_val, 4))
            lot_no_str = str(r["LOT_NO"])
            scored[lot_no_str] = {
                "vendor":          str(r["vendor"]),
                "articleGroup":    str(r["article_group"]),
                "ageDays":         int(r["age"]),
                "estDeliveryDays": int(r["est_delivery_days"]),
                "pendingQty":      int(r["pending_qty"]),
                "delayProb":       prob,
                "riskBand":        "High" if already else _risk_band(prob_val),
                "alreadyLate":     already,
            }

    metrics["openLots"]   = int(len(scored))
    metrics["atRiskHigh"] = int(sum(1 for v in scored.values() if v["riskBand"] == "High"))

    comp        = lot[lot["completed"] & lot["lead_days"].notna()]
    vendor_lead = {
        str(k): int(round(v)) for k, v in
        comp.groupby("vendor")["lead_days"].median().items()
        if v == v
    }
    global_lead = int(round(comp["lead_days"].median())) if not comp.empty else 0

    return {
        "asOf":           str(as_of),
        "metrics":        metrics,
        "lots":           scored,
        "vendorLeadDays": vendor_lead,
        "globalLeadDays": global_lead,
        "source":         "purchase_order",
    }
