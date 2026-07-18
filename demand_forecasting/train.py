"""
Model training: time-based splitting, walk-forward validation, LightGBM fit,
and feature-importance extraction.

No random shuffling is ever used — all splits respect the time order so the
model is only ever validated on data that comes strictly after its training
window.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError(
        "LightGBM is required for training. Install it with "
        "`pip install lightgbm` (see requirements.txt)."
    ) from exc

import config
import evaluate


# --------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------- #
@dataclass
class TimeSplit:
    """Boolean masks for a single chronological train/val/test split."""
    train: pd.Series
    val: pd.Series
    test: pd.Series
    val_start: pd.Timestamp
    test_start: pd.Timestamp


def time_based_split(df: pd.DataFrame,
                     val_days: int = config.VAL_DAYS,
                     test_days: int = config.TEST_DAYS,
                     date_col: str = config.DATE_COL) -> TimeSplit:
    """
    Split chronologically: the last ``test_days`` form the test set, the
    preceding ``val_days`` form the validation set, everything earlier is train.
    """
    max_date = df[date_col].max()
    test_start = max_date - pd.Timedelta(days=test_days - 1)
    val_start = test_start - pd.Timedelta(days=val_days)

    test = df[date_col] >= test_start
    val = (df[date_col] >= val_start) & (df[date_col] < test_start)
    train = df[date_col] < val_start
    return TimeSplit(train, val, test, val_start, test_start)


def walk_forward_folds(df: pd.DataFrame,
                       n_folds: int = config.WALK_FORWARD_FOLDS,
                       horizon: int = config.WALK_FORWARD_HORIZON,
                       date_col: str = config.DATE_COL
                       ) -> List[Tuple[pd.Series, pd.Series]]:
    """
    Build expanding-window walk-forward folds.

    The most recent ``n_folds * horizon`` days are carved into consecutive
    validation blocks of ``horizon`` days; each fold trains on everything before
    its block. Returns a list of ``(train_mask, val_mask)`` boolean Series.
    """
    max_date = df[date_col].max()
    folds: List[Tuple[pd.Series, pd.Series]] = []
    for i in range(n_folds, 0, -1):
        val_end = max_date - pd.Timedelta(days=(i - 1) * horizon)
        val_start = val_end - pd.Timedelta(days=horizon - 1)
        val_mask = (df[date_col] >= val_start) & (df[date_col] <= val_end)
        train_mask = df[date_col] < val_start
        if train_mask.sum() and val_mask.sum():
            folds.append((train_mask, val_mask))
    return folds


# --------------------------------------------------------------------------- #
# LightGBM training
# --------------------------------------------------------------------------- #
def train_lightgbm(train_df: pd.DataFrame, val_df: pd.DataFrame,
                   feature_cols: List[str], categorical_cols: List[str],
                   target: str = config.TARGET,
                   params: Dict | None = None) -> "lgb.LGBMRegressor":
    """
    Fit a LightGBM regressor with early stopping on the validation set.

    If ``val_df`` is empty, the model trains for the full ``n_estimators`` with
    no early stopping.
    """
    params = dict(params or config.LGBM_PARAMS)
    model = lgb.LGBMRegressor(**params)

    fit_kwargs: Dict[str, object] = {
        "categorical_feature": categorical_cols,
    }
    if val_df is not None and len(val_df):
        model.fit(
            train_df[feature_cols], train_df[target],
            eval_set=[(val_df[feature_cols], val_df[target])],
            eval_metric="mae",
            callbacks=[
                lgb.early_stopping(config.EARLY_STOPPING_ROUNDS, verbose=False),
                lgb.log_evaluation(0),
            ],
            **fit_kwargs,
        )
    else:
        model.fit(train_df[feature_cols], train_df[target], **fit_kwargs)
    return model


def get_feature_importance(model: "lgb.LGBMRegressor",
                           feature_cols: List[str]) -> pd.DataFrame:
    """Return a [feature, importance] frame using gain importance."""
    booster = model.booster_
    gain = booster.feature_importance(importance_type="gain")
    return (
        pd.DataFrame({"feature": feature_cols, "importance": gain})
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )


# --------------------------------------------------------------------------- #
# Orchestration helpers
# --------------------------------------------------------------------------- #
def run_walk_forward(df: pd.DataFrame, feature_cols: List[str],
                     categorical_cols: List[str],
                     target: str = config.TARGET) -> pd.DataFrame:
    """
    Run walk-forward CV and return per-fold LightGBM metrics.

    Each fold trains a fresh model on the expanding window and scores the held
    out horizon. Useful as a stability check on top of the single test split.
    """
    folds = walk_forward_folds(df)
    records = []
    for k, (train_mask, val_mask) in enumerate(folds, start=1):
        model = train_lightgbm(df[train_mask], df[val_mask],
                               feature_cols, categorical_cols, target)
        preds = model.predict(df.loc[val_mask, feature_cols])
        metrics = evaluate.compute_metrics(df.loc[val_mask, target], preds)
        metrics["fold"] = k
        metrics["best_iteration"] = getattr(model, "best_iteration_", None)
        records.append(metrics)
    out = pd.DataFrame(records)
    return out[["fold", "MAE", "RMSE", "MAPE", "SMAPE", "R2", "best_iteration"]] \
        if not out.empty else out


def fit_final_model(df: pd.DataFrame, feature_cols: List[str],
                    categorical_cols: List[str],
                    n_estimators: int | None = None,
                    target: str = config.TARGET) -> "lgb.LGBMRegressor":
    """
    Train the production model on ALL available rows (no early stopping).

    ``n_estimators`` is normally the best iteration discovered on the test
    split; if None the configured default is used.
    """
    params = dict(config.LGBM_PARAMS)
    if n_estimators:
        params["n_estimators"] = int(n_estimators)
    model = lgb.LGBMRegressor(**params)
    model.fit(df[feature_cols], df[target], categorical_feature=categorical_cols)
    return model
