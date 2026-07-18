"""
Recursive multi-step forecasting (vectorised).

Lag / rolling features depend on the target itself, so a multi-day forecast is
generated recursively. With tens of thousands of SKUs, recomputing pandas
group-by features every day is far too slow, so the recursion is vectorised:

  * each SKU's recent demand is kept in a NumPy matrix ``H`` of shape
    (n_ids, W) where ``W = max(lags)``; the most recent day is the last column,
  * for each forecast day the lag and rolling features are read directly from
    ``H`` with array slicing (no group-by),
  * the day's prediction is appended as the new last column and ``H`` is rolled
    left by one, keeping the cost per step constant.

The feature maths here mirrors ``feature_engineering`` exactly:
``lag_k = H[:, -k]`` and ``rolling_*_w`` are computed on the ``shift(1)`` window
(the ``w`` days ending yesterday), with the same ``min_periods`` rules.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

import config


class RecursiveForecaster:
    """
    Roll a trained LightGBM model forward day-by-day for every id in the panel.

    Parameters
    ----------
    model : trained LightGBM regressor
    feature_cols : ordered model-input columns (same object used at fit time)
    static_attrs : per-id static attribute table (product attrs + LAUNCH_DATE)
    id_col : entity column (listing_sku_code or DESIGN_NO)
    cat_dtypes : {column -> pandas CategoricalDtype} captured from the training
        feature matrix, so categorical features encode identically at predict
        time. Numeric features are passed through untouched.
    """

    def __init__(self, model, feature_cols: List[str], static_attrs: pd.DataFrame,
                 id_col: str, cat_dtypes: Dict[str, pd.CategoricalDtype] | None = None,
                 verbose: bool = True) -> None:
        self.model = model
        self.feature_cols = feature_cols
        self.static_attrs = static_attrs
        self.id_col = id_col
        self.cat_dtypes = cat_dtypes or {}
        self.verbose = verbose
        self.W = max(config.LAGS)  # history width needed for the deepest lag

    # ------------------------------------------------------------------ #
    # Setup helpers
    # ------------------------------------------------------------------ #
    def _build_history_matrix(self, panel: pd.DataFrame, ids: np.ndarray,
                              last_date: pd.Timestamp) -> np.ndarray:
        """(n_ids, W) matrix of recent weekly net_demand; NaN where a week is absent."""
        date_cols = pd.date_range(end=last_date, periods=self.W, freq="7D")  # last W weeks
        recent = panel[panel[config.DATE_COL] >= date_cols[0]]
        wide = (recent.pivot_table(index=self.id_col, columns=config.DATE_COL,
                                   values=config.TARGET, aggfunc="sum")
                      .reindex(index=ids, columns=date_cols))
        return wide.to_numpy(dtype="float64")

    def _static_frame(self, ids: np.ndarray) -> pd.DataFrame:
        """Per-id static attributes aligned to ``ids`` order."""
        s = self.static_attrs.set_index(self.id_col).reindex(ids)
        s.index.name = self.id_col
        return s

    # ------------------------------------------------------------------ #
    # Vectorised rolling stats with pandas-compatible NaN / min_periods
    # ------------------------------------------------------------------ #
    @staticmethod
    def _win_mean(H: np.ndarray, w: int) -> np.ndarray:
        sl = H[:, -w:]
        cnt = np.sum(~np.isnan(sl), axis=1)
        with np.errstate(invalid="ignore"):
            mean = np.nansum(sl, axis=1) / np.where(cnt > 0, cnt, np.nan)
        return mean  # NaN where no observations (min_periods=1)

    @staticmethod
    def _win_std(H: np.ndarray, w: int) -> np.ndarray:
        sl = H[:, -w:]
        cnt = np.sum(~np.isnan(sl), axis=1)
        mean = np.where(cnt > 0, np.nansum(sl, axis=1) / np.where(cnt > 0, cnt, np.nan), np.nan)
        dev2 = np.nansum((sl - mean[:, None]) ** 2, axis=1)
        with np.errstate(invalid="ignore"):
            var = dev2 / np.where(cnt >= 2, cnt - 1, np.nan)  # ddof=1, min_periods=2
        return np.sqrt(var)

    # ------------------------------------------------------------------ #
    # Per-step feature frame
    # ------------------------------------------------------------------ #
    def _features_for_day(self, H: np.ndarray, day: pd.Timestamp,
                          static: pd.DataFrame, ids: np.ndarray) -> pd.DataFrame:
        data: Dict[str, np.ndarray] = {}

        # lag_k = demand k days before `day`  ->  column -k of H
        for k in config.LAGS:
            data[f"lag_{k}"] = H[:, -k]
        # rolling features over the shift(1) window (days ending yesterday)
        for w in config.ROLLING_MEAN_WINDOWS:
            data[f"rolling_mean_{w}"] = self._win_mean(H, w)
        for w in config.ROLLING_STD_WINDOWS:
            data[f"rolling_std_{w}"] = self._win_std(H, w)

        # calendar features (scalar for the week `day`, broadcast across ids).
        # Must mirror feature_engineering.add_time_features exactly (weekly grain).
        n = len(ids)
        data["week_of_year"] = np.full(n, int(day.isocalendar()[1]), dtype="int16")
        data["month"] = np.full(n, day.month, dtype="int16")
        data["quarter"] = np.full(n, day.quarter, dtype="int16")
        data["year"] = np.full(n, day.year, dtype="int16")

        # product features
        for col in config.PRODUCT_ATTR_COLS:
            if col in static.columns:
                data[col] = static[col].to_numpy()
        if config.COL_LAUNCH_DATE in static.columns:
            age = (day - static[config.COL_LAUNCH_DATE]).dt.days
            data["product_age_days"] = age.clip(lower=0).fillna(-1).astype("int32").to_numpy()
        else:
            data["product_age_days"] = np.full(n, -1, dtype="int32")

        frame = pd.DataFrame(data)
        # cast categoricals to the exact training dtypes so codes line up
        for col, dtype in self.cat_dtypes.items():
            if col in frame.columns:
                frame[col] = frame[col].astype(dtype)
        return frame[self.feature_cols]

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def forecast(self, panel: pd.DataFrame, horizon: int) -> pd.DataFrame:
        """
        Generate a ``horizon``-day forecast for every id in ``panel``.

        Returns a frame [forecast_date, sku, forecast_qty]; ``forecast_qty`` is
        clipped at 0 (demand can't be negative) and kept as a 2-decimal float.
        """
        if panel.empty:
            return pd.DataFrame(columns=["forecast_date", "sku", "forecast_qty"])

        ids = np.sort(panel[self.id_col].dropna().unique())
        last_date = panel[config.DATE_COL].max()
        static = self._static_frame(ids)
        H = self._build_history_matrix(panel, ids, last_date)

        collected: List[pd.DataFrame] = []
        for step in range(1, horizon + 1):
            day = last_date + pd.Timedelta(weeks=step)   # next week-start (Monday)
            X = self._features_for_day(H, day, static, ids)
            preds = np.clip(self.model.predict(X), 0, None)

            # roll history left and append today's prediction as the new last day
            H = np.concatenate([H[:, 1:], preds[:, None]], axis=1)

            collected.append(pd.DataFrame({
                "forecast_date": day,
                "sku": ids,
                "forecast_qty": np.round(preds, 2),
            }))
            if self.verbose and (step % 10 == 0 or step == horizon):
                print(f"  [forecast] step {step}/{horizon} ({day.date()})", flush=True)

        return (pd.concat(collected, ignore_index=True)
                .sort_values(["sku", "forecast_date"]).reset_index(drop=True))


def generate_horizons(model, feature_cols: List[str], static_attrs: pd.DataFrame,
                      id_col: str, panel: pd.DataFrame,
                      cat_dtypes: Dict[str, pd.CategoricalDtype] | None = None,
                      horizons: List[int] | None = None,
                      verbose: bool = True) -> dict:
    """
    Produce a forecast frame for each requested horizon.

    The longest horizon is computed once and sliced for the shorter ones, so the
    recursion runs only a single time. Returns ``{horizon: dataframe}``.
    """
    horizons = sorted(horizons or config.FORECAST_HORIZONS)
    forecaster = RecursiveForecaster(model, feature_cols, static_attrs, id_col,
                                     cat_dtypes=cat_dtypes, verbose=verbose)
    longest = forecaster.forecast(panel, max(horizons))
    start = longest["forecast_date"].min()

    out = {}
    for h in horizons:
        cutoff = start + pd.Timedelta(weeks=h - 1)
        out[h] = longest[longest["forecast_date"] <= cutoff].reset_index(drop=True)
    return out
