"""
Inhouse floor delay risk (Module 3C).

Trains on the already-built Inhouse rows (``inhouse._build_rows`` output — one
row per (lot, process) stage) and scores every currently-open stage with a
delay probability. Mirrors ``delay_predict_jw.py``'s approach, but scores at
the STAGE level rather than the lot level: a single Inhouse lot passes through
up to 5 sequential processes (Cutting -> Stitching -> Thread Cutting Store ->
General Store -> Final Barcode Generator), each with its own issue/receive
dates and its own expected turnaround — so "will this row be late" is scored
per stage, not once per lot.

Label: a Completed row is late if (receiveDate - issueDate).days > that row's
own expectedDays (Cutting=5, Stitching=12, ... — these vary a lot, so the
threshold is per-row, not a single fixed constant like Job Work's TARGET_DAYS).
An Open row already past its expectedDays is labeled late (1.0); an open row
still inside its expected window is censored (excluded from training).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

TARGET_MODE = "per_row_expected"  # late = actual days > that row's own expectedDays
_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
_CACHE_VERSION = "ih_v1"

_FEATURES = [
    "process", "section",
    "issue_month", "issue_woy",
    "log_qty",
    "design_rate", "design_cnt", "section_rate", "vendor_rate",
]
_CAT_FEATURES = ["process", "section"]


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
def build_stage_rows(rows: list[dict], today: pd.Timestamp) -> pd.DataFrame:
    """Turn inhouse's already-built (lot, process) rows into a training frame.

    ``rows`` is the output of ``inhouse._build_rows`` / ``inhouse.get_data()["items"]``
    — dicts with lotNo, design, section, vendor, process, issueQty, issueDate,
    receiveQty, receiveDate, ageDays, expectedDays, lotStatus (display date
    strings are "D Mon YYYY", parsed back here).
    """
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["issue_dt"]   = pd.to_datetime(df["issueDate"],   format="%d %b %Y", errors="coerce")
    df["receive_dt"] = pd.to_datetime(df["receiveDate"], format="%d %b %Y", errors="coerce")
    df["completed"]  = df["lotStatus"] == "Completed"
    df["expected"]   = pd.to_numeric(df["expectedDays"], errors="coerce").fillna(5).astype(float)
    df["age"]        = pd.to_numeric(df["ageDays"],      errors="coerce").fillna(0).astype(float)
    df["issueQty"]   = pd.to_numeric(df["issueQty"],     errors="coerce").fillna(0).clip(lower=0)

    df["lead_days"] = (df["receive_dt"] - df["issue_dt"]).dt.days
    # Fall back to age when issue_dt failed to parse — keeps the row usable
    # instead of dropping it outright.
    df["age"] = np.where(df["issue_dt"].notna(), (today - df["issue_dt"]).dt.days, df["age"])
    df = df[(df["age"] >= 0) & (df["age"] <= 500)].copy()

    df["process"] = df["process"].fillna("Unknown").astype(str)
    df["section"] = df["section"].fillna("").replace("", "Unknown").astype(str)
    df["design"]  = df["design"].fillna("Unknown").astype(str)
    df["vendor"]  = df["vendor"].fillna("").replace("", "Unknown").astype(str)

    df["issue_month"] = df["issue_dt"].dt.month.fillna(today.month).astype(int)
    df["issue_woy"]    = df["issue_dt"].dt.isocalendar().week.fillna(today.isocalendar()[1]).astype(int)
    df["log_qty"]      = np.log1p(df["issueQty"])

    return df.reset_index(drop=True)


def _label(df: pd.DataFrame) -> pd.Series:
    y = pd.Series(np.nan, index=df.index)
    done = df["completed"] & df["lead_days"].notna()
    y[done] = (df.loc[done, "lead_days"] > df.loc[done, "expected"]).astype(float)
    y[(~df["completed"]) & (df["age"] > df["expected"])] = 1.0
    return y


def _group_stats(tr: pd.DataFrame) -> dict:
    g_rate = float(tr["y"].mean())
    return {
        "design_rate":  tr.groupby("design")["y"].mean(),
        "design_cnt":   tr.groupby("design").size(),
        "section_rate": tr.groupby("section")["y"].mean(),
        "vendor_rate":  tr.groupby("vendor")["y"].mean(),
        "g_rate": g_rate,
    }


def _attach_group_feats(x: pd.DataFrame, stats: dict) -> pd.DataFrame:
    x = x.copy()
    x["design_rate"]  = x["design"].map(stats["design_rate"]).fillna(stats["g_rate"])
    x["design_cnt"]   = x["design"].map(stats["design_cnt"]).fillna(0).astype(float)
    x["section_rate"] = x["section"].map(stats["section_rate"]).fillna(stats["g_rate"])
    x["vendor_rate"]  = x["vendor"].map(stats["vendor_rate"]).fillna(stats["g_rate"])
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


# A process needs at least this many labeled (completed, resolved) historical
# rows, WITH both outcomes represented, before its lots are scored by the
# model. Below this, per-design/vendor/section rates are estimated from 0-2
# examples — pure noise that flips wildly between refreshes as the training
# window shifts (found via audit: Final Barcode Generator had only 28 labeled
# rows and Thread Cutting Store only 9, both 100% "late" — nothing to learn a
# real contrast from). Those processes fall back to a plain, deterministic
# age-vs-expected-days ramp instead of a model prediction.
_MIN_LABELED_PER_PROCESS = 50


def _heuristic_prob(age: float, expected: float) -> float:
    """Deterministic fallback for low-data processes: linear ramp of elapsed
    age toward the expected turnaround (capped at 1.0 — already-late rows are
    handled separately as a hard 100%)."""
    if expected <= 0:
        return 0.0
    return min(1.0, max(0.0, age / expected))


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def compute(rows: list[dict] | None = None) -> dict | None:
    """Train on labeled (lot, process) stage-rows and score every open one.

    ``rows`` — the list of dicts from ``inhouse.get_data()["items"]`` /
    ``inhouse._RAW_ROWS``. Returns ``None`` if data is missing or insufficient.
    Output keyed by ``(lotNo, process)`` — pass the same key when merging
    scores back onto inhouse rows, since a lot can have several open stages.
    """
    try:
        from sklearn.metrics import accuracy_score, roc_auc_score
    except Exception:  # noqa: BLE001
        return None

    if not rows:
        return None

    today = pd.Timestamp.now().normalize()
    df = build_stage_rows(rows, today)
    if df.empty:
        return None

    df["y"] = _label(df)
    labeled = df.dropna(subset=["y"]).copy()
    labeled["y"] = labeled["y"].astype(int)
    if len(labeled) < 100 or labeled["y"].nunique() < 2:
        return None

    labeled = labeled.sort_values("issue_dt").reset_index(drop=True)
    cut = labeled["issue_dt"].quantile(0.70)
    tr  = labeled[labeled["issue_dt"] <= cut]
    te  = labeled[labeled["issue_dt"] > cut]

    as_of = today.date()
    metrics: dict = {"labeledRows": int(len(labeled))}

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

    stats = _group_stats(labeled)
    full  = _attach_group_feats(labeled, stats)
    model = _fit(full)

    # Processes without enough resolved history (or with only one outcome
    # ever observed) don't get a model prediction — see _MIN_LABELED_PER_PROCESS.
    proc_stats = labeled.groupby("process")["y"].agg(["count", "nunique"])
    low_data_processes = set(
        proc_stats[(proc_stats["count"] < _MIN_LABELED_PER_PROCESS) | (proc_stats["nunique"] < 2)].index
    )
    if low_data_processes:
        print(f"[delay_predict_inhouse] low-data processes (heuristic fallback): {sorted(low_data_processes)}")

    open_rows = df[~df["completed"]].copy()

    scored: dict[str, dict] = {}
    if not open_rows.empty:
        of    = _attach_group_feats(open_rows, stats)
        of    = _align_cats(of, full)
        probs = model.predict_proba(of[_FEATURES])[:, 1]
        for (_, r), prob_val in zip(open_rows.iterrows(), probs):
            already = bool(r["age"] > r["expected"])
            if already:
                prob, band = 1.0, "High"
            elif r["process"] in low_data_processes:
                prob = round(_heuristic_prob(r["age"], r["expected"]), 4)
                band = _risk_band(prob)
            else:
                prob, band = float(round(prob_val, 4)), _risk_band(prob_val)
            key = f"{r['lotNo']}::{r['process']}"
            scored[key] = {
                "lotNo":       r["lotNo"],
                "process":     r["process"],
                "design":      r["design"],
                "ageDays":     int(r["age"]),
                "expectedDays": int(r["expected"]),
                "delayProb":   prob,
                "riskBand":    band,
                "alreadyLate": already,
            }

    metrics["openRows"]   = int(len(scored))
    metrics["atRiskHigh"] = int(sum(1 for v in scored.values() if v["riskBand"] == "High"))

    return {
        "asOf":       str(as_of),
        "metrics":    metrics,
        "stages":     scored,
        "source":     "inhouse_stage_rows",
    }
