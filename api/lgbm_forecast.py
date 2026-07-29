"""
WEEKLY XGBoost + LightGBM demand forecasting for the SKU Production Plan API.

Feature engineering and model search follow the user-provided demand-forecasting
script (lags, rolling stats, calendar, promo, and category_name/brand_name/
channel_name categoricals), extended with three things that script doesn't have:

  1. A RECURSIVE FUTURE FORECAST. The script only trains and backtests
     (reports WAPE/R²/MAE/RMSE on a held-out test split) — it never predicts
     forward. ``compute()``/``compute_design()`` below refit the winning
     model(s) on all history and step forward HORIZON_WEEKS, exactly as the
     previous deployed model did (see the H-matrix loop in each function).
  2. A festival/sale-season signal (``festival.week_signal``) as a model
     feature, plus a post-hoc uplift applied in data.py.
  3. Cold-start demand borrowing for young designs from material-similar
     designs (``similar_design.py``), since a brand-new design has no lag
     history of its own to forecast from.

Three deliberate deviations from the pasted script, for correctness against
THIS dataset (verified elsewhere in this codebase):
  - Target definition: only "Cancelled" and "Cancelled Before Shipping" are
    excluded — everything else (including "Return Received", "Cancel Init",
    "Return Init", etc.) counts as realized demand, matching data.py's
    ``_EXCLUDED_STATUSES`` (``_EXCLUDED_NORM`` here). Note this DOES count
    later-returned orders as demand (this app's own report flagged a 30.7%
    return rate) — a deliberate choice, not an oversight.
  - "design_no": the script derives it as the FIRST "-"-split segment of
    product_sku_code. In this catalog SKUs are "<design>-<variant>-<size>"
    (e.g. "474-01-M"), so DESIGN_NO is actually the first TWO segments
    ("474-01") — using the real ``DESIGN_NO`` column instead of a naive
    single-segment split, so design-grain output lines up with the rest of
    the app (PlanRow.designNo, lifecycle, cold-start).
  - "product_age_weeks": the script derives this from each SKU's first
    OBSERVED sale in the loaded window, which understates age for anything
    that launched before the window started. Using the real LAUNCH_DATE
    (fetched directly from the ERP design-master view, like data.py and
    similar_design.py already do) with the script's own first-sale-week
    approach only as a fallback when no launch date is known.
  - eval_metric for the XGBoost grid search is "mae", not the script's
    f"tweedie-nloglik@{{vp}}": that exact metric previously emitted
    "-nan(ind)" mid-search on this dataset, which XGBoost's early-stopping
    callback can't parse (already diagnosed once in this codebase's history —
    reg:tweedie stays the training objective, only the validation metric
    changes).

``compute(df)`` returns ``{sku: {"weekly": [w1..w6]}}`` — predicted gross
sold units per ISO week (Mon-anchored), cached to disk by snapshot date.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

HORIZON_WEEKS = 6                          # weeks forecast ahead (covers the 35-day = 5-week plan)
_WEEK_LAGS = [1, 2, 4, 8, 12, 26, 52]       # lag features, in WEEKS (matches the pasted script)
_WEEK_ROLL = [4, 8, 12]                     # rolling-mean windows, in WEEKS
_WEEK_STD = [4, 8]                          # rolling-std windows, in WEEKS
_ACTIVE_WEEKS = 13                # a SKU silent this many weeks gets no forecast
_USE_FESTIVAL_FEATURES = True

# --- source column names --------------------------------------------------- #
COL_QTY = "qty"
COL_STATUS = "order_status"
COL_ORDER_DATE = "order_date"
COL_SKU = "product_sku_code"
COL_DESIGN = "DESIGN_NO"
COL_LAUNCH_DATE = "LAUNCH_DATE"
TARGET = "sales_qty"
# Static per-SKU categorical attributes (mode over the SKU's own history).
_ATTR_COLS = ["category_name", "brand_name", "channel_name"]

# Excluded statuses (normalised). See module docstring: everything NOT in
# this set counts as realized demand, matching data.py's _EXCLUDED_STATUSES.
_EXCLUDED_NORM = {"cancelled", "cancelled before shipping"}

# XGBoost params for the (legacy) single fixed-hyperparameter fallback path —
# kept only as a safety net if a validation split can't be formed at all (see
# `has_valid` guard in compute()). The real production path below searches
# _XGB_GRID/_LGBM_GRID instead.
_XGB_PARAMS = {
    "objective": "reg:tweedie",
    "tweedie_variance_power": 1.3,
    "n_estimators": 3000,
    "learning_rate": 0.02,
    "max_depth": 8,
    "min_child_weight": 15,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "tree_method": "hist",
    "enable_categorical": True,
    "n_jobs": -1,
    "random_state": 42,
    "verbosity": 0,
}

# --- Model search: grid-search XGBoost + LightGBM (both Tweedie objective,
# for intermittent non-negative demand), select by validation WAPE, ensemble
# 50/50 if that blend wins. Grids match the pasted script exactly. ---
_VAL_FRAC = 0.15
_TEST_FRAC = 0.15
_GRID_N_ESTIMATORS = 3000
_GRID_LEARNING_RATE = 0.02
_GRID_EARLY_STOP = 50
_XGB_GRID = [
    {"max_depth": 4, "min_child_weight": 20, "reg_alpha": 1.0, "reg_lambda": 5.0,
     "subsample": 0.7, "colsample_bytree": 0.7, "colsample_bynode": 0.7, "tweedie_variance_power": 1.3},
    {"max_depth": 3, "min_child_weight": 20, "reg_alpha": 1.0, "reg_lambda": 5.0,
     "subsample": 0.7, "colsample_bytree": 0.7, "colsample_bynode": 0.7, "tweedie_variance_power": 1.3},
    {"max_depth": 4, "min_child_weight": 30, "reg_alpha": 2.0, "reg_lambda": 8.0,
     "subsample": 0.6, "colsample_bytree": 0.6, "colsample_bynode": 0.6, "tweedie_variance_power": 1.1},
    {"max_depth": 5, "min_child_weight": 30, "reg_alpha": 2.0, "reg_lambda": 8.0,
     "subsample": 0.6, "colsample_bytree": 0.6, "colsample_bynode": 0.6, "tweedie_variance_power": 1.5},
    {"max_depth": 4, "min_child_weight": 15, "reg_alpha": 0.5, "reg_lambda": 3.0,
     "subsample": 0.8, "colsample_bytree": 0.8, "colsample_bynode": 0.8, "tweedie_variance_power": 1.3},
]
_LGBM_GRID = [
    {"max_depth": 4, "num_leaves": 31, "min_child_samples": 30, "reg_alpha": 1.0, "reg_lambda": 5.0,
     "subsample": 0.7, "colsample_bytree": 0.7, "tweedie_variance_power": 1.3},
    {"max_depth": 5, "num_leaves": 63, "min_child_samples": 30, "reg_alpha": 2.0, "reg_lambda": 8.0,
     "subsample": 0.6, "colsample_bytree": 0.6, "tweedie_variance_power": 1.1},
    {"max_depth": -1, "num_leaves": 63, "min_child_samples": 20, "reg_alpha": 0.5, "reg_lambda": 3.0,
     "subsample": 0.8, "colsample_bytree": 0.8, "tweedie_variance_power": 1.3},
]

_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
# v17: full feature-set replacement — lags [1,2,4,8,12,26,52] (was +3), promo
# features, category_name/brand_name/channel_name categoricals (was DESIGN_GROUP/
# COLOR/SECTION/CATALOG_NAME + price + vertical), product_age_weeks from real
# LAUNCH_DATE with first-sale-week fallback. Cross-SKU design_level/
# similar_design_level cold-start features and the festival feature are unchanged.
_CACHE_VERSION = "v17"


def _cache_file(snapshot: str) -> Path:
    return _CACHE_DIR / f"lgbm_forecasts_{_CACHE_VERSION}_{snapshot}.json"


def load_cache(snapshot: str) -> dict | None:
    """Return cached forecasts for the snapshot date, or None."""
    path = _cache_file(snapshot)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())   # {sku: {"weekly": [...]}}
    except Exception:  # noqa: BLE001
        return None


def save_cache(snapshot: str, forecasts: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_file(snapshot).write_text(json.dumps(forecasts))


# --- Design-level (style) cache — a SEPARATE model + cache from the SKU-level
# one above (see compute_design() below). Independent filename prefix so it
# can never collide with data.py's `lgbm_forecasts_v*_*.json` backtest glob. ---
_DESIGN_CACHE_VERSION = "d2"


def _design_cache_file(snapshot: str) -> Path:
    return _CACHE_DIR / f"lgbm_design_forecasts_{_DESIGN_CACHE_VERSION}_{snapshot}.json"


def load_design_cache(snapshot: str) -> dict | None:
    """Return cached design-level forecasts for the snapshot date, or None."""
    path = _design_cache_file(snapshot)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())   # {design_no: {"weekly": [...]}}
    except Exception:  # noqa: BLE001
        return None


def save_design_cache(snapshot: str, forecasts: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _design_cache_file(snapshot).write_text(json.dumps(forecasts))


def _wape(y_true, y_pred) -> float:
    """Volume-weighted absolute percentage error, as a 0-100 number."""
    import numpy as np
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    denom = np.abs(y_true).sum()
    return float(np.abs(y_true - y_pred).sum() / denom * 100) if denom > 0 else 0.0


def _fetch_launch_dates():
    """Design -> real LAUNCH_DATE, fetched directly from the ERP design-master
    view (one row per DESIGN_NO, no join/collision) — same source data.py and
    similar_design.py already use, and more reliable than the sales-row
    LAUNCH_DATE column (populated by a regex-keyed merge that can collide)."""
    import pandas as pd

    try:
        import live_source
        master = live_source.fetch_erp_view(live_source.VIEW_MASTER)
        if master.empty or "DESIGN_NO" not in master.columns:
            return {}
        dt = pd.to_datetime(master.get("LAUNCH_DATE"), errors="coerce")
        out: dict = {}
        for dn, d in zip(master["DESIGN_NO"].astype(str), dt):
            if pd.isna(d) or d < pd.Timestamp("2015-01-01"):
                continue  # implausible placeholder (e.g. Excel epoch)
            if dn not in out or d < out[dn]:
                out[dn] = d
        return out
    except Exception as exc:  # noqa: BLE001 — never let this block training
        print(f"[lgbm_forecast] launch-date fetch failed: {exc!r}", file=sys.stderr)
        return {}


def compute(df_full) -> dict:
    """Train weekly XGBoost/LightGBM models and forecast the next
    ``HORIZON_WEEKS`` weeks.

    ``df_full`` is the merged order-history frame. Returns
    ``{sku: {"weekly": [w1..w6]}}`` of predicted gross sold units per week."""
    import numpy as np
    import pandas as pd
    import xgboost as xgb
    import lightgbm as lgb

    id_col = COL_SKU
    clean = df_full.dropna(subset=[id_col]).copy()
    clean[id_col] = clean[id_col].astype(str).str.strip().str.upper()
    clean[COL_ORDER_DATE] = pd.to_datetime(clean[COL_ORDER_DATE], errors="coerce")
    clean = clean.dropna(subset=[COL_ORDER_DATE])
    clean = clean[clean[COL_ORDER_DATE] <= pd.Timestamp.today().normalize()]
    clean = clean.drop_duplicates()
    for c in _ATTR_COLS:
        if c in clean.columns:
            clean[c] = clean[c].astype(str).str.strip().str.upper()
    status_norm = clean[COL_STATUS].astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    qty = pd.to_numeric(clean[COL_QTY], errors="coerce").fillna(0)
    clean[TARGET] = np.where(~status_norm.isin(_EXCLUDED_NORM), qty, 0.0).astype("float64")
    clean["promo_discount"] = pd.to_numeric(clean.get("promo_discount"), errors="coerce").fillna(0.0) \
        if "promo_discount" in clean.columns else 0.0

    # --- weekly panel (Mon-anchored), dense per SKU from first week to last ----- #
    _od = clean[COL_ORDER_DATE]
    clean["_week"] = _od - pd.to_timedelta(_od.dt.weekday, unit="D")
    wk = (
        clean.groupby([id_col, "_week"], as_index=False)
        .agg(**{TARGET: (TARGET, "sum"), "promo_discount": ("promo_discount", "sum")})
    )
    if wk.empty:
        return {}
    # Drop the trailing partial week — see module docstring's dataset-specific
    # gotchas: a week pulled mid-collection is under-counted vs a real full week.
    _true_last_date = clean[COL_ORDER_DATE].max()
    _last_bucketed_wk = wk["_week"].max()
    if pd.notna(_true_last_date) and _true_last_date < _last_bucketed_wk + pd.Timedelta(days=6):
        wk = wk[wk["_week"] < _last_bucketed_wk]
        if wk.empty:
            return {}

    # --- static per-SKU attributes: modal category/brand/channel, real design_no,
    # first-observed week, and (below) the real launch date. --------------------- #
    def _mode(s):
        m = s.dropna()
        return m.mode().iloc[0] if not m.mode().empty else "UNKNOWN"

    agg = {c: _mode for c in _ATTR_COLS if c in clean.columns}
    static = clean.loc[clean[id_col].notna()].groupby(id_col).agg(agg).reset_index()
    for c in _ATTR_COLS:
        if c not in static.columns:
            static[c] = "UNKNOWN"
        static[c] = static[c].fillna("UNKNOWN")

    design_of = clean.groupby(id_col)[COL_DESIGN].first().astype(str)
    static["design_no"] = static[id_col].map(design_of).fillna("UNKNOWN")
    # SKU convention here is "<design_no>-<size>" (design_no itself may contain
    # a "-"), so size is whatever's after the design_no prefix.
    def _size_of(row):
        sku, dn = str(row[id_col]), str(row["design_no"])
        return sku[len(dn):].lstrip("-") if sku.upper().startswith(dn.upper()) else "UNKNOWN"
    static["size"] = static.apply(_size_of, axis=1).replace("", "UNKNOWN")

    first_week = wk.groupby(id_col)["_week"].min()
    launch_map = _fetch_launch_dates()
    static["_launch"] = static["design_no"].map(launch_map)
    static["_first_week"] = static[id_col].map(first_week)
    # Fall back to first-observed-week (as a launch proxy) only when the real
    # launch date is unknown.
    static["_launch"] = static["_launch"].fillna(static["_first_week"])

    gmax = wk["_week"].max()
    starts = wk.groupby(id_col)["_week"].min().reset_index(name="_start")
    starts["_w"] = starts["_start"].apply(lambda s: pd.date_range(s, gmax, freq="W-MON"))
    grid = starts.explode("_w")[[id_col, "_w"]].rename(columns={"_w": "_week"})
    panel = grid.merge(wk, on=[id_col, "_week"], how="left").sort_values([id_col, "_week"])
    panel[TARGET] = panel[TARGET].fillna(0.0)
    panel["promo_discount"] = panel["promo_discount"].fillna(0.0)
    panel = panel[panel["_week"].notna()].reset_index(drop=True)

    # --- cross-SKU cold-start signal: design-level recent demand + a
    # similarity-borrowed level for designs with little/no history of their own
    # (see similar_design.py). --------------------------------------------------- #
    _cut = gmax - pd.Timedelta(weeks=12)
    _rec = wk[wk["_week"] > _cut].copy()
    _rec["_d"] = _rec[id_col].map(design_of)
    _design_weeks = _rec.dropna(subset=["_d"]).groupby(["_d", "_week"])[TARGET].sum()
    _dlevel = _design_weeks.groupby(level=0).median()
    static["design_level"] = static[id_col].map(design_of.map(_dlevel)).fillna(0.0).astype("float64")
    try:
        import similar_design
        sim_level = similar_design.borrow_design_level(_dlevel)
        static["similar_design_level"] = static[id_col].map(design_of.map(sim_level)).fillna(0.0).astype("float64")
    except Exception as exc:  # noqa: BLE001 — never let this block training
        print(f"[lgbm_forecast] similar_design borrowing failed: {exc!r}", file=sys.stderr)
        static["similar_design_level"] = 0.0

    # --- features (all from PAST weeks; no leakage) ---------------------------- #
    g = panel.groupby(id_col)[TARGET]
    for L in _WEEK_LAGS:
        panel[f"lag_{L}"] = g.shift(L)
    sh = panel.groupby(id_col)[TARGET].shift(1)
    gg = sh.groupby(panel[id_col])
    for w in _WEEK_ROLL:
        panel[f"rolling_mean_{w}"] = gg.rolling(w, min_periods=1).mean().reset_index(level=0, drop=True)
    for w in _WEEK_STD:
        panel[f"rolling_std_{w}"] = gg.rolling(w, min_periods=2).std().reset_index(level=0, drop=True)
    panel["rolling_max_4"] = gg.rolling(4, min_periods=1).max().reset_index(level=0, drop=True)
    panel["rolling_min_4"] = gg.rolling(4, min_periods=1).min().reset_index(level=0, drop=True)
    panel["sales_diff_1"] = panel["lag_1"] - panel["lag_2"]
    panel["sales_growth_pct"] = np.where(
        panel["lag_2"].fillna(0) == 0, 0.0,
        (panel["lag_1"] - panel["lag_2"]) / panel["lag_2"])
    panel["rolling_mean_ratio"] = np.where(
        panel["rolling_mean_4"].fillna(0) == 0, 0.0,
        panel["lag_1"] / panel["rolling_mean_4"])
    panel["trend_vs_avg"] = panel["lag_1"] - panel["rolling_mean_4"]
    panel["coefficient_variation"] = np.where(
        panel["rolling_mean_4"].fillna(0) == 0, 0.0,
        panel["rolling_std_4"] / panel["rolling_mean_4"])
    panel["avg_weekly_sales"] = g.transform(lambda s: s.shift(1).expanding().mean())
    _is_zero = (panel[TARGET] == 0).astype("float64")
    panel["zero_sales_ratio"] = (
        _is_zero.groupby(panel[id_col]).transform(lambda s: s.shift(1).expanding().mean())
    )
    panel["promotion_flag"] = (panel["promo_discount"] < 0).astype("int8")

    panel["weekofyear"] = panel["_week"].dt.isocalendar().week.fillna(0).astype("int16")
    panel["month"] = panel["_week"].dt.month.astype("int16")
    panel["quarter"] = panel["_week"].dt.quarter.astype("int16")
    panel["year"] = panel["_week"].dt.year.astype("int32")
    panel["day_of_year"] = panel["_week"].dt.dayofyear.astype("int16")
    panel["is_month_start"] = panel["_week"].dt.is_month_start.astype("int8")
    panel["is_month_end"] = panel["_week"].dt.is_month_end.astype("int8")
    panel["is_quarter_start"] = panel["_week"].dt.is_quarter_start.astype("int8")
    panel["is_quarter_end"] = panel["_week"].dt.is_quarter_end.astype("int8")

    if _USE_FESTIVAL_FEATURES:
        import festival
        uniq_weeks = panel["_week"].dropna().unique()
        wk_sig = {w: festival.week_signal(pd.Timestamp(w).date()) for w in uniq_weeks}
        panel["festival_mult"] = panel["_week"].map(lambda w: wk_sig[w][0]).astype("float64")
        panel["festival_event"] = panel["_week"].map(lambda w: wk_sig[w][1] or "None")
        panel["days_to_festival_peak"] = panel["_week"].map(lambda w: wk_sig[w][2]).astype("int32")
        panel["is_festival"] = (panel["festival_mult"] > 1.0).astype("int8")

    panel = panel.merge(static, on=id_col, how="left")
    age = (panel["_week"] - panel["_launch"]).dt.days.astype("float64")
    panel["product_age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32")
    panel = panel.drop(columns=["_launch", "_first_week"])

    lag_cols = [f"lag_{L}" for L in _WEEK_LAGS]
    roll_cols = [f"rolling_mean_{w}" for w in _WEEK_ROLL] + [f"rolling_std_{w}" for w in _WEEK_STD] \
        + ["rolling_max_4", "rolling_min_4"]
    trend_cols = ["sales_diff_1", "sales_growth_pct", "rolling_mean_ratio", "trend_vs_avg",
                  "coefficient_variation", "avg_weekly_sales", "zero_sales_ratio"]
    calendar_cols = ["year", "month", "quarter", "weekofyear", "day_of_year",
                     "is_month_start", "is_month_end", "is_quarter_start", "is_quarter_end"]
    prod_cat = [c for c in _ATTR_COLS if c in panel.columns]
    festival_cols = ["festival_mult", "is_festival", "festival_event", "days_to_festival_peak"] if _USE_FESTIVAL_FEATURES else []
    feature_cols = (lag_cols + roll_cols + trend_cols + calendar_cols
                    + prod_cat + ["design_no", "size", "product_age_weeks",
                                  "design_level", "similar_design_level",
                                  "promo_discount", "promotion_flag"]
                    + festival_cols)
    cat_cols = prod_cat + ["design_no", "size"] + (["festival_event"] if _USE_FESTIVAL_FEATURES else [])
    for c in cat_cols:
        panel[c] = panel[c].astype("category")
    cat_dtypes = {c: panel[c].dtype for c in cat_cols}

    # --- train: time-based split, grid-search XGBoost + LightGBM (Tweedie),
    # select by validation WAPE only, then refit the winner on ALL history for
    # the actual deployed forecast (test stays untouched for anything but a
    # final, purely-diagnostic metric). ---------------------------------------- #
    _t0 = time.time()
    weeks_sorted = np.sort(panel["_week"].unique())
    n_weeks = len(weeks_sorted)
    train_end = max(1, int(n_weeks * (1 - _VAL_FRAC - _TEST_FRAC)))
    valid_end = max(train_end, int(n_weeks * (1 - _TEST_FRAC)))
    train_weeks = weeks_sorted[:train_end]
    valid_weeks = weeks_sorted[train_end:valid_end]
    test_weeks = weeks_sorted[valid_end:]

    def _rows(weeks):
        m = panel["_week"].isin(weeks)
        return panel.loc[m, feature_cols], panel.loc[m, TARGET]

    X_train, y_train = _rows(train_weeks)
    X_valid, y_valid = _rows(valid_weeks)
    X_test, y_test = _rows(test_weeks)
    has_valid, has_test = len(X_valid) > 0, len(X_test) > 0

    if not has_valid:
        print(f"[lgbm_forecast] only {n_weeks} distinct weeks — skipping grid search, "
              f"using a single fixed-param XGBoost fit", file=sys.stderr)
        final_xgb = xgb.XGBRegressor(**_XGB_PARAMS)
        final_xgb.fit(panel[feature_cols], panel[TARGET])
        final_lgbm = None

        def _predict(X):
            return final_xgb.predict(X)
    else:
        xgb_search: list[dict] = []
        for params in _XGB_GRID:
            # eval_metric intentionally "mae" — see module docstring.
            kwargs = dict(
                objective="reg:tweedie", n_estimators=_GRID_N_ESTIMATORS,
                learning_rate=_GRID_LEARNING_RATE, random_state=42, tree_method="hist",
                enable_categorical=True, n_jobs=-1, verbosity=0,
                early_stopping_rounds=_GRID_EARLY_STOP, eval_metric="mae",
                **params,
            )
            m = xgb.XGBRegressor(**kwargs)
            m.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False)
            xgb_search.append({
                "params": params, "model": m,
                "best_iteration": getattr(m, "best_iteration", None),
                "valid_wape": _wape(y_valid, m.predict(X_valid)),
            })
        best_xgb = min(xgb_search, key=lambda r: r["valid_wape"])

        lgbm_search: list[dict] = []
        for params in _LGBM_GRID:
            m = lgb.LGBMRegressor(
                objective="tweedie", n_estimators=_GRID_N_ESTIMATORS,
                learning_rate=_GRID_LEARNING_RATE, subsample_freq=1,
                random_state=42, verbose=-1, n_jobs=-1, **params,
            )
            m.fit(
                X_train, y_train, eval_set=[(X_valid, y_valid)], eval_metric="mae",
                callbacks=[lgb.early_stopping(_GRID_EARLY_STOP, verbose=False)],
            )
            lgbm_search.append({
                "params": params, "model": m,
                "best_iteration": getattr(m, "best_iteration_", None),
                "valid_wape": _wape(y_valid, m.predict(X_valid)),
            })
        best_lgbm = min(lgbm_search, key=lambda r: r["valid_wape"])

        candidates = {
            "xgboost": {
                "train": best_xgb["model"].predict(X_train),
                "valid": best_xgb["model"].predict(X_valid),
                "test": best_xgb["model"].predict(X_test) if has_test else np.array([]),
            },
            "lightgbm": {
                "train": best_lgbm["model"].predict(X_train),
                "valid": best_lgbm["model"].predict(X_valid),
                "test": best_lgbm["model"].predict(X_test) if has_test else np.array([]),
            },
        }
        candidates["blend"] = {
            s: (candidates["xgboost"][s] + candidates["lightgbm"][s]) / 2 for s in ("train", "valid", "test")
        }
        comparison = sorted((
            {
                "candidate": name,
                "valid_wape": _wape(y_valid, p["valid"]),
                "test_wape": _wape(y_test, p["test"]) if has_test else float("nan"),
            }
            for name, p in candidates.items()
        ), key=lambda r: r["valid_wape"])
        best_name = comparison[0]["candidate"]
        print(f"[lgbm_forecast] grid search done in {time.time() - _t0:.0f}s | "
              f"comparison={comparison} | selected='{best_name}'", file=sys.stderr)

        try:
            from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
            _bp = candidates[best_name]
            _diag = {
                split: {
                    "r2": round(float(r2_score(y_true, pred)), 3),
                    "mae": round(float(mean_absolute_error(y_true, pred)), 3),
                    "rmse": round(float(np.sqrt(mean_squared_error(y_true, pred))), 3),
                }
                for split, y_true, pred, ok in (
                    ("train", y_train, _bp["train"], True),
                    ("valid", y_valid, _bp["valid"], has_valid),
                    ("test", y_test, _bp["test"], has_test),
                ) if ok
            }
            print(f"[lgbm_forecast] diagnostics ({best_name}): {_diag}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — diagnostics must never block training
            print(f"[lgbm_forecast] diagnostics logging failed: {exc!r}", file=sys.stderr)

        def _refit_xgb(cfg: dict):
            n_est = cfg["best_iteration"] or _GRID_N_ESTIMATORS
            rm = xgb.XGBRegressor(
                objective="reg:tweedie", n_estimators=max(int(n_est), 1),
                learning_rate=_GRID_LEARNING_RATE, random_state=42, tree_method="hist",
                enable_categorical=True, n_jobs=-1, verbosity=0, **cfg["params"],
            )
            rm.fit(panel[feature_cols], panel[TARGET])
            return rm

        def _refit_lgbm(cfg: dict):
            n_est = cfg["best_iteration"] or _GRID_N_ESTIMATORS
            rm = lgb.LGBMRegressor(
                objective="tweedie", n_estimators=max(int(n_est), 1),
                learning_rate=_GRID_LEARNING_RATE, subsample_freq=1,
                random_state=42, verbose=-1, n_jobs=-1, **cfg["params"],
            )
            rm.fit(panel[feature_cols], panel[TARGET])
            return rm

        final_xgb = _refit_xgb(best_xgb) if best_name in ("xgboost", "blend") else None
        final_lgbm = _refit_lgbm(best_lgbm) if best_name in ("lightgbm", "blend") else None

        def _predict(X):
            if best_name == "xgboost":
                return final_xgb.predict(X)
            if best_name == "lightgbm":
                return final_lgbm.predict(X)
            return (final_xgb.predict(X) + final_lgbm.predict(X)) / 2

        if final_xgb is not None:
            _imp = pd.Series(final_xgb.feature_importances_, index=feature_cols).sort_values(ascending=False)
            print(f"[lgbm_forecast] top-10 features (xgboost): {_imp.head(10).round(3).to_dict()}", file=sys.stderr)
        print(f"[lgbm_forecast] training total {time.time() - _t0:.0f}s", file=sys.stderr)

    # --- recursive weekly forecast (vectorised history matrix) ----------------- #
    ids = np.sort(panel[id_col].unique())
    W = max(_WEEK_LAGS)
    last_week = panel["_week"].max()
    weeks_idx = pd.date_range(last_week - pd.Timedelta(weeks=W - 1), last_week, freq="W-MON")
    H = (panel[panel["_week"] >= weeks_idx[0]]
         .pivot_table(index=id_col, columns="_week", values=TARGET, aggfunc="sum")
         .reindex(index=ids, columns=weeks_idx).to_numpy(dtype="float64"))
    stat = static.set_index(id_col).reindex(ids)
    n = len(ids)
    out: dict[str, list[float]] = {str(s): [] for s in ids}

    _hist_by_sku = panel.groupby(id_col)[TARGET]
    run_sum = _hist_by_sku.sum().reindex(ids).fillna(0.0).to_numpy(dtype="float64")
    run_count = _hist_by_sku.count().reindex(ids).fillna(0).to_numpy(dtype="float64")
    run_zeros = (
        (panel[TARGET] == 0).groupby(panel[id_col]).sum()
        .reindex(ids).fillna(0.0).to_numpy(dtype="float64")
    )

    for step in range(1, HORIZON_WEEKS + 1):
        wkdate = last_week + pd.Timedelta(weeks=step)
        data: dict = {}
        for L in _WEEK_LAGS:
            data[f"lag_{L}"] = H[:, -L]
        for w in _WEEK_ROLL:
            sl = H[:, -w:]
            cnt = np.sum(~np.isnan(sl), axis=1)
            with np.errstate(invalid="ignore"):
                data[f"rolling_mean_{w}"] = np.nansum(sl, axis=1) / np.where(cnt > 0, cnt, np.nan)
        for w in _WEEK_STD:
            slw = H[:, -w:]
            cntw = np.sum(~np.isnan(slw), axis=1)
            mw = np.where(cntw > 0, np.nansum(slw, axis=1) / np.where(cntw > 0, cntw, np.nan), np.nan)
            with np.errstate(invalid="ignore"):
                data[f"rolling_std_{w}"] = np.sqrt(np.nansum((slw - mw[:, None]) ** 2, axis=1)
                                                    / np.where(cntw >= 2, cntw - 1, np.nan))
        sl4 = H[:, -4:]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            data["rolling_max_4"] = np.nanmax(sl4, axis=1)
            data["rolling_min_4"] = np.nanmin(sl4, axis=1)
        data["sales_diff_1"] = data["lag_1"] - data["lag_2"]
        with np.errstate(invalid="ignore", divide="ignore"):
            data["sales_growth_pct"] = np.where(
                np.nan_to_num(data["lag_2"]) == 0, 0.0,
                (data["lag_1"] - data["lag_2"]) / data["lag_2"])
            data["rolling_mean_ratio"] = np.where(
                np.nan_to_num(data["rolling_mean_4"]) == 0, 0.0,
                data["lag_1"] / data["rolling_mean_4"])
            data["coefficient_variation"] = np.where(
                np.nan_to_num(data["rolling_mean_4"]) == 0, 0.0,
                data["rolling_std_4"] / data["rolling_mean_4"])
        data["trend_vs_avg"] = data["lag_1"] - data["rolling_mean_4"]
        with np.errstate(invalid="ignore", divide="ignore"):
            data["avg_weekly_sales"] = np.where(run_count > 0, run_sum / run_count, 0.0)
            data["zero_sales_ratio"] = np.where(run_count > 0, run_zeros / run_count, 0.0)
        data["year"] = np.full(n, wkdate.year, dtype="int32")
        data["month"] = np.full(n, wkdate.month, dtype="int16")
        data["quarter"] = np.full(n, wkdate.quarter, dtype="int16")
        data["weekofyear"] = np.full(n, int(wkdate.isocalendar()[1]), dtype="int16")
        data["day_of_year"] = np.full(n, wkdate.dayofyear, dtype="int16")
        data["is_month_start"] = np.full(n, int(wkdate.is_month_start), dtype="int8")
        data["is_month_end"] = np.full(n, int(wkdate.is_month_end), dtype="int8")
        data["is_quarter_start"] = np.full(n, int(wkdate.is_quarter_start), dtype="int8")
        data["is_quarter_end"] = np.full(n, int(wkdate.is_quarter_end), dtype="int8")
        # Future promo calendar is unknown — assume no promotion, the
        # conservative default (matches how the model treats most weeks).
        data["promo_discount"] = np.zeros(n, dtype="float64")
        data["promotion_flag"] = np.zeros(n, dtype="int8")
        if _USE_FESTIVAL_FEATURES:
            fmult, fevent, fdays = festival.week_signal(wkdate.date())
            data["festival_mult"] = np.full(n, fmult, dtype="float64")
            data["festival_event"] = np.full(n, fevent or "None", dtype=object)
            data["days_to_festival_peak"] = np.full(n, fdays, dtype="int32")
            data["is_festival"] = np.full(n, 1 if fmult > 1.0 else 0, dtype="int8")
        for c in prod_cat:
            if c in stat.columns:
                data[c] = stat[c].to_numpy()
        data["design_no"] = stat["design_no"].to_numpy()
        data["size"] = stat["size"].to_numpy()
        data["design_level"] = stat["design_level"].to_numpy()
        data["similar_design_level"] = stat["similar_design_level"].to_numpy()
        age = (wkdate - stat["_launch"]).dt.days.astype("float64")
        data["product_age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32").to_numpy()
        X = pd.DataFrame(data)
        for c, dt in cat_dtypes.items():
            if c in X.columns:
                X[c] = X[c].astype(dt)
        preds = np.clip(_predict(X[feature_cols]), 0, None)
        H = np.concatenate([H[:, 1:], preds[:, None]], axis=1)
        run_sum = run_sum + preds
        run_count = run_count + 1
        run_zeros = run_zeros + (preds == 0).astype("float64")
        for i, s in enumerate(ids):
            out[str(s)].append(round(float(preds[i]), 3))

    # --- cold-start blend: for young/unproven designs, blend the model's own
    # forecast with a demand-curve SHAPE borrowed from material-similar
    # designs at the same weeks-since-launch offset (similar_design_curve). ---- #
    try:
        import similar_design as _simdes
        _design_launch_map: dict = dict(zip(static["design_no"], static["_launch"]))

        _wkd = wk.copy()
        _wkd["_design"] = _wkd[id_col].map(design_of)
        _wkd = _wkd.dropna(subset=["_design"])
        _wkd["_launch"] = _wkd["_design"].map(_design_launch_map)
        _wkd = _wkd.dropna(subset=["_launch"])
        _wkd["_offset"] = ((_wkd["_week"] - _wkd["_launch"]).dt.days // 7).astype(int)
        _weekly_by_design_offset: dict = {}
        for (_dsn, _off), _grp in _wkd.groupby(["_design", "_offset"]):
            _weekly_by_design_offset.setdefault(_dsn, {})[int(_off)] = float(_grp[TARGET].sum())

        if _USE_FESTIVAL_FEATURES:
            _wkd["_is_fest"] = _wkd["_week"].map(lambda w: wk_sig[w][0] > 1.0)
            _design_saw_festival = _wkd.groupby("_design")["_is_fest"].any().to_dict()
        else:
            _design_saw_festival = {}

        _REQUIRED_WEEKS = 13
        _blended = 0
        for i, sku in enumerate(ids):
            design = design_of.get(sku)
            if design is None or design not in _design_launch_map or pd.isna(_design_launch_map[design]):
                continue
            launch_dt = _design_launch_map[design]
            own_offsets = _weekly_by_design_offset.get(design, {})
            active_weeks = len(own_offsets)
            confidence = min(1.0, active_weeks / _REQUIRED_WEEKS)
            if not _design_saw_festival.get(design, False):
                confidence = min(confidence, 0.5)
            if confidence >= 1.0:
                continue
            start_offset = int((last_week - launch_dt).days // 7) + 1
            own_early_vals = [v for o, v in own_offsets.items() if 0 <= o <= 2]
            own_early_level = sum(own_early_vals) / len(own_early_vals) if own_early_vals else 0.0
            curve = _simdes.similar_design_curve(
                design, _weekly_by_design_offset, start_offset, own_early_level,
                horizon_weeks=HORIZON_WEEKS)
            if curve is None:
                continue
            sku_key = str(sku)
            changed = False
            for step in range(HORIZON_WEEKS):
                if curve[step] is None:
                    continue
                blended = confidence * out[sku_key][step] + (1 - confidence) * curve[step]
                out[sku_key][step] = round(max(0.0, blended), 3)
                changed = True
            if changed:
                _blended += 1
        print(f"[lgbm_forecast] cold-start blend applied to {_blended} SKUs", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — cold-start blend must never break the core forecast
        print(f"[lgbm_forecast] cold-start blend failed: {exc!r}", file=sys.stderr)

    # --- activity gate: only SKUs that sold in the last _ACTIVE_WEEKS weeks ----- #
    recent = panel[panel["_week"] >= last_week - pd.Timedelta(weeks=_ACTIVE_WEEKS - 1)]
    active = set(recent.groupby(id_col)[TARGET].sum().loc[lambda s: s > 0].index.astype(str))
    return {s: {"weekly": v} for s, v in out.items() if s in active}


def compute_design(df_full) -> dict:
    """Train weekly XGBoost/LightGBM models at DESIGN_NO grain (a style's
    total demand across all its colors/sizes) and forecast the next
    ``HORIZON_WEEKS`` weeks.

    Same recipe as compute() (see its docstring), minus the SKU-specific
    color/size split and the design_level/similar_design_level cross-features
    (meaningless once the entity itself IS the design), and no cold-start
    blend (design-level aggregation is itself the noise-reduction lever here,
    not a cold-start remedy).

    Returns ``{design_no: {"weekly": [w1..w6]}}`` of predicted gross sold
    units per week."""
    import numpy as np
    import pandas as pd
    import xgboost as xgb
    import lightgbm as lgb

    id_col = COL_DESIGN
    clean = df_full.dropna(subset=[id_col]).copy()
    clean[id_col] = clean[id_col].astype(str).str.strip().str.upper()
    clean[COL_ORDER_DATE] = pd.to_datetime(clean[COL_ORDER_DATE], errors="coerce")
    clean = clean.dropna(subset=[COL_ORDER_DATE])
    clean = clean[clean[COL_ORDER_DATE] <= pd.Timestamp.today().normalize()]
    clean = clean.drop_duplicates()
    for c in _ATTR_COLS:
        if c in clean.columns:
            clean[c] = clean[c].astype(str).str.strip().str.upper()
    status_norm = clean[COL_STATUS].astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    qty = pd.to_numeric(clean[COL_QTY], errors="coerce").fillna(0)
    clean[TARGET] = np.where(~status_norm.isin(_EXCLUDED_NORM), qty, 0.0).astype("float64")
    clean["promo_discount"] = pd.to_numeric(clean.get("promo_discount"), errors="coerce").fillna(0.0) \
        if "promo_discount" in clean.columns else 0.0

    _od = clean[COL_ORDER_DATE]
    clean["_week"] = _od - pd.to_timedelta(_od.dt.weekday, unit="D")
    wk = (
        clean.groupby([id_col, "_week"], as_index=False)
        .agg(**{TARGET: (TARGET, "sum"), "promo_discount": ("promo_discount", "sum")})
    )
    if wk.empty:
        return {}
    _true_last_date = clean[COL_ORDER_DATE].max()
    _last_bucketed_wk = wk["_week"].max()
    if pd.notna(_true_last_date) and _true_last_date < _last_bucketed_wk + pd.Timedelta(days=6):
        wk = wk[wk["_week"] < _last_bucketed_wk]
        if wk.empty:
            return {}

    def _mode(s):
        m = s.dropna()
        return m.mode().iloc[0] if not m.mode().empty else "UNKNOWN"

    agg = {c: _mode for c in _ATTR_COLS if c in clean.columns}
    static = clean.loc[clean[id_col].notna()].groupby(id_col).agg(agg).reset_index()
    for c in _ATTR_COLS:
        if c not in static.columns:
            static[c] = "UNKNOWN"
        static[c] = static[c].fillna("UNKNOWN")

    first_week = wk.groupby(id_col)["_week"].min()
    launch_map = _fetch_launch_dates()
    static["_launch"] = static[id_col].map(launch_map)
    static["_first_week"] = static[id_col].map(first_week)
    static["_launch"] = static["_launch"].fillna(static["_first_week"])

    gmax = wk["_week"].max()
    starts = wk.groupby(id_col)["_week"].min().reset_index(name="_start")
    starts["_w"] = starts["_start"].apply(lambda s: pd.date_range(s, gmax, freq="W-MON"))
    grid = starts.explode("_w")[[id_col, "_w"]].rename(columns={"_w": "_week"})
    panel = grid.merge(wk, on=[id_col, "_week"], how="left").sort_values([id_col, "_week"])
    panel[TARGET] = panel[TARGET].fillna(0.0)
    panel["promo_discount"] = panel["promo_discount"].fillna(0.0)
    panel = panel[panel["_week"].notna()].reset_index(drop=True)

    g = panel.groupby(id_col)[TARGET]
    for L in _WEEK_LAGS:
        panel[f"lag_{L}"] = g.shift(L)
    sh = panel.groupby(id_col)[TARGET].shift(1)
    gg = sh.groupby(panel[id_col])
    for w in _WEEK_ROLL:
        panel[f"rolling_mean_{w}"] = gg.rolling(w, min_periods=1).mean().reset_index(level=0, drop=True)
    for w in _WEEK_STD:
        panel[f"rolling_std_{w}"] = gg.rolling(w, min_periods=2).std().reset_index(level=0, drop=True)
    panel["rolling_max_4"] = gg.rolling(4, min_periods=1).max().reset_index(level=0, drop=True)
    panel["rolling_min_4"] = gg.rolling(4, min_periods=1).min().reset_index(level=0, drop=True)
    panel["sales_diff_1"] = panel["lag_1"] - panel["lag_2"]
    panel["sales_growth_pct"] = np.where(
        panel["lag_2"].fillna(0) == 0, 0.0,
        (panel["lag_1"] - panel["lag_2"]) / panel["lag_2"])
    panel["rolling_mean_ratio"] = np.where(
        panel["rolling_mean_4"].fillna(0) == 0, 0.0,
        panel["lag_1"] / panel["rolling_mean_4"])
    panel["trend_vs_avg"] = panel["lag_1"] - panel["rolling_mean_4"]
    panel["coefficient_variation"] = np.where(
        panel["rolling_mean_4"].fillna(0) == 0, 0.0,
        panel["rolling_std_4"] / panel["rolling_mean_4"])
    panel["avg_weekly_sales"] = g.transform(lambda s: s.shift(1).expanding().mean())
    _is_zero = (panel[TARGET] == 0).astype("float64")
    panel["zero_sales_ratio"] = (
        _is_zero.groupby(panel[id_col]).transform(lambda s: s.shift(1).expanding().mean())
    )
    panel["promotion_flag"] = (panel["promo_discount"] < 0).astype("int8")

    panel["weekofyear"] = panel["_week"].dt.isocalendar().week.fillna(0).astype("int16")
    panel["month"] = panel["_week"].dt.month.astype("int16")
    panel["quarter"] = panel["_week"].dt.quarter.astype("int16")
    panel["year"] = panel["_week"].dt.year.astype("int32")
    panel["day_of_year"] = panel["_week"].dt.dayofyear.astype("int16")
    panel["is_month_start"] = panel["_week"].dt.is_month_start.astype("int8")
    panel["is_month_end"] = panel["_week"].dt.is_month_end.astype("int8")
    panel["is_quarter_start"] = panel["_week"].dt.is_quarter_start.astype("int8")
    panel["is_quarter_end"] = panel["_week"].dt.is_quarter_end.astype("int8")

    if _USE_FESTIVAL_FEATURES:
        import festival
        uniq_weeks = panel["_week"].dropna().unique()
        wk_sig = {w: festival.week_signal(pd.Timestamp(w).date()) for w in uniq_weeks}
        panel["festival_mult"] = panel["_week"].map(lambda w: wk_sig[w][0]).astype("float64")
        panel["festival_event"] = panel["_week"].map(lambda w: wk_sig[w][1] or "None")
        panel["days_to_festival_peak"] = panel["_week"].map(lambda w: wk_sig[w][2]).astype("int32")
        panel["is_festival"] = (panel["festival_mult"] > 1.0).astype("int8")

    panel = panel.merge(static, on=id_col, how="left")
    age = (panel["_week"] - panel["_launch"]).dt.days.astype("float64")
    panel["product_age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32")
    panel = panel.drop(columns=["_launch", "_first_week"])

    lag_cols = [f"lag_{L}" for L in _WEEK_LAGS]
    roll_cols = [f"rolling_mean_{w}" for w in _WEEK_ROLL] + [f"rolling_std_{w}" for w in _WEEK_STD] \
        + ["rolling_max_4", "rolling_min_4"]
    trend_cols = ["sales_diff_1", "sales_growth_pct", "rolling_mean_ratio", "trend_vs_avg",
                  "coefficient_variation", "avg_weekly_sales", "zero_sales_ratio"]
    calendar_cols = ["year", "month", "quarter", "weekofyear", "day_of_year",
                     "is_month_start", "is_month_end", "is_quarter_start", "is_quarter_end"]
    # Design grain has no single COLOR (it varies across the design's own
    # SKUs) — category_name/brand_name/channel_name are the stable per-design
    # attributes (same set used at SKU grain, minus size/design_no which are
    # meaningless once the entity itself IS the design).
    prod_cat = [c for c in _ATTR_COLS if c in panel.columns]
    festival_cols = ["festival_mult", "is_festival", "festival_event", "days_to_festival_peak"] if _USE_FESTIVAL_FEATURES else []
    feature_cols = (lag_cols + roll_cols + trend_cols + calendar_cols
                    + prod_cat + ["product_age_weeks", "promo_discount", "promotion_flag"]
                    + festival_cols)
    cat_cols = prod_cat + (["festival_event"] if _USE_FESTIVAL_FEATURES else [])
    for c in cat_cols:
        panel[c] = panel[c].astype("category")
    cat_dtypes = {c: panel[c].dtype for c in cat_cols}

    _t0 = time.time()
    weeks_sorted = np.sort(panel["_week"].unique())
    n_weeks = len(weeks_sorted)
    train_end = max(1, int(n_weeks * (1 - _VAL_FRAC - _TEST_FRAC)))
    valid_end = max(train_end, int(n_weeks * (1 - _TEST_FRAC)))
    train_weeks, valid_weeks, test_weeks = (
        weeks_sorted[:train_end], weeks_sorted[train_end:valid_end], weeks_sorted[valid_end:])

    def _rows(weeks):
        m = panel["_week"].isin(weeks)
        return panel.loc[m, feature_cols], panel.loc[m, TARGET]

    X_train, y_train = _rows(train_weeks)
    X_valid, y_valid = _rows(valid_weeks)
    X_test, y_test = _rows(test_weeks)
    has_valid, has_test = len(X_valid) > 0, len(X_test) > 0

    if not has_valid:
        print(f"[lgbm_forecast][design] only {n_weeks} distinct weeks — skipping "
              f"grid search, using a single fixed-param XGBoost fit", file=sys.stderr)
        final_xgb = xgb.XGBRegressor(**_XGB_PARAMS)
        final_xgb.fit(panel[feature_cols], panel[TARGET])
        final_lgbm = None

        def _predict(X):
            return final_xgb.predict(X)
    else:
        xgb_search: list[dict] = []
        for params in _XGB_GRID:
            kwargs = dict(
                objective="reg:tweedie", n_estimators=_GRID_N_ESTIMATORS,
                learning_rate=_GRID_LEARNING_RATE, random_state=42, tree_method="hist",
                enable_categorical=True, n_jobs=-1, verbosity=0,
                early_stopping_rounds=_GRID_EARLY_STOP, eval_metric="mae",
                **params,
            )
            m = xgb.XGBRegressor(**kwargs)
            m.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False)
            xgb_search.append({
                "params": params, "model": m,
                "best_iteration": getattr(m, "best_iteration", None),
                "valid_wape": _wape(y_valid, m.predict(X_valid)),
            })
        best_xgb = min(xgb_search, key=lambda r: r["valid_wape"])

        lgbm_search: list[dict] = []
        for params in _LGBM_GRID:
            m = lgb.LGBMRegressor(
                objective="tweedie", n_estimators=_GRID_N_ESTIMATORS,
                learning_rate=_GRID_LEARNING_RATE, subsample_freq=1,
                random_state=42, verbose=-1, n_jobs=-1, **params,
            )
            m.fit(
                X_train, y_train, eval_set=[(X_valid, y_valid)], eval_metric="mae",
                callbacks=[lgb.early_stopping(_GRID_EARLY_STOP, verbose=False)],
            )
            lgbm_search.append({
                "params": params, "model": m,
                "best_iteration": getattr(m, "best_iteration_", None),
                "valid_wape": _wape(y_valid, m.predict(X_valid)),
            })
        best_lgbm = min(lgbm_search, key=lambda r: r["valid_wape"])

        candidates = {
            "xgboost": {"valid": best_xgb["model"].predict(X_valid),
                        "test": best_xgb["model"].predict(X_test) if has_test else np.array([])},
            "lightgbm": {"valid": best_lgbm["model"].predict(X_valid),
                         "test": best_lgbm["model"].predict(X_test) if has_test else np.array([])},
        }
        candidates["blend"] = {
            s: (candidates["xgboost"][s] + candidates["lightgbm"][s]) / 2 for s in ("valid", "test")
        }
        comparison = sorted((
            {"candidate": name, "valid_wape": _wape(y_valid, p["valid"]),
             "test_wape": _wape(y_test, p["test"]) if has_test else float("nan")}
            for name, p in candidates.items()
        ), key=lambda r: r["valid_wape"])
        best_name = comparison[0]["candidate"]
        print(f"[lgbm_forecast][design] grid search done in {time.time() - _t0:.0f}s | "
              f"comparison={comparison} | selected='{best_name}'", file=sys.stderr)

        def _refit_xgb(cfg: dict):
            n_est = cfg["best_iteration"] or _GRID_N_ESTIMATORS
            rm = xgb.XGBRegressor(
                objective="reg:tweedie", n_estimators=max(int(n_est), 1),
                learning_rate=_GRID_LEARNING_RATE, random_state=42, tree_method="hist",
                enable_categorical=True, n_jobs=-1, verbosity=0, **cfg["params"],
            )
            rm.fit(panel[feature_cols], panel[TARGET])
            return rm

        def _refit_lgbm(cfg: dict):
            n_est = cfg["best_iteration"] or _GRID_N_ESTIMATORS
            rm = lgb.LGBMRegressor(
                objective="tweedie", n_estimators=max(int(n_est), 1),
                learning_rate=_GRID_LEARNING_RATE, subsample_freq=1,
                random_state=42, verbose=-1, n_jobs=-1, **cfg["params"],
            )
            rm.fit(panel[feature_cols], panel[TARGET])
            return rm

        final_xgb = _refit_xgb(best_xgb) if best_name in ("xgboost", "blend") else None
        final_lgbm = _refit_lgbm(best_lgbm) if best_name in ("lightgbm", "blend") else None

        def _predict(X):
            if best_name == "xgboost":
                return final_xgb.predict(X)
            if best_name == "lightgbm":
                return final_lgbm.predict(X)
            return (final_xgb.predict(X) + final_lgbm.predict(X)) / 2

        print(f"[lgbm_forecast][design] training total {time.time() - _t0:.0f}s", file=sys.stderr)

    ids = np.sort(panel[id_col].unique())
    W = max(_WEEK_LAGS)
    last_week = panel["_week"].max()
    weeks_idx = pd.date_range(last_week - pd.Timedelta(weeks=W - 1), last_week, freq="W-MON")
    H = (panel[panel["_week"] >= weeks_idx[0]]
         .pivot_table(index=id_col, columns="_week", values=TARGET, aggfunc="sum")
         .reindex(index=ids, columns=weeks_idx).to_numpy(dtype="float64"))
    stat = static.set_index(id_col).reindex(ids)
    n = len(ids)
    out: dict[str, list[float]] = {str(s): [] for s in ids}

    _hist_by_id = panel.groupby(id_col)[TARGET]
    run_sum = _hist_by_id.sum().reindex(ids).fillna(0.0).to_numpy(dtype="float64")
    run_count = _hist_by_id.count().reindex(ids).fillna(0).to_numpy(dtype="float64")
    run_zeros = (
        (panel[TARGET] == 0).groupby(panel[id_col]).sum()
        .reindex(ids).fillna(0.0).to_numpy(dtype="float64")
    )

    for step in range(1, HORIZON_WEEKS + 1):
        wkdate = last_week + pd.Timedelta(weeks=step)
        data: dict = {}
        for L in _WEEK_LAGS:
            data[f"lag_{L}"] = H[:, -L]
        for w in _WEEK_ROLL:
            sl = H[:, -w:]
            cnt = np.sum(~np.isnan(sl), axis=1)
            with np.errstate(invalid="ignore"):
                data[f"rolling_mean_{w}"] = np.nansum(sl, axis=1) / np.where(cnt > 0, cnt, np.nan)
        for w in _WEEK_STD:
            slw = H[:, -w:]
            cntw = np.sum(~np.isnan(slw), axis=1)
            mw = np.where(cntw > 0, np.nansum(slw, axis=1) / np.where(cntw > 0, cntw, np.nan), np.nan)
            with np.errstate(invalid="ignore"):
                data[f"rolling_std_{w}"] = np.sqrt(np.nansum((slw - mw[:, None]) ** 2, axis=1)
                                                    / np.where(cntw >= 2, cntw - 1, np.nan))
        sl4 = H[:, -4:]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            data["rolling_max_4"] = np.nanmax(sl4, axis=1)
            data["rolling_min_4"] = np.nanmin(sl4, axis=1)
        data["sales_diff_1"] = data["lag_1"] - data["lag_2"]
        with np.errstate(invalid="ignore", divide="ignore"):
            data["sales_growth_pct"] = np.where(
                np.nan_to_num(data["lag_2"]) == 0, 0.0,
                (data["lag_1"] - data["lag_2"]) / data["lag_2"])
            data["rolling_mean_ratio"] = np.where(
                np.nan_to_num(data["rolling_mean_4"]) == 0, 0.0,
                data["lag_1"] / data["rolling_mean_4"])
            data["coefficient_variation"] = np.where(
                np.nan_to_num(data["rolling_mean_4"]) == 0, 0.0,
                data["rolling_std_4"] / data["rolling_mean_4"])
        data["trend_vs_avg"] = data["lag_1"] - data["rolling_mean_4"]
        with np.errstate(invalid="ignore", divide="ignore"):
            data["avg_weekly_sales"] = np.where(run_count > 0, run_sum / run_count, 0.0)
            data["zero_sales_ratio"] = np.where(run_count > 0, run_zeros / run_count, 0.0)
        data["year"] = np.full(n, wkdate.year, dtype="int32")
        data["month"] = np.full(n, wkdate.month, dtype="int16")
        data["quarter"] = np.full(n, wkdate.quarter, dtype="int16")
        data["weekofyear"] = np.full(n, int(wkdate.isocalendar()[1]), dtype="int16")
        data["day_of_year"] = np.full(n, wkdate.dayofyear, dtype="int16")
        data["is_month_start"] = np.full(n, int(wkdate.is_month_start), dtype="int8")
        data["is_month_end"] = np.full(n, int(wkdate.is_month_end), dtype="int8")
        data["is_quarter_start"] = np.full(n, int(wkdate.is_quarter_start), dtype="int8")
        data["is_quarter_end"] = np.full(n, int(wkdate.is_quarter_end), dtype="int8")
        data["promo_discount"] = np.zeros(n, dtype="float64")
        data["promotion_flag"] = np.zeros(n, dtype="int8")
        if _USE_FESTIVAL_FEATURES:
            fmult, fevent, fdays = festival.week_signal(wkdate.date())
            data["festival_mult"] = np.full(n, fmult, dtype="float64")
            data["festival_event"] = np.full(n, fevent or "None", dtype=object)
            data["days_to_festival_peak"] = np.full(n, fdays, dtype="int32")
            data["is_festival"] = np.full(n, 1 if fmult > 1.0 else 0, dtype="int8")
        for c in prod_cat:
            if c in stat.columns:
                data[c] = stat[c].to_numpy()
        age = (wkdate - stat["_launch"]).dt.days.astype("float64")
        data["product_age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32").to_numpy()
        X = pd.DataFrame(data)
        for c, dt in cat_dtypes.items():
            if c in X.columns:
                X[c] = X[c].astype(dt)
        preds = np.clip(_predict(X[feature_cols]), 0, None)
        H = np.concatenate([H[:, 1:], preds[:, None]], axis=1)
        run_sum = run_sum + preds
        run_count = run_count + 1
        run_zeros = run_zeros + (preds == 0).astype("float64")
        for i, s in enumerate(ids):
            out[str(s)].append(round(float(preds[i]), 3))

    print(f"[lgbm_forecast][design] forecast built for {len(out)} designs", file=sys.stderr)
    return {s: {"weekly": v} for s, v in out.items()}
