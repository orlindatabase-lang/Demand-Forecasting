"""
Feature engineering for the daily demand panel.

All temporal features are built from PAST information only:
lags use ``groupby.shift(k)`` and rolling stats are computed on a ``shift(1)``
series, so the current day's target never leaks into its own features.

Public entry point
-------------------
make_features(panel, static_attrs, id_col) -> (frame, feature_cols, categorical_cols)
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

import config


# --------------------------------------------------------------------------- #
# Temporal features
# --------------------------------------------------------------------------- #
def add_lag_features(df: pd.DataFrame, id_col: str,
                     target: str = config.TARGET,
                     lags: List[int] | None = None) -> pd.DataFrame:
    """Add lag_<k> columns of the target within each id group."""
    lags = lags or config.LAGS
    grp = df.groupby(id_col)[target]
    for lag in lags:
        df[f"lag_{lag}"] = grp.shift(lag)
    return df


def add_rolling_features(df: pd.DataFrame, id_col: str,
                         target: str = config.TARGET) -> pd.DataFrame:
    """
    Add rolling mean/std features on the PAST target (shifted by 1 day) so the
    window never includes the current observation, and re-group so windows do
    not bleed across ids.
    """
    shifted = df.groupby(id_col)[target].shift(1)
    g = shifted.groupby(df[id_col])

    for w in config.ROLLING_MEAN_WINDOWS:
        df[f"rolling_mean_{w}"] = (
            g.rolling(w, min_periods=1).mean().reset_index(level=0, drop=True))
    for w in config.ROLLING_STD_WINDOWS:
        df[f"rolling_std_{w}"] = (
            g.rolling(w, min_periods=2).std().reset_index(level=0, drop=True))
    return df


def add_time_features(df: pd.DataFrame, date_col: str = config.DATE_COL) -> pd.DataFrame:
    """Add calendar features from the week-start date (weekly grain — day-of-week
    and weekend flags no longer apply)."""
    d = df[date_col].dt
    df["week_of_year"] = d.isocalendar().week.astype("int16")
    df["month"] = d.month.astype("int16")
    df["quarter"] = d.quarter.astype("int16")
    df["year"] = d.year.astype("int16")
    return df


# --------------------------------------------------------------------------- #
# Product features
# --------------------------------------------------------------------------- #
def add_product_features(df: pd.DataFrame, static_attrs: pd.DataFrame,
                         id_col: str, date_col: str = config.DATE_COL) -> pd.DataFrame:
    """
    Merge static product attributes onto the panel and compute product_age_days
    = days since launch (clipped at 0; -1 where the launch date is unknown).
    """
    df = df.merge(static_attrs, on=id_col, how="left")

    if config.COL_LAUNCH_DATE in df.columns:
        age = (df[date_col] - df[config.COL_LAUNCH_DATE]).dt.days
        # negative age (order before recorded launch) -> 0; missing -> -1
        df["product_age_days"] = age.clip(lower=0).fillna(-1).astype("int32")
        df = df.drop(columns=[config.COL_LAUNCH_DATE])
    else:
        df["product_age_days"] = -1
    return df


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def make_features(panel: pd.DataFrame, static_attrs: pd.DataFrame, id_col: str
                  ) -> Tuple[pd.DataFrame, List[str], List[str]]:
    """
    Build the full feature matrix for one aggregation level.

    Parameters
    ----------
    panel : dense daily panel with columns [id_col, date, net_demand, ...]
    static_attrs : one attribute row per id (from build_static_attrs)
    id_col : entity column for this level

    Returns
    -------
    (frame, feature_cols, categorical_cols)
        ``frame`` is the panel plus all feature columns; ``feature_cols`` is the
        ordered model-input list; ``categorical_cols`` is the subset to be cast
        to pandas ``category`` dtype for LightGBM.
    """
    df = panel.sort_values([id_col, config.DATE_COL]).reset_index(drop=True).copy()

    df = add_lag_features(df, id_col)
    df = add_rolling_features(df, id_col)
    df = add_time_features(df)
    df = add_product_features(df, static_attrs, id_col)

    lag_cols = [f"lag_{k}" for k in config.LAGS]
    roll_cols = ([f"rolling_mean_{w}" for w in config.ROLLING_MEAN_WINDOWS]
                 + [f"rolling_std_{w}" for w in config.ROLLING_STD_WINDOWS])
    time_cols = ["week_of_year", "month", "quarter", "year"]
    product_cat_cols = [c for c in config.PRODUCT_ATTR_COLS if c in df.columns]
    product_num_cols = ["product_age_days"]

    feature_cols = lag_cols + roll_cols + time_cols + product_cat_cols + product_num_cols
    categorical_cols = product_cat_cols + ["month"]

    # LightGBM needs categoricals as pandas 'category' dtype.
    for c in categorical_cols:
        df[c] = df[c].astype("category")

    return df, feature_cols, categorical_cols
