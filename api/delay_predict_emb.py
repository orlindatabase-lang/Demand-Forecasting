"""Embroidery delay prediction.

Data sources:
  issue_df : View_Dboard_Trans_FAB_JOB_ISS_EMB_NEW_Data_For_Test_BI
  grn_df   : View_Dboard_Trans_FAB_JOB_GRN_Data_For_Test_BI

Training : completed lots ONLY (grn_qty / issue_qty >= 90%, matching
           embroidery.py's own open/closed rule)
Label    : lead_days > EXPECTED_DAYS (fixed embroidery turnaround target)
Features : vendor, section, log_qty, issue_month, issue_woy,
           vendor_delay_rate, vendor_delay_cnt, section_delay_rate
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

EXPECTED_DAYS = int(os.getenv("EMB_DELAY_TARGET_DAYS", "10"))
_CACHE_DIR     = Path(__file__).resolve().parent / ".cache"
_CACHE_VERSION = "emb_v1"

_FEATURES = [
    "vendor", "section",
    "log_qty", "issue_month", "issue_woy",
    "vendor_delay_rate", "vendor_delay_cnt", "section_delay_rate",
]
_CAT_FEATURES = ["vendor", "section"]

_RECEIVE_COMPLETE_THRESHOLD = 0.90   # grn_qty / issue_qty >= this -> lot is "completed"


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
# Helpers (duplicated from embroidery.py to keep this module self-contained)
# --------------------------------------------------------------------------- #
def _design_from_article(article: str) -> str:
    s = str(article).strip()
    m = re.match(r"^([^-]+(?:-\d+)?)\s*-", s)
    if m:
        return m.group(1).strip()
    return s.split(" ")[0] if s else ""


def _section_from_article(article: str) -> str:
    a = str(article).upper()
    if "DUPATTA" in a:
        return "Dupatta"
    for kw in ("PLAZZO", "PALAZZO", "BOTTOM", "PANT", "TROUSER", "SKIRT"):
        if kw in a:
            return "Bottom"
    return "Top"


# --------------------------------------------------------------------------- #
# Feature engineering
# --------------------------------------------------------------------------- #
def build_lots(
    issue_df: pd.DataFrame,
    grn_df:   pd.DataFrame | None,
    today:    pd.Timestamp,
) -> pd.DataFrame:
    """One row per embroidery lot with features + label inputs."""
    issue = issue_df.copy()
    issue.columns = [c.upper() for c in issue.columns]

    issue["LOT_NO"] = issue["LOT_NO"].astype(str).str.strip()
    issue = issue[issue["LOT_NO"].str.len() > 0]
    if issue.empty:
        return pd.DataFrame()

    issue["VOUCHER_DATE"] = pd.to_datetime(issue.get("VOUCHER_DATE"), errors="coerce")
    issue["QTY_SIZE"]     = pd.to_numeric(issue.get("QTY_SIZE"), errors="coerce").fillna(0)
    issue["PARTY_NAME"]   = issue.get("PARTY_NAME", pd.Series("Unknown", index=issue.index)).fillna("Unknown").astype(str).str.strip()
    issue["ARTICLE_NAME"] = issue.get("ARTICLE_NAME", pd.Series("", index=issue.index)).fillna("").astype(str)
    issue["_section"]     = issue["ARTICLE_NAME"].apply(_section_from_article)

    gi = issue.groupby("LOT_NO")
    lot = pd.DataFrame({
        "issue_date":  gi["VOUCHER_DATE"].min(),
        "vendor":      gi["PARTY_NAME"].agg(lambda s: s.mode().iat[0] if not s.empty else "Unknown"),
        "section":     gi["_section"].agg(lambda s: s.mode().iat[0] if not s.empty else "Top"),
        "issue_qty":   gi["QTY_SIZE"].sum(),
    }).reset_index()

    # ── Completion date from GRN ───────────────────────────────────────────── #
    if grn_df is not None and not grn_df.empty:
        grn = grn_df.copy()
        grn.columns = [c.upper() for c in grn.columns]
        grn["LOT_NO"]       = grn["LOT_NO"].astype(str).str.strip()
        grn["VOUCHER_DATE"] = pd.to_datetime(grn.get("VOUCHER_DATE"), errors="coerce")
        grn["GRN_QTY"]      = pd.to_numeric(grn.get("GRN_QTY"), errors="coerce").fillna(0)
        grn_agg = grn.groupby("LOT_NO").agg(
            grn_date=("VOUCHER_DATE", "max"),
            grn_qty=("GRN_QTY", "sum"),
        ).reset_index()
        lot = lot.merge(grn_agg, on="LOT_NO", how="left")
    else:
        lot["grn_date"] = pd.NaT
        lot["grn_qty"]  = 0.0

    lot["grn_qty"]   = lot["grn_qty"].fillna(0.0)
    recv_frac        = lot["grn_qty"] / lot["issue_qty"].clip(lower=1)
    lot["completed"] = recv_frac >= _RECEIVE_COMPLETE_THRESHOLD

    lot["lead_days"] = (lot["grn_date"] - lot["issue_date"]).dt.days
    lot["age"]       = (today - lot["issue_date"]).dt.days
    lot = lot[(lot["age"] >= 0) & (lot["age"] <= 500)].copy()

    lot["issue_month"] = lot["issue_date"].dt.month.astype(int)
    lot["issue_woy"]   = lot["issue_date"].dt.isocalendar().week.astype(int)
    lot["log_qty"]     = np.log1p(lot["issue_qty"].clip(lower=0))

    return lot


def _label(lot: pd.DataFrame) -> pd.Series:
    y = pd.Series(np.nan, index=lot.index)
    done = lot["completed"] & lot["lead_days"].notna()
    y[done] = (lot.loc[done, "lead_days"] > EXPECTED_DAYS).astype(float)
    y[(~lot["completed"]) & (lot["age"] > EXPECTED_DAYS)] = 1.0
    return y


def _group_stats(tr: pd.DataFrame) -> dict:
    return {
        "vendor_delay_rate":  tr.groupby("vendor")["y"].mean(),
        "vendor_delay_cnt":   tr.groupby("vendor").size(),
        "section_delay_rate": tr.groupby("section")["y"].mean(),
        "g_rate": float(tr["y"].mean()),
    }


def _attach_group_feats(x: pd.DataFrame, stats: dict) -> pd.DataFrame:
    x = x.copy()
    x["vendor_delay_rate"]  = x["vendor"].map(stats["vendor_delay_rate"]).fillna(stats["g_rate"])
    x["vendor_delay_cnt"]   = x["vendor"].map(stats["vendor_delay_cnt"]).fillna(0).astype(float)
    x["section_delay_rate"] = x["section"].map(stats["section_delay_rate"]).fillna(stats["g_rate"])
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
    issue_df: pd.DataFrame | None = None,
    grn_df:   pd.DataFrame | None = None,
) -> dict | None:
    """Train on completed embroidery lots and score open (in-progress) lots.

    Returns None if data is missing or insufficient (same graceful-degrade
    contract as delay_predict_jw.py / delay_predict_po.py).
    """
    try:
        from sklearn.metrics import accuracy_score, roc_auc_score
    except Exception:  # noqa: BLE001
        return None

    if issue_df is None or issue_df.empty:
        return None

    today = pd.Timestamp.now().normalize()
    lot   = build_lots(issue_df, grn_df, today)
    if lot.empty:
        return None

    lot["y"] = _label(lot)

    labeled = lot[lot["completed"] & lot["lead_days"].notna() & lot["y"].notna()].copy()
    labeled["y"] = labeled["y"].astype(int)
    if len(labeled) < 100 or labeled["y"].nunique() < 2:
        return None

    # Time-based 70/30 holdout
    labeled = labeled.sort_values("issue_date").reset_index(drop=True)
    cut = labeled["issue_date"].quantile(0.70)
    tr  = labeled[labeled["issue_date"] <= cut]
    te  = labeled[labeled["issue_date"] > cut]

    as_of   = today.date()
    metrics: dict = {"expectedDays": EXPECTED_DAYS, "labeledLots": int(len(labeled))}

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
    open_lots = lot[~lot["completed"]].copy()

    scored: dict[str, dict] = {}
    if not open_lots.empty:
        of    = _attach_group_feats(open_lots, stats)
        of    = _align_cats(of, full)
        probs = model.predict_proba(of[_FEATURES])[:, 1]
        for (_, r), prob_val in zip(open_lots.iterrows(), probs):
            already    = bool(r["age"] > EXPECTED_DAYS)
            prob       = 1.0 if already else float(round(prob_val, 4))
            lot_no_str = str(r["LOT_NO"])
            scored[lot_no_str] = {
                "vendor":      str(r["vendor"]),
                "section":     str(r["section"]),
                "ageDays":     int(r["age"]),
                "delayProb":   prob,
                "riskBand":    "High" if already else _risk_band(prob_val),
                "alreadyLate": already,
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
        "source":         "embroidery",
    }
