"""Regression tests for the pure/extracted pieces of lgbm_forecast.py fixed
during the forecasting lifecycle audit. Deliberately scoped to small
synthetic frames and the standalone helper functions (``_wape``,
``_cap_non_festival_outliers``, ``_rolling_folds``, ``_recency_weights``) —
``compute()``/``compute_design()`` themselves need a full ERP-shaped
dataframe plus live BigQuery/ERP access (``_fetch_launch_dates``,
``similar_design``) and aren't exercised end-to-end here. Where a fix lives
inline inside ``compute()`` rather than in an extracted helper (the
point-in-time ``design_level`` feature), the test instead verifies the same
pandas expression pattern used there in isolation.

Run: python -m pytest api/test_lgbm_forecast.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import pytest

import lgbm_forecast as lf


# --- _wape -------------------------------------------------------------- #

def test_wape_zero_error_is_zero():
    assert lf._wape([10, 20, 30], [10, 20, 30]) == 0.0


def test_wape_known_value():
    # |10-8| + |20-25| = 2 + 5 = 7; denom = 30 -> 7/30*100
    assert lf._wape([10, 20], [8, 25]) == pytest.approx(7 / 30 * 100)


def test_wape_all_zero_actuals_returns_zero_not_nan():
    assert lf._wape([0, 0], [5, 3]) == 0.0


# --- _rolling_folds ------------------------------------------------------ #

def test_rolling_folds_chronological_and_non_overlapping():
    weeks = pd.date_range("2024-01-01", periods=60, freq="W-MON")
    folds = lf._rolling_folds(weeks, n_folds=3, val_frac=0.15, test_frac=0.15)
    assert len(folds) >= 1
    for train_weeks, valid_weeks in folds:
        assert len(train_weeks) > 0 and len(valid_weeks) > 0
        # train strictly precedes valid (no leakage within a fold)
        assert train_weeks[-1] < valid_weeks[0]
        # chronologically ordered
        assert list(train_weeks) == sorted(train_weeks)
        assert list(valid_weeks) == sorted(valid_weeks)


def test_rolling_folds_never_touch_the_test_tail():
    weeks = pd.date_range("2024-01-01", periods=60, freq="W-MON")
    test_frac = 0.15
    cv_end = int(len(weeks) * (1 - test_frac))
    test_tail = set(weeks[cv_end:])
    folds = lf._rolling_folds(weeks, n_folds=3, val_frac=0.15, test_frac=test_frac)
    for train_weeks, valid_weeks in folds:
        assert not (set(train_weeks) | set(valid_weeks)) & test_tail


def test_rolling_folds_last_fold_is_the_latest():
    weeks = pd.date_range("2024-01-01", periods=80, freq="W-MON")
    folds = lf._rolling_folds(weeks, n_folds=3, val_frac=0.15, test_frac=0.15)
    # each successive fold's validation window should end no earlier than the previous
    ends = [valid[-1] for _train, valid in folds]
    assert ends == sorted(ends)


def test_rolling_folds_short_history_falls_back_to_one_split():
    weeks = pd.date_range("2024-01-01", periods=3, freq="W-MON")
    folds = lf._rolling_folds(weeks, n_folds=3, val_frac=0.15, test_frac=0.15)
    assert len(folds) == 1


# --- _recency_weights ----------------------------------------------------- #

def test_recency_weights_most_recent_row_is_full_weight():
    weeks = pd.Series(pd.date_range("2024-01-01", periods=10, freq="W-MON"))
    w = lf._recency_weights(weeks)
    assert w[-1] == pytest.approx(1.0)


def test_recency_weights_decay_at_halflife():
    ref = pd.Timestamp("2024-12-30")
    old = ref - pd.Timedelta(weeks=lf._RECENCY_HALFLIFE_WEEKS)
    weeks = pd.Series([old, ref])
    w = lf._recency_weights(weeks)
    assert w[1] == pytest.approx(1.0)
    assert w[0] == pytest.approx(0.5, abs=1e-3)


def test_recency_weights_monotonically_decreasing_with_age():
    weeks = pd.Series(pd.date_range("2024-01-01", periods=20, freq="W-MON"))
    w = lf._recency_weights(weeks)
    assert all(a <= b for a, b in zip(w, w[1:]))


# --- _cap_non_festival_outliers: fit-on-train-only (leakage fix) --------- #
#
# These stub out festival.week_signal to "always non-festival" so the test is
# about the fit/apply split, not which real calendar dates happen to fall in
# a festival window (festival.py's windows are real, dated business rules
# that can change year to year — coupling this test to them would make it
# fail for reasons unrelated to what it's testing).

def _stub_no_festival(monkeypatch):
    import festival
    monkeypatch.setattr(festival, "week_signal", lambda d: (1.0, None, 999))


def test_outlier_cap_threshold_ignores_post_cutoff_spike(monkeypatch):
    _stub_no_festival(monkeypatch)
    weeks = pd.date_range("2024-01-01", periods=20, freq="W-MON")
    wk = pd.DataFrame({"sku": ["A"] * 20, "_week": weeks, "qty": [10.0] * 20})

    cutoff = weeks[9]  # rows 0-9 are the "training region"
    # A huge spike AFTER the cutoff must not raise the cap threshold applied
    # to rows before it.
    wk_with_spike = wk.copy()
    wk_with_spike.loc[wk_with_spike["_week"] == weeks[15], "qty"] = 9999.0

    capped = lf._cap_non_festival_outliers(
        wk_with_spike.copy(), "sku", "_week", "qty", fit_cutoff=cutoff,
    )
    # The spike itself is a POST-cutoff row; the 99th-percentile-of-TRAIN cap
    # (10.0, every training row is 10.0) must still clip it.
    spike_row = capped.loc[capped["_week"] == weeks[15], "qty"].iloc[0]
    assert spike_row <= 10.0 + 1e-6
    # And it must not have leaked backward into any pre-cutoff row.
    pre_cutoff = capped.loc[capped["_week"] <= cutoff, "qty"]
    assert (pre_cutoff == 10.0).all()


def test_outlier_cap_without_fit_cutoff_matches_legacy_full_history_fit(monkeypatch):
    _stub_no_festival(monkeypatch)
    weeks = pd.date_range("2024-01-01", periods=20, freq="W-MON")
    values = [10.0] * 19 + [500.0]
    wk = pd.DataFrame({"sku": ["A"] * 20, "_week": weeks, "qty": values})
    capped = lf._cap_non_festival_outliers(wk.copy(), "sku", "_week", "qty", fit_cutoff=None)
    assert capped["qty"].iloc[-1] < 500.0


# --- point-in-time design_level pattern (inline in compute(), tested here
# via the same pandas expression the fix uses) ---------------------------- #

def test_design_level_point_in_time_pattern_ignores_future_spike():
    weeks = pd.date_range("2024-01-01", periods=10, freq="W-MON")
    df = pd.DataFrame({
        "_d": ["X"] * 10,
        "_week": weeks,
        "qty": [5.0] * 10,
    }).sort_values(["_d", "_week"])
    # Same pattern as lgbm_forecast.compute()'s _design_weekly computation.
    df["_design_level_pit"] = (
        df.groupby("_d")["qty"].transform(lambda s: s.shift(1).rolling(12, min_periods=1).median())
    )
    # Row 0 has no prior week at all -> NaN (correctly "unknown", not leaked).
    assert pd.isna(df["_design_level_pit"].iloc[0])
    # Row 1 reflects only row 0's value (5.0), matching the steady level.
    assert df["_design_level_pit"].iloc[1] == pytest.approx(5.0)

    # Now inject a huge spike at week index 8 (near the end) and confirm it
    # has NO effect on any row before it (the leakage this fix removes).
    df2 = df.copy()
    df2.loc[df2["_week"] == weeks[8], "qty"] = 9999.0
    df2["_design_level_pit"] = (
        df2.groupby("_d")["qty"].transform(lambda s: s.shift(1).rolling(12, min_periods=1).median())
    )
    for i in range(8):
        assert df2["_design_level_pit"].iloc[i] == pytest.approx(df["_design_level_pit"].iloc[i]) \
            or (pd.isna(df2["_design_level_pit"].iloc[i]) and pd.isna(df["_design_level_pit"].iloc[i]))


# --- launch_drr causal feature: computation + point-in-time gate --------- #
# (added when launch_drr was wired into the model as the one lifecycle
# signal its own docstring flags as safe/causal for training)

def test_launch_drr_computation_matches_units_per_day():
    # Same aggregation compute()'s launch_drr block uses: sum of target over
    # days-after-launch in [0, 20), divided by weeks-covered*7.
    launch = pd.Timestamp("2024-01-01")
    weeks = pd.date_range(launch, periods=5, freq="W-MON")  # offsets 0,7,14,21,28 days
    wk = pd.DataFrame({"id": ["A"] * 5, "_week": weeks, "target": [10.0] * 5})
    static_launch = pd.DataFrame({"id": ["A"], "_launch": [launch]})

    lwo = wk.merge(static_launch, on="id", how="left")
    lwo["_dal"] = (lwo["_week"] - lwo["_launch"]).dt.days
    lwo = lwo[(lwo["_dal"] >= 0) & (lwo["_dal"] < lf._LAUNCH_WINDOW_DAYS)]
    # 0/7/14-day-offset weeks fall inside the 20-day window; 21/28 don't.
    assert len(lwo) == 3
    launch_units = lwo.groupby("id")["target"].sum()
    launch_days = lwo.groupby("id")["_week"].count() * 7
    launch_drr = (launch_units / launch_days.clip(lower=1)).fillna(0.0)
    assert launch_drr["A"] == pytest.approx(30.0 / 21.0)


def test_launch_drr_point_in_time_gate_zeroes_rows_inside_own_launch_window():
    # Same gate compute() applies post-merge: rows dated before their own
    # SKU's 20-day launch window has elapsed must not see the "final" value.
    launch = pd.Timestamp("2024-01-01")
    panel = pd.DataFrame({
        "_week": pd.date_range(launch, periods=6, freq="W-MON"),  # 0,7,14,21,28,35 days
        "launch_drr": [5.0] * 6,  # the "final" per-SKU value, pre-gate
    })
    age = (panel["_week"] - launch).dt.days.astype("float64")
    gated = np.where(age >= lf._LAUNCH_WINDOW_DAYS, panel["launch_drr"], 0.0)
    # 0-day and 7-day rows are still inside the 20-day launch window -> 0.
    assert list(gated[:2]) == [0.0, 0.0]
    # 21+ day rows have the window fully behind them -> real value kept.
    assert list(gated[3:]) == [5.0, 5.0, 5.0]


# --- lag-feature shift alignment (no same-row leakage) -------------------- #

def test_lag_features_never_see_their_own_row():
    """Same groupby().shift(L) pattern compute() uses for lag_L features:
    row t's lag_1 must equal row (t-1)'s target, never row t's own value."""
    panel = pd.DataFrame({
        "sku": ["A"] * 6,
        "_week": pd.date_range("2024-01-01", periods=6, freq="W-MON"),
        "target": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })
    g = panel.groupby("sku")["target"]
    panel["lag_1"] = g.shift(1)
    assert pd.isna(panel["lag_1"].iloc[0])
    assert list(panel["lag_1"].iloc[1:]) == list(panel["target"].iloc[:-1])
    # No row's lag_1 ever equals its own target (would indicate a shift bug).
    non_null = panel.dropna(subset=["lag_1"])
    assert not (non_null["lag_1"] == non_null["target"]).any()
