"""
WEEKLY XGBoost demand forecasting for the SKU Production Plan API.

Forecasts at WEEKLY granularity (not daily): apparel demand is heavily
intermittent — the median SKU sells ~2 units / 35 days, so at the daily grain
almost every day is zero and the signal drowns in noise. Aggregating to weekly
buckets reduces the intermittency, and a 6-week recursive forecast takes 6 steps
instead of 35 — far less error accumulation, and ~7x less compute.

``compute(df)`` returns ``{sku: {"weekly": [w1..w6]}}`` — predicted gross units
sold in each of the next 6 ISO weeks (Mon-anchored). Cached to disk keyed by the
snapshot date. The target is gross SOLD units (statuses in ``_SOLD_NORM``,
matching ``data._SOLD_STATUSES`` and the seasonal-naive baseline).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the forecasting pipeline modules (config, data_processing) importable.
# They normally live in ../demand_forecasting/, but if that package was flattened
# into the project root they sit one level up instead.
_API_DIR = Path(__file__).resolve().parent
_PKG_DIR = _API_DIR.parent / "demand_forecasting"
if not (_PKG_DIR / "config.py").exists():
    _PKG_DIR = _API_DIR.parent  # flattened layout: pipeline modules at the repo root
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

HORIZON_WEEKS = 6                 # weeks forecast ahead (covers the 35-day = 5-week plan)
_WEEK_LAGS = [1, 2, 3, 4, 8, 12]   # lag features, in WEEKS (+12 = quarter signal)
_WEEK_ROLL = [4, 8, 12]            # rolling-mean windows, in WEEKS
_ACTIVE_WEEKS = 13               # a SKU silent this many weeks gets no forecast
# Feed festival/sale-season signal into the model as an input feature (see
# festival.week_signal), letting it learn per-SKU/category festival response
# instead of only the flat post-hoc multiplier data.py applies afterward.
# Toggled off here (not deleted) for A/B backtesting against the pre-feature
# baseline; module-level so a backtest harness can monkeypatch it per run.
_USE_FESTIVAL_FEATURES = True
# Recent-momentum signal (last week vs its own smoothed 4-week average) — more
# reactive than `trend` (wmean_4/wmean_8, itself already smoothed on both
# sides), meant to reduce the model's systematic under-prediction on genuine
# demand ramp-ups (walk-forward backtest showed the raw model under-shoots
# spikes badly: trees don't extrapolate past values they've seen, and a bare
# lag_1 feature makes them re-learn "recent spike" from splits on a noisy
# absolute count instead of a normalised ratio). Toggled off here (not
# deleted) for A/B backtesting against the pre-feature baseline.
_USE_MOMENTUM_FEATURE = True

# XGBoost params — Tweedie objective for intermittent, non-negative demand;
# native categorical support + NaN handling (early lags are NaN).
_XGB_PARAMS = {
    "objective": "reg:tweedie",
    "tweedie_variance_power": 1.3,
    # Fixed, regularized rounds (no early stopping): the per-iteration
    # tweedie-nloglik metric can emit '-nan(ind)', which XGBoost's early-stopping
    # callback then fails to parse.
    "n_estimators": 1000,
    "learning_rate": 0.04,
    "max_depth": 6,
    "min_child_weight": 5,
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

# Sold statuses (normalised) — kept in sync with data._SOLD_STATUSES.
_SOLD_NORM = {
    "delivered", "shipped", "in transit", "ready to ship",
    "packed", "new", "processing", "pending",
}

_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
# Cache schema version — bump when the structure/model changes (v4 = weekly LGBM,
# v5 = weekly XGBoost, v6 = + design-level/price/trend features, v7 = + festival
# signal as a model feature, v8 = + similar-design cold-start feature, v9 = +
# confidence-weighted similar-design curve blend for young designs, v10 =
# n_estimators 600 -> 1000, lr/depth unchanged, v11 = + vertical (grouped
# DESIGN_GROUP) as a model feature, v12 = similar_design's donor-age gate
# raised 60 -> 90 days, matching the "newly launched" threshold everywhere
# else - a design still counted as newly launched can no longer donate its
# demand level/curve to an even-younger design).
_CACHE_VERSION = "v12"


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


def compute(df_full) -> dict:
    """Train a weekly XGBoost model and forecast the next ``HORIZON_WEEKS`` weeks.

    ``df_full`` is the merged order-history frame. Returns
    ``{sku: {"weekly": [w1..w6]}}`` of predicted gross sold units per week."""
    import numpy as np
    import pandas as pd
    import xgboost as xgb

    import config
    import data_processing as dp
    import verticals

    id_col = "product_sku_code"
    clean = dp.clean_data(df_full)
    # Override the target: gross sold units (match the dashboard), not net demand.
    qty = pd.to_numeric(clean[config.COL_QTY], errors="coerce").fillna(0)
    clean[config.TARGET] = np.where(
        clean["status_norm"].isin(_SOLD_NORM), qty, 0.0).astype("float64")

    # --- weekly panel (Mon-anchored), dense per SKU from first week to last ----- #
    # Monday floor (NOT to_period("W-MON").start_time, which yields TUESDAY and
    # mismatches the date_range(freq="W-MON") grid below → every target became 0).
    _od = clean[config.COL_ORDER_DATE]
    clean["_week"] = _od - pd.to_timedelta(_od.dt.weekday, unit="D")
    wk = clean.groupby([id_col, "_week"], as_index=False)[config.TARGET].sum()
    if wk.empty:
        return {}
    static = dp.build_static_attrs(clean, id_col)
    # Product vertical (see verticals.py) - a coarser grouping of DESIGN_GROUP's
    # 14 raw values into ~7 buckets, so the model can borrow statistical
    # strength across sparse DESIGN_GROUPs (e.g. SET/SHRUGE SET, a few hundred
    # rows each) from their larger vertical siblings (CO-ORDS, 72K rows).
    if "DESIGN_GROUP" in static.columns:
        static["vertical"] = static["DESIGN_GROUP"].map(verticals.vertical_of).fillna(verticals.VERTICAL_UNKNOWN)
    else:
        static["vertical"] = verticals.VERTICAL_UNKNOWN
    gmax = wk["_week"].max()
    starts = wk.groupby(id_col)["_week"].min().reset_index(name="_start")
    starts["_w"] = starts["_start"].apply(lambda s: pd.date_range(s, gmax, freq="W-MON"))
    grid = starts.explode("_w")[[id_col, "_w"]].rename(columns={"_w": "_week"})
    panel = grid.merge(wk, on=[id_col, "_week"], how="left").sort_values([id_col, "_week"])
    panel[config.TARGET] = panel[config.TARGET].fillna(0.0)
    # Drop any NaT week rows (root cause of the calendar-feature int-cast crashes).
    panel = panel[panel["_week"].notna()].reset_index(drop=True)

    # --- cross-SKU drivers (static per SKU): hierarchical design-level demand +
    # realized price. design_level gives the model its parent's (stable) volume —
    # the strongest lever for the intermittent tail. ----------------------------- #
    _cut = gmax - pd.Timedelta(weeks=12)
    _design_of = clean.dropna(subset=[id_col]).groupby(id_col)[config.COL_DESIGN].first()
    _rec = wk[wk["_week"] > _cut].copy()
    _rec["_d"] = _rec[id_col].map(_design_of)
    _dlevel = (_rec.dropna(subset=["_d"]).groupby(["_d", "_week"])[config.TARGET].sum()
               .groupby(level=0).mean())                       # design -> avg weekly total
    static["design_level"] = static[id_col].map(_design_of.map(_dlevel)).fillna(0.0).astype("float64")
    # Similar-design borrowing (cold start): for designs with no siblings and
    # little/no own recent history, design_level above is 0 — borrow demand
    # instead from designs that share the same fabric/embellishment articles
    # (BOM fingerprint from similar_design.py), gated by the *donor* design's
    # own launch-date age so a brand-new design can't borrow from an equally
    # unproven neighbor. Complements design_level with cross-design signal.
    try:
        import similar_design
        sim_level = similar_design.borrow_design_level(_dlevel)
        static["similar_design_level"] = static[id_col].map(_design_of.map(sim_level)).fillna(0.0).astype("float64")
    except Exception as exc:  # noqa: BLE001 — never let this block training
        print(f"[lgbm_forecast] similar_design borrowing failed: {exc!r}", file=sys.stderr)
        static["similar_design_level"] = 0.0
    if "total" in clean.columns:
        _pr = clean[clean["_week"] > _cut]
        _tot = pd.to_numeric(_pr["total"], errors="coerce").groupby(_pr[id_col]).sum()
        _qs = pd.to_numeric(_pr[config.COL_QTY], errors="coerce").groupby(_pr[id_col]).sum()
        static["price"] = static[id_col].map(_tot / _qs.where(_qs > 0)).fillna(0.0).astype("float64")
    else:
        static["price"] = 0.0

    # --- features (all from PAST weeks; no leakage) ---------------------------- #
    g = panel.groupby(id_col)[config.TARGET]
    for L in _WEEK_LAGS:
        panel[f"wlag_{L}"] = g.shift(L)
    sh = panel.groupby(id_col)[config.TARGET].shift(1)
    gg = sh.groupby(panel[id_col])
    for w in _WEEK_ROLL:
        panel[f"wmean_{w}"] = gg.rolling(w, min_periods=1).mean().reset_index(level=0, drop=True)
    panel["wstd_4"] = gg.rolling(4, min_periods=2).std().reset_index(level=0, drop=True)
    # trend: recent 4-week mean vs medium 8-week mean (lifecycle signal).
    panel["trend"] = panel["wmean_4"] / panel["wmean_8"].where(panel["wmean_8"] > 0)
    if _USE_MOMENTUM_FEATURE:
        # momentum: last week alone vs its own smoothed 4-week average — a
        # sharper, less-lagged ramp-up signal than `trend` above.
        panel["momentum"] = panel["wlag_1"] / panel["wmean_4"].where(panel["wmean_4"] > 0)
    # isocalendar().week is a NULLABLE masked array on pandas 3.x — fillna before
    # the int cast (a stray <NA> here is what silently broke the model → naive).
    panel["weekofyear"] = panel["_week"].dt.isocalendar().week.fillna(0).astype("int16")
    panel["month"] = panel["_week"].dt.month.astype("int16")

    # Festival/sale-season signal AS A MODEL FEATURE (distinct from the
    # post-hoc multiplicative uplift data.py applies to the final blended
    # forecast — see festival.week_signal's docstring). Computed once per
    # distinct week (not per SKU-week row) and mapped back, since there are
    # far fewer unique weeks than panel rows.
    if _USE_FESTIVAL_FEATURES:
        import festival
        uniq_weeks = panel["_week"].dropna().unique()
        wk_sig = {wk: festival.week_signal(pd.Timestamp(wk).date()) for wk in uniq_weeks}
        panel["festival_mult"] = panel["_week"].map(lambda w: wk_sig[w][0]).astype("float64")
        panel["festival_event"] = panel["_week"].map(lambda w: wk_sig[w][1] or "None")
        panel["days_to_festival_peak"] = panel["_week"].map(lambda w: wk_sig[w][2]).astype("int32")
        panel["is_festival"] = (panel["festival_mult"] > 1.0).astype("int8")

    panel = panel.merge(static, on=id_col, how="left")
    if config.COL_LAUNCH_DATE in panel.columns:
        # .astype(float) so NaT launch dates become NaN (not nullable <NA>, which
        # breaks the later .astype("int32") on pandas 3.x).
        age = (panel["_week"] - panel[config.COL_LAUNCH_DATE]).dt.days.astype("float64")
        panel["age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32")
        panel = panel.drop(columns=[config.COL_LAUNCH_DATE])
    else:
        panel["age_weeks"] = -1

    lag_cols = [f"wlag_{L}" for L in _WEEK_LAGS]
    roll_cols = [f"wmean_{w}" for w in _WEEK_ROLL] + ["wstd_4"]
    prod_cat = [c for c in config.PRODUCT_ATTR_COLS if c in panel.columns]
    festival_cols = ["festival_mult", "is_festival", "festival_event", "days_to_festival_peak"] if _USE_FESTIVAL_FEATURES else []
    momentum_cols = ["momentum"] if _USE_MOMENTUM_FEATURE else []
    feature_cols = (lag_cols + roll_cols + ["trend", "weekofyear", "month"]
                    + prod_cat + ["vertical", "age_weeks", "design_level", "similar_design_level", "price"]
                    + festival_cols + momentum_cols)
    cat_cols = prod_cat + ["vertical", "month"] + (["festival_event"] if _USE_FESTIVAL_FEATURES else [])
    for c in cat_cols:
        panel[c] = panel[c].astype("category")
    cat_dtypes = {c: panel[c].dtype for c in cat_cols}

    # --- train (XGBoost, fixed rounds) ----------------------------------------- #
    model = xgb.XGBRegressor(**_XGB_PARAMS)
    model.fit(panel[feature_cols], panel[config.TARGET])

    # --- recursive weekly forecast (vectorised history matrix) ----------------- #
    ids = np.sort(panel[id_col].unique())
    W = max(_WEEK_LAGS)
    last_week = panel["_week"].max()
    weeks_idx = pd.date_range(last_week - pd.Timedelta(weeks=W - 1), last_week, freq="W-MON")
    H = (panel[panel["_week"] >= weeks_idx[0]]
         .pivot_table(index=id_col, columns="_week", values=config.TARGET, aggfunc="sum")
         .reindex(index=ids, columns=weeks_idx).to_numpy(dtype="float64"))
    stat = static.set_index(id_col).reindex(ids)
    n = len(ids)
    out: dict[str, list[float]] = {str(s): [] for s in ids}

    for step in range(1, HORIZON_WEEKS + 1):
        wkdate = last_week + pd.Timedelta(weeks=step)
        data: dict = {}
        for L in _WEEK_LAGS:
            data[f"wlag_{L}"] = H[:, -L]
        for w in _WEEK_ROLL:
            sl = H[:, -w:]
            cnt = np.sum(~np.isnan(sl), axis=1)
            with np.errstate(invalid="ignore"):
                data[f"wmean_{w}"] = np.nansum(sl, axis=1) / np.where(cnt > 0, cnt, np.nan)
        sl4 = H[:, -4:]
        cnt4 = np.sum(~np.isnan(sl4), axis=1)
        m4 = np.where(cnt4 > 0, np.nansum(sl4, axis=1) / np.where(cnt4 > 0, cnt4, np.nan), np.nan)
        with np.errstate(invalid="ignore"):
            data["wstd_4"] = np.sqrt(np.nansum((sl4 - m4[:, None]) ** 2, axis=1)
                                     / np.where(cnt4 >= 2, cnt4 - 1, np.nan))
        with np.errstate(invalid="ignore", divide="ignore"):
            data["trend"] = np.where(data["wmean_8"] > 0, data["wmean_4"] / data["wmean_8"], np.nan)
        if _USE_MOMENTUM_FEATURE:
            with np.errstate(invalid="ignore", divide="ignore"):
                data["momentum"] = np.where(data["wmean_4"] > 0, data["wlag_1"] / data["wmean_4"], np.nan)
        data["weekofyear"] = np.full(n, int(wkdate.isocalendar()[1]), dtype="int16")
        data["month"] = np.full(n, wkdate.month, dtype="int16")
        if _USE_FESTIVAL_FEATURES:
            fmult, fevent, fdays = festival.week_signal(wkdate.date())
            data["festival_mult"] = np.full(n, fmult, dtype="float64")
            data["festival_event"] = np.full(n, fevent or "None", dtype=object)
            data["days_to_festival_peak"] = np.full(n, fdays, dtype="int32")
            data["is_festival"] = np.full(n, 1 if fmult > 1.0 else 0, dtype="int8")
        for c in config.PRODUCT_ATTR_COLS:
            if c in stat.columns:
                data[c] = stat[c].to_numpy()
        data["vertical"] = stat["vertical"].to_numpy()
        data["design_level"] = stat["design_level"].to_numpy()
        data["similar_design_level"] = stat["similar_design_level"].to_numpy()
        data["price"] = stat["price"].to_numpy()
        if config.COL_LAUNCH_DATE in stat.columns:
            age = (wkdate - stat[config.COL_LAUNCH_DATE]).dt.days.astype("float64")
            data["age_weeks"] = (age.clip(lower=0) / 7).fillna(-1).astype("int32").to_numpy()
        else:
            data["age_weeks"] = np.full(n, -1, dtype="int32")
        X = pd.DataFrame(data)
        for c, dt in cat_dtypes.items():
            if c in X.columns:
                X[c] = X[c].astype(dt)
        preds = np.clip(model.predict(X[feature_cols]), 0, None)
        H = np.concatenate([H[:, 1:], preds[:, None]], axis=1)
        for i, s in enumerate(ids):
            out[str(s)].append(round(float(preds[i]), 3))

    # --- cold-start blend: for designs still young/unproven, blend the
    # model's own recursive forecast with a demand-curve shape borrowed from
    # material-similar designs at the SAME weeks-since-launch offset (see
    # similar_design.similar_design_curve). confidence = min(1, activeWeeks/13)
    # capped at 0.5 until the design has lived through at least one festival
    # week — a design that's technically "old enough" but launched just
    # before its category's peak season shouldn't be treated as proven yet.
    # This is distinct from the similar_design_level FEATURE fed into the
    # model above (a flat borrowed average the trees can split on); this pass
    # borrows the actual week-by-week SHAPE, which a flat feature can't give
    # a design that has never itself seen a seasonal peak.
    try:
        import similar_design as _simdes
        _design_launch_map: dict = {}
        if config.COL_LAUNCH_DATE in static.columns:
            _design_launch_map = (
                static.set_index(id_col)[config.COL_LAUNCH_DATE]
                .groupby(static[id_col].map(_design_of).to_numpy())
                .min().dropna().to_dict()
            )

        _wkd = wk.copy()
        _wkd["_design"] = _wkd[id_col].map(_design_of)
        _wkd = _wkd.dropna(subset=["_design"])
        _wkd["_launch"] = _wkd["_design"].map(_design_launch_map)
        _wkd = _wkd.dropna(subset=["_launch"])
        _wkd["_offset"] = ((_wkd["_week"] - _wkd["_launch"]).dt.days // 7).astype(int)
        _weekly_by_design_offset: dict = {}
        for (_dsn, _off), _grp in _wkd.groupby(["_design", "_offset"]):
            _weekly_by_design_offset.setdefault(_dsn, {})[int(_off)] = float(_grp[config.TARGET].sum())

        if _USE_FESTIVAL_FEATURES:
            _wkd["_is_fest"] = _wkd["_week"].map(lambda w: wk_sig[w][0] > 1.0)
            _design_saw_festival = _wkd.groupby("_design")["_is_fest"].any().to_dict()
        else:
            _design_saw_festival = {}

        _REQUIRED_WEEKS = 13
        _blended = 0
        for i, sku in enumerate(ids):
            design = _design_of.get(sku)
            if design is None or design not in _design_launch_map:
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
    active = set(recent.groupby(id_col)[config.TARGET].sum().loc[lambda s: s > 0].index.astype(str))
    return {s: {"weekly": v} for s, v in out.items() if s in active}
