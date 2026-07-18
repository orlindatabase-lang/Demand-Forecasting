"""
Evaluation metrics, naive baselines, and comparison tables.

Metrics implemented without scikit-learn so the only heavy dependency is
LightGBM. All metric functions accept array-likes and ignore NaNs pairwise.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

import config


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _align(y_true, y_pred):
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    return y_true[mask], y_pred[mask]


def mae(y_true, y_pred) -> float:
    y_true, y_pred = _align(y_true, y_pred)
    return float(np.mean(np.abs(y_true - y_pred))) if y_true.size else np.nan


def rmse(y_true, y_pred) -> float:
    y_true, y_pred = _align(y_true, y_pred)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2))) if y_true.size else np.nan


def mape(y_true, y_pred) -> float:
    """Mean absolute percentage error over non-zero actuals (percent)."""
    y_true, y_pred = _align(y_true, y_pred)
    nz = y_true != 0
    if nz.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((y_true[nz] - y_pred[nz]) / y_true[nz])) * 100)


def smape(y_true, y_pred) -> float:
    """Symmetric MAPE (percent); robust to zeros in the actuals."""
    y_true, y_pred = _align(y_true, y_pred)
    denom = np.abs(y_true) + np.abs(y_pred)
    nz = denom != 0
    if nz.sum() == 0:
        return np.nan
    return float(np.mean(2.0 * np.abs(y_pred - y_true)[nz] / denom[nz]) * 100)


def r2(y_true, y_pred) -> float:
    y_true, y_pred = _align(y_true, y_pred)
    if y_true.size < 2:
        return np.nan
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else np.nan


def compute_metrics(y_true, y_pred) -> Dict[str, float]:
    """Return all metrics as a dict keyed by metric name."""
    return {
        "MAE": mae(y_true, y_pred),
        "RMSE": rmse(y_true, y_pred),
        "MAPE": mape(y_true, y_pred),
        "SMAPE": smape(y_true, y_pred),
        "R2": r2(y_true, y_pred),
    }


# --------------------------------------------------------------------------- #
# Naive baselines
# --------------------------------------------------------------------------- #
def baseline_predictions(eval_df: pd.DataFrame, moving_avg_window: int = 7
                         ) -> Dict[str, np.ndarray]:
    """
    Produce baseline predictions for an evaluation slice using the lag / rolling
    feature columns already present on the frame:

      * Last Day Forecast            -> lag_1
      * Last Week Same Day Forecast  -> lag_7
      * Moving Average Forecast      -> rolling_mean_<window>

    Missing feature values fall back to 0 (a cold-start SKU's first days).
    """
    out: Dict[str, np.ndarray] = {}
    out["Last Day"] = eval_df.get("lag_1", pd.Series(0, index=eval_df.index)).fillna(0).to_numpy()
    out["Last Week Same Day"] = eval_df.get("lag_7", pd.Series(0, index=eval_df.index)).fillna(0).to_numpy()
    ma_col = f"rolling_mean_{moving_avg_window}"
    out[f"Moving Avg ({moving_avg_window}d)"] = (
        eval_df.get(ma_col, pd.Series(0, index=eval_df.index)).fillna(0).to_numpy())
    return out


# --------------------------------------------------------------------------- #
# Comparison table
# --------------------------------------------------------------------------- #
def comparison_table(results: Dict[str, Dict[str, float]]) -> pd.DataFrame:
    """
    Build a model x metric comparison table sorted by MAE (best first).

    ``results`` maps model name -> metric dict (from :func:`compute_metrics`).
    """
    table = pd.DataFrame(results).T
    table.index.name = "model"
    if "MAE" in table.columns:
        table = table.sort_values("MAE")
    return table.reset_index()


# --------------------------------------------------------------------------- #
# Feature-importance business interpretation
# --------------------------------------------------------------------------- #
_INTERPRETATIONS = {
    "lag_1": "Yesterday's demand — short-term momentum / autocorrelation.",
    "lag_7": "Same weekday last week — captures weekly purchase rhythm.",
    "lag_14": "Two-week-ago demand — bi-weekly pattern / restock cycle.",
    "lag_28": "Four-week-ago demand — monthly seasonality.",
    "lag_56": "Eight-week-ago demand — longer seasonal memory.",
    "rolling_mean_7": "Avg demand last 7 days — current run-rate / trend level.",
    "rolling_mean_14": "Avg demand last 14 days — smoothed short-term trend.",
    "rolling_mean_28": "Avg demand last 28 days — stable monthly base level.",
    "rolling_std_7": "7-day volatility — demand stability vs. spikiness.",
    "rolling_std_28": "28-day volatility — longer-run demand variability.",
    "day_of_week": "Weekday effect — weekend vs. weekday buying.",
    "week_of_year": "Seasonal week — festival / season-of-year signal.",
    "month": "Month effect — seasonal apparel demand.",
    "quarter": "Quarterly seasonality.",
    "is_weekend": "Weekend flag — higher consumer ordering.",
    "is_month_start": "Month-start effect (salary-cycle buying).",
    "is_month_end": "Month-end effect.",
    "product_age_days": "Days since launch — new-launch ramp vs. maturity/decline.",
    "DESIGN_GROUP": "Design family — category-level demand level.",
    "COLOR": "Colour preference effect.",
    "SECTION": "Garment section (e.g. Top) demand level.",
    "CATALOG_NAME": "Catalogue / collection effect.",
}


def interpret_importance(importance_df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """
    Attach a plain-language business interpretation to the top-N features.

    ``importance_df`` must have columns ['feature', 'importance'].
    """
    top = importance_df.sort_values("importance", ascending=False).head(top_n).copy()
    top["interpretation"] = top["feature"].map(_INTERPRETATIONS).fillna(
        "Engineered feature contributing to the demand signal.")
    top = top.reset_index(drop=True)
    top.index += 1
    top.index.name = "rank"
    return top.reset_index()
