from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

HORIZON_WEEKS = 13
_WEEK_LAGS = [1, 2, 4, 8, 12, 26, 52]
_WEEK_ROLL = [4, 8, 12]
_WEEK_STD = [4, 8]
_ACTIVE_WEEKS = 13
_USE_FESTIVAL_FEATURES = True
MATURATION_DAYS = 7
_LAUNCH_WINDOW_DAYS = 20

COL_QTY = "qty"
COL_STATUS = "order_status"
COL_ORDER_DATE = "order_date"
COL_SKU = "product_sku_code"
COL_DESIGN = "DESIGN_NO"
COL_LAUNCH_DATE = "LAUNCH_DATE"
TARGET = "sales_qty"
_ATTR_COLS = ["category_name", "brand_name", "channel_name"]
_MASTER_ATTR_COLS = {
    "sub_category": "SUB CATEGORY",
    "top_fabric": "TOP_Fabric",
    "top_embroidery": "Top Emboidery_Type",
    "neck_style": "NECK STYLE",
}


def _attach_master_attrs(static, design_col: str) -> None:
    import design_attributes
    lookup = {d: design_attributes.get_forecast_attrs(d) for d in static[design_col].unique()}
    for feat_name, src_col in _MASTER_ATTR_COLS.items():
        static[feat_name] = [lookup.get(d, {}).get(src_col, "UNKNOWN") for d in static[design_col]]


_TIER_FEATURE = "forecast_tier"
_TIER_UNKNOWN = "Unknown"


def _point_in_time_tier(panel, design_col: str):
    import pandas as pd
    import catalog_style
    hist = catalog_style.get_tier_history()
    if not hist:
        return pd.Series(_TIER_UNKNOWN, index=panel.index)
    hist_df = pd.DataFrame(hist).rename(columns={"name": design_col})
    hist_df["effectiveFrom"] = pd.to_datetime(hist_df["effectiveFrom"]).astype("datetime64[ns]")
    hist_df = hist_df.sort_values("effectiveFrom")

    left = panel[[design_col, "_week"]].reset_index().rename(columns={"index": "_orig_idx"})
    left["_week"] = left["_week"].astype("datetime64[ns]")
    left = left.sort_values("_week")
    merged = pd.merge_asof(
        left, hist_df[[design_col, "effectiveFrom", "tier"]],
        left_on="_week", right_on="effectiveFrom", by=design_col, direction="backward",
    )
    result = merged.set_index("_orig_idx")["tier"].reindex(panel.index)
    return result.fillna(_TIER_UNKNOWN)


def _attach_current_tier(static, design_col: str) -> None:
    import catalog_style
    current = catalog_style.get_current_tier_map()
    static[_TIER_FEATURE] = static[design_col].map(current).replace("", _TIER_UNKNOWN).fillna(_TIER_UNKNOWN)

_SOLD_NORM = {
    "delivered", "shipped", "in transit", "ready to ship", "ready for pickup",
    "packed", "new", "processing", "pending",
    "manifested", "out for delivery", "picked up", "reached at destination", "delayed",
}
_GROSS_SALE_NORM = {
    "delivered", "new", "rto delivered", "manifested", "out for delivery",
    "reverse closed", "return received", "cancelled return received", "shipped",
    "cancel init", "ready to ship", "in transit", "return init", "packed",
    "rto in transit", "reached at destination", "reverse delivered", "undelivered",
    "reverse in transit", "partial return received", "rto processing",
    "partial cancelled return received", "rto out for delivery", "return rejected",
    "pending", "reverse out for delivery", "reverse out for pickup", "delayed",
    "reverse cancelled", "picked up", "reverse not picked", "damaged",
    "rto undelivered", "reverse manifest", "processing",
    "lost", "misrouted", "out of delivery area",
    "reached at origin", "reverse picked up",
}

_OUTLIER_CAP_QUANTILE = 0.99


def _cap_non_festival_outliers(wk, id_col: str, week_col: str, target_col: str, fit_cutoff=None):
    import numpy as np
    import pandas as pd

    import festival

    uniq_weeks = wk[week_col].dropna().unique()
    fest_week = {w: festival.week_signal(pd.Timestamp(w).date())[1] is not None for w in uniq_weeks}
    is_fest = wk[week_col].map(fest_week)

    fit_rows = wk.loc[~is_fest] if fit_cutoff is None else wk.loc[~is_fest & (wk[week_col] <= fit_cutoff)]
    caps = fit_rows.groupby(id_col)[target_col].quantile(_OUTLIER_CAP_QUANTILE)
    cap_for_row = wk[id_col].map(caps)
    wk[target_col] = np.where(
        (~is_fest) & cap_for_row.notna() & (wk[target_col] > cap_for_row),
        cap_for_row,
        wk[target_col],
    )
    return wk


_XGB_PARAMS = {
    "objective": "reg:tweedie",
    "tweedie_variance_power": 1.3,
    "n_estimators": 3000,
    "learning_rate": 0.04,
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

_VAL_FRAC = 0.15
_TEST_FRAC = 0.15
_GRID_N_ESTIMATORS = 3000
_GRID_LEARNING_RATE = 0.04
_GRID_EARLY_STOP = 50
_CV_FOLDS = 3
_RECENCY_HALFLIFE_WEEKS = 26
_XGB_GRID = [
    {"max_depth": 8, "min_child_weight": 30, "reg_alpha": 1.0, "reg_lambda": 5.0,
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
    {"max_depth": 8, "num_leaves": 31, "min_child_samples": 30, "reg_alpha": 1.0, "reg_lambda": 5.0,
     "subsample": 0.7, "colsample_bytree": 0.7, "tweedie_variance_power": 1.3},
    {"max_depth": 5, "num_leaves": 63, "min_child_samples": 30, "reg_alpha": 2.0, "reg_lambda": 8.0,
     "subsample": 0.6, "colsample_bytree": 0.6, "tweedie_variance_power": 1.1},
    {"max_depth": -1, "num_leaves": 63, "min_child_samples": 20, "reg_alpha": 0.5, "reg_lambda": 3.0,
     "subsample": 0.8, "colsample_bytree": 0.8, "tweedie_variance_power": 1.3}, 
]

_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
_CACHE_VERSION = "v48"


def _cache_file(snapshot: str) -> Path:
    return _CACHE_DIR / f"lgbm_forecasts_{_CACHE_VERSION}_{snapshot}.json"


def load_cache(snapshot: str) -> dict | None:
    path = _cache_file(snapshot)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_cache(snapshot: str, forecasts: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_file(snapshot).write_text(json.dumps(forecasts))


_DESIGN_CACHE_VERSION = "d35"


def _design_cache_file(snapshot: str) -> Path:
    return _CACHE_DIR / f"lgbm_design_forecasts_{_DESIGN_CACHE_VERSION}_{snapshot}.json"


def load_design_cache(snapshot: str) -> dict | None:
    path = _design_cache_file(snapshot)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_design_cache(snapshot: str, forecasts: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _design_cache_file(snapshot).write_text(json.dumps(forecasts))


_CHANNEL_CACHE_VERSION = "c6"

_OMS_MARKETPLACE_KEYWORDS = [
    ("amazon", "Amazon"),
    ("flipkart", "Flipkart"),
    ("meesho", "Meesho"),
    ("myntra", "Myntra"),
    ("nykaa", "Nykaa"),
    ("wishlink", "Amazon"),
]


def _marketplace_of(channel_name, source) -> str:
    name = str(channel_name).strip().lower()
    if str(source) == "WEBSITE":
        if "mokosh" in name:
            return "MOKOSH"
        if "colorsofearth" in name:
            return "Color's Of Earth"
        return str(channel_name)
    for keyword, label in _OMS_MARKETPLACE_KEYWORDS:
        if keyword in name:
            return label
    return str(channel_name)


def _channel_cache_file(snapshot: str) -> Path:
    return _CACHE_DIR / f"lgbm_channel_forecasts_{_CHANNEL_CACHE_VERSION}_{snapshot}.json"


def load_channel_cache(snapshot: str) -> dict | None:
    path = _channel_cache_file(snapshot)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_channel_cache(snapshot: str, forecasts: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _channel_cache_file(snapshot).write_text(json.dumps(forecasts))


def latest_cached_file(kind: str, on_or_before: str) -> tuple[str, Path, bool] | None:
    """(snapshot ISO, path, is_current_version) of the newest saved forecast
    for ``kind`` ("sku", "design" or "channel") with snapshot <=
    ``on_or_before``, across every cache version (the on-disk schema is the
    same) - the current version wins a same-date tie. None if nothing is
    saved. Lets data.py keep serving the last trained model while a new
    snapshot's (or a new version's) model trains, instead of dropping to
    naive."""
    file_of = {"sku": _cache_file, "design": _design_cache_file, "channel": _channel_cache_file}[kind]
    current = file_of("").name[: -len("_.json")]            # e.g. "lgbm_forecasts_v48"
    family = current.rstrip("0123456789")                    # e.g. "lgbm_forecasts_v"
    best: tuple[str, bool, int, Path] | None = None
    for f in _CACHE_DIR.glob(f"{family}*_*.json"):
        version, _, snap = f.stem.rpartition("_")
        if not version[len(family):].isdigit() or len(snap) != 10 or snap > on_or_before:
            continue
        key = (snap, version == current, int(version[len(family):]), f)
        if best is None or key[:3] > best[:3]:
            best = key
    return (best[0], best[3], best[1]) if best else None


def _wape(y_true, y_pred) -> float:
    import numpy as np
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    denom = np.abs(y_true).sum()
    return float(np.abs(y_true - y_pred).sum() / denom * 100) if denom > 0 else 0.0


def _fetch_launch_dates():
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
                continue
            if dn not in out or d < out[dn]:
                out[dn] = d
        return out
    except Exception as exc:
        print(f"[lgbm_forecast] launch-date fetch failed: {exc!r}", file=sys.stderr)
        return {}


def _recency_weights(weeks):
    import numpy as np
    import pandas as pd

    weeks = pd.Series(weeks)
    ref = weeks.max()
    age_weeks = (ref - weeks).dt.days / 7.0
    return np.power(0.5, age_weeks.clip(lower=0) / _RECENCY_HALFLIFE_WEEKS).to_numpy()


def _rolling_folds(weeks_sorted, n_folds: int, val_frac: float, test_frac: float):
    n = len(weeks_sorted)
    if n < 4:
        train_end = max(1, int(n * (1 - val_frac - test_frac)))
        valid_end = max(train_end, int(n * (1 - test_frac)))
        return [(weeks_sorted[:train_end], weeks_sorted[train_end:valid_end])]

    cv_end = max(1, int(n * (1 - test_frac)))
    val_span = max(1, int(n * val_frac))
    folds = []
    for i in range(n_folds):
        v_end = cv_end - (n_folds - 1 - i) * val_span
        v_start = v_end - val_span
        if v_start <= 0 or v_end <= v_start:
            continue
        folds.append((weeks_sorted[:v_start], weeks_sorted[v_start:v_end]))
    if not folds:
        train_end = max(1, int(n * (1 - val_frac - test_frac)))
        valid_end = max(train_end, cv_end)
        folds = [(weeks_sorted[:train_end], weeks_sorted[train_end:valid_end])]
    return folds


def _train_select_and_refit(panel, feature_cols, target_col, weeks_sorted, test_weeks, log_prefix=""):
    import numpy as np
    import xgboost as xgb
    import lightgbm as lgb

    _t0 = time.time()
    folds = _rolling_folds(weeks_sorted, _CV_FOLDS, _VAL_FRAC, _TEST_FRAC)
    train_weeks, valid_weeks = folds[-1]

    def _rows(weeks):
        m = panel["_week"].isin(weeks)
        return panel.loc[m, feature_cols], panel.loc[m, target_col]

    X_train, y_train = _rows(train_weeks)
    X_valid, y_valid = _rows(valid_weeks)
    X_test, y_test = _rows(test_weeks)
    has_valid, has_test = len(X_valid) > 0, len(X_test) > 0

    if not has_valid:
        print(f"[lgbm_forecast]{log_prefix} only {len(weeks_sorted)} distinct weeks — skipping "
              f"grid search, using a single fixed-param XGBoost fit", file=sys.stderr)
        final_xgb = xgb.XGBRegressor(**_XGB_PARAMS)
        final_xgb.fit(panel[feature_cols], panel[target_col])
        return (lambda X: final_xgb.predict(X)), final_xgb, None

    def _cv_xgb(params):
        wapes, iters = [], []
        for f_train, f_valid in folds:
            Xtr, ytr = _rows(f_train)
            Xva, yva = _rows(f_valid)
            if len(Xtr) == 0 or len(Xva) == 0:
                continue
            m = xgb.XGBRegressor(
                objective="reg:tweedie", n_estimators=_GRID_N_ESTIMATORS,
                learning_rate=_GRID_LEARNING_RATE, random_state=42, tree_method="hist",
                enable_categorical=True, n_jobs=-1, verbosity=0,
                early_stopping_rounds=_GRID_EARLY_STOP, eval_metric="mae", **params,
            )
            m.fit(Xtr, ytr, sample_weight=_recency_weights(panel.loc[panel["_week"].isin(f_train), "_week"]),
                  eval_set=[(Xva, yva)], verbose=False)
            wapes.append(_wape(yva, m.predict(Xva)))
            iters.append(getattr(m, "best_iteration", None) or _GRID_N_ESTIMATORS)
        return (float(np.mean(wapes)) if wapes else float("inf"),
                int(np.median(iters)) if iters else _GRID_N_ESTIMATORS)

    def _cv_lgbm(params):
        wapes, iters = [], []
        for f_train, f_valid in folds:
            Xtr, ytr = _rows(f_train)
            Xva, yva = _rows(f_valid)
            if len(Xtr) == 0 or len(Xva) == 0:
                continue
            m = lgb.LGBMRegressor(
                objective="tweedie", n_estimators=_GRID_N_ESTIMATORS,
                learning_rate=_GRID_LEARNING_RATE, subsample_freq=1,
                random_state=42, verbose=-1, n_jobs=-1, **params,
            )
            m.fit(Xtr, ytr, sample_weight=_recency_weights(panel.loc[panel["_week"].isin(f_train), "_week"]),
                  eval_set=[(Xva, yva)], eval_metric="mae",
                  callbacks=[lgb.early_stopping(_GRID_EARLY_STOP, verbose=False)])
            wapes.append(_wape(yva, m.predict(Xva)))
            iters.append(getattr(m, "best_iteration_", None) or _GRID_N_ESTIMATORS)
        return (float(np.mean(wapes)) if wapes else float("inf"),
                int(np.median(iters)) if iters else _GRID_N_ESTIMATORS)

    xgb_search = []
    for p in _XGB_GRID:
        wape, it = _cv_xgb(p)
        xgb_search.append({"params": p, "cv_wape": wape, "cv_iter": it})
    best_xgb_cfg = min(xgb_search, key=lambda r: r["cv_wape"])

    lgbm_search = []
    for p in _LGBM_GRID:
        wape, it = _cv_lgbm(p)
        lgbm_search.append({"params": p, "cv_wape": wape, "cv_iter": it})
    best_lgbm_cfg = min(lgbm_search, key=lambda r: r["cv_wape"])

    print(f"[lgbm_forecast]{log_prefix} CV grid search done in {time.time() - _t0:.0f}s over "
          f"{len(folds)} fold(s) | best_xgb={best_xgb_cfg['params']} (cv_wape={best_xgb_cfg['cv_wape']:.2f}) "
          f"| best_lgbm={best_lgbm_cfg['params']} (cv_wape={best_lgbm_cfg['cv_wape']:.2f})", file=sys.stderr)

    sw_train = _recency_weights(panel.loc[X_train.index, "_week"])
    m_xgb = xgb.XGBRegressor(
        objective="reg:tweedie", n_estimators=_GRID_N_ESTIMATORS,
        learning_rate=_GRID_LEARNING_RATE, random_state=42, tree_method="hist",
        enable_categorical=True, n_jobs=-1, verbosity=0,
        early_stopping_rounds=_GRID_EARLY_STOP, eval_metric="mae", **best_xgb_cfg["params"],
    )
    m_xgb.fit(X_train, y_train, sample_weight=sw_train, eval_set=[(X_valid, y_valid)], verbose=False)
    m_lgbm = lgb.LGBMRegressor(
        objective="tweedie", n_estimators=_GRID_N_ESTIMATORS,
        learning_rate=_GRID_LEARNING_RATE, subsample_freq=1,
        random_state=42, verbose=-1, n_jobs=-1, **best_lgbm_cfg["params"],
    )
    m_lgbm.fit(X_train, y_train, sample_weight=sw_train, eval_set=[(X_valid, y_valid)], eval_metric="mae",
               callbacks=[lgb.early_stopping(_GRID_EARLY_STOP, verbose=False)])

    valid_wape_xgb = _wape(y_valid, m_xgb.predict(X_valid))
    valid_wape_lgbm = _wape(y_valid, m_lgbm.predict(X_valid))
    inv_x, inv_l = 1.0 / max(valid_wape_xgb, 1e-6), 1.0 / max(valid_wape_lgbm, 1e-6)
    w_xgb = inv_x / (inv_x + inv_l)

    def _blend(xp, lp):
        return w_xgb * xp + (1 - w_xgb) * lp

    candidates = {
        "xgboost": {"valid": m_xgb.predict(X_valid), "test": m_xgb.predict(X_test) if has_test else np.array([])},
        "lightgbm": {"valid": m_lgbm.predict(X_valid), "test": m_lgbm.predict(X_test) if has_test else np.array([])},
    }
    candidates["blend"] = {s: _blend(candidates["xgboost"][s], candidates["lightgbm"][s]) for s in ("valid", "test")}
    comparison = sorted((
        {"candidate": name, "valid_wape": _wape(y_valid, p["valid"]),
         "test_wape": _wape(y_test, p["test"]) if has_test else float("nan")}
        for name, p in candidates.items()
    ), key=lambda r: r["valid_wape"])
    best_name = comparison[0]["candidate"]
    print(f"[lgbm_forecast]{log_prefix} comparison={comparison} | selected='{best_name}' "
          f"| blend_weight_xgb={w_xgb:.2f}", file=sys.stderr)

    try:
        from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
        _bp = candidates[best_name]
        _diag = {
            split: {"r2": round(float(r2_score(y_true, pred)), 3),
                    "mae": round(float(mean_absolute_error(y_true, pred)), 3),
                    "rmse": round(float(np.sqrt(mean_squared_error(y_true, pred))), 3)}
            for split, y_true, pred, ok in (
                ("valid", y_valid, _bp["valid"], has_valid),
                ("test", y_test, _bp["test"], has_test),
            ) if ok
        }
        print(f"[lgbm_forecast]{log_prefix} diagnostics ({best_name}): {_diag}", file=sys.stderr)
    except Exception as exc:
        print(f"[lgbm_forecast]{log_prefix} diagnostics logging failed: {exc!r}", file=sys.stderr)

    full_rows, train_rows = len(panel), len(X_train)

    def _scaled_iters(cv_iter):
        if not cv_iter or train_rows <= 0:
            return _GRID_N_ESTIMATORS
        return max(1, min(_GRID_N_ESTIMATORS, int(cv_iter * (full_rows / train_rows))))

    sw_full = _recency_weights(panel["_week"])
    final_xgb = final_lgbm = None
    if best_name in ("xgboost", "blend"):
        final_xgb = xgb.XGBRegressor(
            objective="reg:tweedie", n_estimators=_scaled_iters(best_xgb_cfg["cv_iter"]),
            learning_rate=_GRID_LEARNING_RATE, random_state=42, tree_method="hist",
            enable_categorical=True, n_jobs=-1, verbosity=0, **best_xgb_cfg["params"],
        )
        final_xgb.fit(panel[feature_cols], panel[target_col], sample_weight=sw_full)
    if best_name in ("lightgbm", "blend"):
        final_lgbm = lgb.LGBMRegressor(
            objective="tweedie", n_estimators=_scaled_iters(best_lgbm_cfg["cv_iter"]),
            learning_rate=_GRID_LEARNING_RATE, subsample_freq=1,
            random_state=42, verbose=-1, n_jobs=-1, **best_lgbm_cfg["params"],
        )
        final_lgbm.fit(panel[feature_cols], panel[target_col], sample_weight=sw_full)

    def _predict(X):
        if best_name == "xgboost":
            return final_xgb.predict(X)
        if best_name == "lightgbm":
            return final_lgbm.predict(X)
        return _blend(final_xgb.predict(X), final_lgbm.predict(X))

    print(f"[lgbm_forecast]{log_prefix} training total {time.time() - _t0:.0f}s", file=sys.stderr)
    return _predict, final_xgb, final_lgbm


def compute(df_full) -> dict:
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
    clean[TARGET] = np.where(status_norm.isin(_GROSS_SALE_NORM), qty, 0.0).astype("float64")
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
    if pd.notna(_true_last_date):
        _maturity_cutoff = _true_last_date - pd.Timedelta(days=MATURATION_DAYS)
        wk = wk[wk["_week"] + pd.Timedelta(days=6) <= _maturity_cutoff]
        if wk.empty:
            return {}
    _cap_all_weeks = pd.date_range(wk["_week"].min(), wk["_week"].max(), freq="W-MON")
    _cap_train_end = max(1, int(len(_cap_all_weeks) * (1 - _VAL_FRAC - _TEST_FRAC)))
    _cap_fit_cutoff = _cap_all_weeks[_cap_train_end - 1]
    wk = _cap_non_festival_outliers(wk, id_col, "_week", TARGET, fit_cutoff=_cap_fit_cutoff)

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
    def _size_of(row):
        sku, dn = str(row[id_col]), str(row["design_no"])
        return sku[len(dn):].lstrip("-") if sku.upper().startswith(dn.upper()) else "UNKNOWN"
    static["size"] = static.apply(_size_of, axis=1).replace("", "UNKNOWN")
    _attach_master_attrs(static, "design_no")
    _attach_current_tier(static, "design_no")

    first_week = wk.groupby(id_col)["_week"].min()
    launch_map = _fetch_launch_dates()
    static["_launch"] = static["design_no"].map(launch_map)
    static["_first_week"] = static[id_col].map(first_week)
    # to_datetime: an empty launch_map (ERP master unavailable) leaves an
    # object column under pandas 3, which breaks the .dt arithmetic below.
    static["_launch"] = pd.to_datetime(static["_launch"].fillna(static["_first_week"]))

    _lwo = wk[[id_col, "_week", TARGET]].merge(static[[id_col, "_launch"]], on=id_col, how="left")
    _lwo = _lwo.dropna(subset=["_launch"])
    _lwo["_dal"] = (_lwo["_week"] - _lwo["_launch"]).dt.days
    _lwo = _lwo[(_lwo["_dal"] >= 0) & (_lwo["_dal"] < _LAUNCH_WINDOW_DAYS)]
    _launch_units = _lwo.groupby(id_col)[TARGET].sum()
    _launch_days = _lwo.groupby(id_col)["_week"].count() * 7
    static["launch_drr"] = static[id_col].map(_launch_units / _launch_days.clip(lower=1)).fillna(0.0).astype("float64")

    gmax = wk["_week"].max()
    starts = wk.groupby(id_col)["_week"].min().reset_index(name="_start")
    starts["_w"] = starts["_start"].apply(lambda s: pd.date_range(s, gmax, freq="W-MON"))
    grid = starts.explode("_w")[[id_col, "_w"]].rename(columns={"_w": "_week"})
    panel = grid.merge(wk, on=[id_col, "_week"], how="left").sort_values([id_col, "_week"])
    panel[TARGET] = panel[TARGET].fillna(0.0)
    panel["promo_discount"] = panel["promo_discount"].fillna(0.0)
    panel = panel[panel["_week"].notna()].reset_index(drop=True)

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
    except Exception as exc:
        print(f"[lgbm_forecast] similar_design borrowing failed: {exc!r}", file=sys.stderr)
        static["similar_design_level"] = 0.0

    try:
        _sim_train_start = _cap_fit_cutoff - pd.Timedelta(weeks=12)
        _rec_train = wk[(wk["_week"] > _sim_train_start) & (wk["_week"] <= _cap_fit_cutoff)].copy()
        _rec_train["_d"] = _rec_train[id_col].map(design_of)
        _dlevel_train = (
            _rec_train.dropna(subset=["_d"]).groupby(["_d", "_week"])[TARGET].sum().groupby(level=0).median()
        )
        sim_level_train = similar_design.borrow_design_level(_dlevel_train)
        static["_similar_design_level_train"] = (
            static[id_col].map(design_of.map(sim_level_train)).fillna(0.0).astype("float64")
        )
    except Exception as exc:
        print(f"[lgbm_forecast] train-region similar_design borrowing failed: {exc!r}", file=sys.stderr)
        static["_similar_design_level_train"] = static["similar_design_level"]

    _design_weekly = (
        wk.assign(_d=wk[id_col].map(design_of)).dropna(subset=["_d"])
          .groupby(["_d", "_week"], as_index=False)[TARGET].sum()
          .sort_values(["_d", "_week"])
    )
    _design_weekly["_design_level_pit"] = (
        _design_weekly.groupby("_d")[TARGET]
        .transform(lambda s: s.shift(1).rolling(12, min_periods=1).median())
    )

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
    panel["launch_drr"] = np.where(age >= _LAUNCH_WINDOW_DAYS, panel["launch_drr"], 0.0)
    panel = panel.drop(columns=["_launch", "_first_week"])

    panel = panel.merge(
        _design_weekly[["_d", "_week", "_design_level_pit"]].rename(columns={"_d": "design_no"}),
        on=["design_no", "_week"], how="left",
    )
    panel["design_level"] = panel["_design_level_pit"].fillna(0.0)
    panel["similar_design_level"] = panel["_similar_design_level_train"]
    panel = panel.drop(columns=["_design_level_pit", "_similar_design_level_train"])

    panel[_TIER_FEATURE] = _point_in_time_tier(panel, "design_no")

    if _USE_FESTIVAL_FEATURES:
        _cat_for_x = panel["category_name"].astype(str) if "category_name" in panel.columns else "UNKNOWN"
        panel["category_festival"] = _cat_for_x + "_" + panel["festival_event"].astype(str)

    lag_cols = [f"lag_{L}" for L in _WEEK_LAGS]
    roll_cols = [f"rolling_mean_{w}" for w in _WEEK_ROLL] + [f"rolling_std_{w}" for w in _WEEK_STD] \
        + ["rolling_max_4", "rolling_min_4"]
    trend_cols = ["sales_diff_1", "sales_growth_pct", "rolling_mean_ratio", "trend_vs_avg",
                  "coefficient_variation", "avg_weekly_sales", "zero_sales_ratio"]
    calendar_cols = ["year", "month", "quarter", "weekofyear", "day_of_year",
                     "is_month_start", "is_month_end", "is_quarter_start", "is_quarter_end"]
    prod_cat = [c for c in _ATTR_COLS if c in panel.columns] \
        + [c for c in _MASTER_ATTR_COLS if c in panel.columns] + [_TIER_FEATURE]
    festival_cols = ["festival_mult", "is_festival", "festival_event", "days_to_festival_peak",
                      "category_festival"] if _USE_FESTIVAL_FEATURES else []
    feature_cols = (lag_cols + roll_cols + trend_cols + calendar_cols
                    + prod_cat + ["design_no", "size", "product_age_weeks",
                                  "design_level", "similar_design_level", "launch_drr",
                                  "promo_discount", "promotion_flag"]
                    + festival_cols)
    cat_cols = prod_cat + ["design_no", "size"] + \
        (["festival_event", "category_festival"] if _USE_FESTIVAL_FEATURES else [])
    for c in cat_cols:
        panel[c] = panel[c].astype("category")
    cat_dtypes = {c: panel[c].dtype for c in cat_cols}

    weeks_sorted = np.sort(panel["_week"].unique())
    valid_end = max(1, int(len(weeks_sorted) * (1 - _TEST_FRAC)))
    test_weeks = weeks_sorted[valid_end:]
    _predict, final_xgb, final_lgbm = _train_select_and_refit(
        panel, feature_cols, TARGET, weeks_sorted, test_weeks,
    )
    if final_xgb is not None:
        _imp = pd.Series(final_xgb.feature_importances_, index=feature_cols).sort_values(ascending=False)
        print(f"[lgbm_forecast] top-10 features (xgboost): {_imp.head(10).round(3).to_dict()}", file=sys.stderr)

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
        if _USE_FESTIVAL_FEATURES:
            _fevent_suffix = f"_{fevent or 'None'}"
            _cat_for_x_step = data["category_name"] if "category_name" in data else np.full(n, "UNKNOWN")
            data["category_festival"] = np.array([f"{c}{_fevent_suffix}" for c in _cat_for_x_step], dtype=object)
        data["design_no"] = stat["design_no"].to_numpy()
        data["size"] = stat["size"].to_numpy()
        data["design_level"] = stat["design_level"].to_numpy()
        data["similar_design_level"] = stat["similar_design_level"].to_numpy()
        data["launch_drr"] = stat["launch_drr"].to_numpy()
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

        try:
            import catalog_style as _catstyle
            _design_tier_map = _catstyle.get_current_tier_map()
        except Exception:
            _design_tier_map = {}
        _COLD_START_TIER_CAP = {"T0": 0.3, "T1": 0.6, "T2": 0.8}

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
            _tier_cap = _COLD_START_TIER_CAP.get(_design_tier_map.get(design, ""))
            if _tier_cap is not None:
                confidence = min(confidence, _tier_cap)
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
    except Exception as exc:
        print(f"[lgbm_forecast] cold-start blend failed: {exc!r}", file=sys.stderr)

    recent = panel[panel["_week"] >= last_week - pd.Timedelta(weeks=_ACTIVE_WEEKS - 1)]
    active = set(recent.groupby(id_col)[TARGET].sum().loc[lambda s: s > 0].index.astype(str))
    return {s: {"weekly": v} for s, v in out.items() if s in active}


def compute_design(df_full) -> dict:
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
    clean[TARGET] = np.where(status_norm.isin(_GROSS_SALE_NORM), qty, 0.0).astype("float64")
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
    if pd.notna(_true_last_date):
        _maturity_cutoff = _true_last_date - pd.Timedelta(days=MATURATION_DAYS)
        wk = wk[wk["_week"] + pd.Timedelta(days=6) <= _maturity_cutoff]
        if wk.empty:
            return {}
    _cap_all_weeks = pd.date_range(wk["_week"].min(), wk["_week"].max(), freq="W-MON")
    _cap_train_end = max(1, int(len(_cap_all_weeks) * (1 - _VAL_FRAC - _TEST_FRAC)))
    _cap_fit_cutoff = _cap_all_weeks[_cap_train_end - 1]
    wk = _cap_non_festival_outliers(wk, id_col, "_week", TARGET, fit_cutoff=_cap_fit_cutoff)

    def _mode(s):
        m = s.dropna()
        return m.mode().iloc[0] if not m.mode().empty else "UNKNOWN"

    agg = {c: _mode for c in _ATTR_COLS if c in clean.columns}
    static = clean.loc[clean[id_col].notna()].groupby(id_col).agg(agg).reset_index()
    for c in _ATTR_COLS:
        if c not in static.columns:
            static[c] = "UNKNOWN"
        static[c] = static[c].fillna("UNKNOWN")
    _attach_master_attrs(static, id_col)
    _attach_current_tier(static, id_col)

    first_week = wk.groupby(id_col)["_week"].min()
    launch_map = _fetch_launch_dates()
    static["_launch"] = static[id_col].map(launch_map)
    static["_first_week"] = static[id_col].map(first_week)
    # to_datetime: an empty launch_map (ERP master unavailable) leaves an
    # object column under pandas 3, which breaks the .dt arithmetic below.
    static["_launch"] = pd.to_datetime(static["_launch"].fillna(static["_first_week"]))

    _lwo = wk[[id_col, "_week", TARGET]].merge(static[[id_col, "_launch"]], on=id_col, how="left")
    _lwo = _lwo.dropna(subset=["_launch"])
    _lwo["_dal"] = (_lwo["_week"] - _lwo["_launch"]).dt.days
    _lwo = _lwo[(_lwo["_dal"] >= 0) & (_lwo["_dal"] < _LAUNCH_WINDOW_DAYS)]
    _launch_units = _lwo.groupby(id_col)[TARGET].sum()
    _launch_days = _lwo.groupby(id_col)["_week"].count() * 7
    static["launch_drr"] = static[id_col].map(_launch_units / _launch_days.clip(lower=1)).fillna(0.0).astype("float64")

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
    panel["launch_drr"] = np.where(age >= _LAUNCH_WINDOW_DAYS, panel["launch_drr"], 0.0)
    panel = panel.drop(columns=["_launch", "_first_week"])
    panel[_TIER_FEATURE] = _point_in_time_tier(panel, id_col)

    if _USE_FESTIVAL_FEATURES:
        _cat_for_x = panel["category_name"].astype(str) if "category_name" in panel.columns else "UNKNOWN"
        panel["category_festival"] = _cat_for_x + "_" + panel["festival_event"].astype(str)

    lag_cols = [f"lag_{L}" for L in _WEEK_LAGS]
    roll_cols = [f"rolling_mean_{w}" for w in _WEEK_ROLL] + [f"rolling_std_{w}" for w in _WEEK_STD] \
        + ["rolling_max_4", "rolling_min_4"]
    trend_cols = ["sales_diff_1", "sales_growth_pct", "rolling_mean_ratio", "trend_vs_avg",
                  "coefficient_variation", "avg_weekly_sales", "zero_sales_ratio"]
    calendar_cols = ["year", "month", "quarter", "weekofyear", "day_of_year",
                     "is_month_start", "is_month_end", "is_quarter_start", "is_quarter_end"]
    prod_cat = [c for c in _ATTR_COLS if c in panel.columns] \
        + [c for c in _MASTER_ATTR_COLS if c in panel.columns] + [_TIER_FEATURE]
    festival_cols = ["festival_mult", "is_festival", "festival_event", "days_to_festival_peak",
                      "category_festival"] if _USE_FESTIVAL_FEATURES else []
    feature_cols = (lag_cols + roll_cols + trend_cols + calendar_cols
                    + prod_cat + ["product_age_weeks", "launch_drr", "promo_discount", "promotion_flag"]
                    + festival_cols)
    cat_cols = prod_cat + (["festival_event", "category_festival"] if _USE_FESTIVAL_FEATURES else [])
    for c in cat_cols:
        panel[c] = panel[c].astype("category")
    cat_dtypes = {c: panel[c].dtype for c in cat_cols}

    weeks_sorted = np.sort(panel["_week"].unique())
    valid_end = max(1, int(len(weeks_sorted) * (1 - _TEST_FRAC)))
    test_weeks = weeks_sorted[valid_end:]
    _predict, final_xgb, final_lgbm = _train_select_and_refit(
        panel, feature_cols, TARGET, weeks_sorted, test_weeks, log_prefix="[design]",
    )

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
        if _USE_FESTIVAL_FEATURES:
            _fevent_suffix = f"_{fevent or 'None'}"
            _cat_for_x_step = data["category_name"] if "category_name" in data else np.full(n, "UNKNOWN")
            data["category_festival"] = np.array([f"{c}{_fevent_suffix}" for c in _cat_for_x_step], dtype=object)
        age = (wkdate - stat["_launch"]).dt.days.astype("float64")
        data["product_age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32").to_numpy()
        data["launch_drr"] = stat["launch_drr"].to_numpy()
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

    try:
        import similar_design as _simdes
        _design_launch_map: dict = dict(zip(static[id_col], static["_launch"]))

        _wkd = wk.copy()
        _wkd["_design"] = _wkd[id_col]
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

        try:
            import catalog_style as _catstyle
            _design_tier_map = _catstyle.get_current_tier_map()
        except Exception:
            _design_tier_map = {}
        _COLD_START_TIER_CAP = {"T0": 0.3, "T1": 0.6, "T2": 0.8}

        _REQUIRED_WEEKS = 13
        _blended = 0
        _raw_total = 0.0
        _new_total = 0.0
        _examples: list[tuple[str, float, float]] = []
        for design in ids:
            if design not in _design_launch_map or pd.isna(_design_launch_map[design]):
                continue
            launch_dt = _design_launch_map[design]
            own_offsets = _weekly_by_design_offset.get(design, {})
            active_weeks = len(own_offsets)
            confidence = min(1.0, active_weeks / _REQUIRED_WEEKS)
            if not _design_saw_festival.get(design, False):
                confidence = min(confidence, 0.5)
            _tier_cap = _COLD_START_TIER_CAP.get(_design_tier_map.get(design, ""))
            if _tier_cap is not None:
                confidence = min(confidence, _tier_cap)
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
            design_key = str(design)
            raw_sum = sum(out[design_key])
            changed = False
            for step in range(HORIZON_WEEKS):
                if curve[step] is None:
                    continue
                blended = confidence * out[design_key][step] + (1 - confidence) * curve[step]
                out[design_key][step] = round(max(0.0, blended), 3)
                changed = True
            if changed:
                _blended += 1
                new_sum = sum(out[design_key])
                _raw_total += raw_sum
                _new_total += new_sum
                _examples.append((design_key, raw_sum, new_sum))
        _examples.sort(key=lambda t: abs(t[2] - t[1]), reverse=True)
        _pct = ((_new_total - _raw_total) / _raw_total * 100) if _raw_total > 0 else 0.0
        print(
            f"[lgbm_forecast][design] cold-start blend applied to {_blended} designs "
            f"(raw {HORIZON_WEEKS}-week total {_raw_total:.0f} -> blended {_new_total:.0f} units, "
            f"{_pct:+.1f}%) | top-5 by absolute change: "
            f"{[(d, round(r, 1), round(n, 1)) for d, r, n in _examples[:5]]}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[lgbm_forecast][design] cold-start blend failed: {exc!r}", file=sys.stderr)

    print(f"[lgbm_forecast][design] forecast built for {len(out)} designs", file=sys.stderr)
    return {s: {"weekly": v} for s, v in out.items()}


_MIN_CHANNEL_ACTIVE_WEEKS = 8


def compute_channel(df_full) -> dict:
    import numpy as np
    import pandas as pd
    import xgboost as xgb
    import lightgbm as lgb

    clean = df_full.dropna(subset=[COL_DESIGN]).copy()
    clean[COL_DESIGN] = clean[COL_DESIGN].astype(str).str.strip().str.upper()
    clean[COL_ORDER_DATE] = pd.to_datetime(clean[COL_ORDER_DATE], errors="coerce")
    clean = clean.dropna(subset=[COL_ORDER_DATE])
    clean = clean[clean[COL_ORDER_DATE] <= pd.Timestamp.today().normalize()]
    clean = clean.drop_duplicates()
    for c in _ATTR_COLS:
        if c in clean.columns:
            clean[c] = clean[c].astype(str).str.strip().str.upper()
    if "source" not in clean.columns:
        clean["source"] = "OMS"
    clean["source"] = clean["source"].fillna("OMS").replace("", "OMS")
    clean["marketplace"] = [
        _marketplace_of(ch, src) for ch, src in zip(clean["channel_name"], clean["source"])
    ]
    clean["_channel_key"] = clean[COL_DESIGN] + "||" + clean["marketplace"]
    id_col = "_channel_key"

    status_norm = clean[COL_STATUS].astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    qty = pd.to_numeric(clean[COL_QTY], errors="coerce").fillna(0)
    clean[TARGET] = np.where(status_norm.isin(_GROSS_SALE_NORM), qty, 0.0).astype("float64")
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
    if pd.notna(_true_last_date):
        _maturity_cutoff = _true_last_date - pd.Timedelta(days=MATURATION_DAYS)
        wk = wk[wk["_week"] + pd.Timedelta(days=6) <= _maturity_cutoff]
        if wk.empty:
            return {}

    active_weeks_count = wk.loc[wk[TARGET] > 0].groupby(id_col)["_week"].nunique()
    eligible_ids = set(active_weeks_count[active_weeks_count >= _MIN_CHANNEL_ACTIVE_WEEKS].index)
    if not eligible_ids:
        print("[lgbm_forecast][channel] no (design, marketplace) pair has enough active weeks yet", file=sys.stderr)
        return {}
    wk = wk[wk[id_col].isin(eligible_ids)]

    _cap_all_weeks = pd.date_range(wk["_week"].min(), wk["_week"].max(), freq="W-MON")
    _cap_train_end = max(1, int(len(_cap_all_weeks) * (1 - _VAL_FRAC - _TEST_FRAC)))
    _cap_fit_cutoff = _cap_all_weeks[_cap_train_end - 1]
    wk = _cap_non_festival_outliers(wk, id_col, "_week", TARGET, fit_cutoff=_cap_fit_cutoff)

    def _mode(s):
        m = s.dropna()
        return m.mode().iloc[0] if not m.mode().empty else "UNKNOWN"

    static_cols = [id_col, COL_DESIGN, "marketplace"] + [c for c in _ATTR_COLS if c in clean.columns]
    agg = {**{c: _mode for c in _ATTR_COLS if c in clean.columns}, COL_DESIGN: "first", "marketplace": "first"}
    static = clean.loc[clean[id_col].notna(), static_cols].groupby(id_col).agg(agg).reset_index()
    for c in _ATTR_COLS:
        if c not in static.columns:
            static[c] = "UNKNOWN"
        static[c] = static[c].fillna("UNKNOWN")
    _attach_master_attrs(static, COL_DESIGN)
    _attach_current_tier(static, COL_DESIGN)

    first_week = wk.groupby(id_col)["_week"].min()
    launch_map = _fetch_launch_dates()
    static["_launch"] = static[COL_DESIGN].map(launch_map)
    static["_first_week"] = static[id_col].map(first_week)
    # to_datetime: an empty launch_map (ERP master unavailable) leaves an
    # object column under pandas 3, which breaks the .dt arithmetic below.
    static["_launch"] = pd.to_datetime(static["_launch"].fillna(static["_first_week"]))

    _lwo = wk[[id_col, "_week", TARGET]].merge(static[[id_col, "_launch"]], on=id_col, how="left")
    _lwo = _lwo.dropna(subset=["_launch"])
    _lwo["_dal"] = (_lwo["_week"] - _lwo["_launch"]).dt.days
    _lwo = _lwo[(_lwo["_dal"] >= 0) & (_lwo["_dal"] < _LAUNCH_WINDOW_DAYS)]
    _launch_units = _lwo.groupby(id_col)[TARGET].sum()
    _launch_days = _lwo.groupby(id_col)["_week"].count() * 7
    static["launch_drr"] = static[id_col].map(_launch_units / _launch_days.clip(lower=1)).fillna(0.0).astype("float64")

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
    panel["launch_drr"] = np.where(age >= _LAUNCH_WINDOW_DAYS, panel["launch_drr"], 0.0)
    panel = panel.drop(columns=["_launch", "_first_week"])
    panel[_TIER_FEATURE] = _point_in_time_tier(panel, COL_DESIGN)

    if _USE_FESTIVAL_FEATURES:
        _cat_for_x = panel["category_name"].astype(str) if "category_name" in panel.columns else "UNKNOWN"
        panel["category_festival"] = _cat_for_x + "_" + panel["festival_event"].astype(str)

    lag_cols = [f"lag_{L}" for L in _WEEK_LAGS]
    roll_cols = [f"rolling_mean_{w}" for w in _WEEK_ROLL] + [f"rolling_std_{w}" for w in _WEEK_STD] \
        + ["rolling_max_4", "rolling_min_4"]
    trend_cols = ["sales_diff_1", "sales_growth_pct", "rolling_mean_ratio", "trend_vs_avg",
                  "coefficient_variation", "avg_weekly_sales", "zero_sales_ratio"]
    calendar_cols = ["year", "month", "quarter", "weekofyear", "day_of_year",
                     "is_month_start", "is_month_end", "is_quarter_start", "is_quarter_end"]
    prod_cat = [c for c in _ATTR_COLS if c in panel.columns] \
        + [c for c in _MASTER_ATTR_COLS if c in panel.columns] + [_TIER_FEATURE]
    festival_cols = ["festival_mult", "is_festival", "festival_event", "days_to_festival_peak",
                      "category_festival"] if _USE_FESTIVAL_FEATURES else []
    feature_cols = (lag_cols + roll_cols + trend_cols + calendar_cols
                    + prod_cat + ["marketplace", "product_age_weeks", "launch_drr",
                                  "promo_discount", "promotion_flag"]
                    + festival_cols)
    cat_cols = prod_cat + ["marketplace"] + \
        (["festival_event", "category_festival"] if _USE_FESTIVAL_FEATURES else [])
    for c in cat_cols:
        panel[c] = panel[c].astype("category")
    cat_dtypes = {c: panel[c].dtype for c in cat_cols}

    weeks_sorted = np.sort(panel["_week"].unique())
    valid_end = max(1, int(len(weeks_sorted) * (1 - _TEST_FRAC)))
    test_weeks = weeks_sorted[valid_end:]
    _predict, final_xgb, final_lgbm = _train_select_and_refit(
        panel, feature_cols, TARGET, weeks_sorted, test_weeks, log_prefix="[channel]",
    )

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
        data["marketplace"] = stat["marketplace"].to_numpy()
        if _USE_FESTIVAL_FEATURES:
            _fevent_suffix = f"_{fevent or 'None'}"
            _cat_for_x_step = data["category_name"] if "category_name" in data else np.full(n, "UNKNOWN")
            data["category_festival"] = np.array([f"{c}{_fevent_suffix}" for c in _cat_for_x_step], dtype=object)
        age = (wkdate - stat["_launch"]).dt.days.astype("float64")
        data["product_age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32").to_numpy()
        data["launch_drr"] = stat["launch_drr"].to_numpy()
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

    print(f"[lgbm_forecast][channel] forecast built for {len(out)} (design, marketplace) pairs", file=sys.stderr)

    fitted_vals = np.clip(_predict(panel[feature_cols]), 0, None)
    fitted_by_id: dict[str, dict[str, float]] = {}
    for key, week_iso, val in zip(panel[id_col], panel["_week"].dt.strftime("%Y-%m-%d"), fitted_vals):
        fitted_by_id.setdefault(str(key), {})[week_iso] = round(float(val), 3)

    nested: dict[str, dict[str, dict]] = {}
    for key, weekly in out.items():
        design_no, marketplace = key.split("||", 1)
        nested.setdefault(design_no, {})[marketplace] = {
            "weekly": weekly,
            "fitted": fitted_by_id.get(key, {}),
        }
    return nested
