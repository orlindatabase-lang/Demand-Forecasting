
from __future__ import annotations

import os
import random
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import festival
import lifecycle
from models import (
    BreakdownResponse,
    ForecastPoint,
    HistoricalPoint,
    PlanRow,
    TopRegion,
    TopWarehouse,
)

# Snapshot ("data as of") date. Set dynamically to the latest order_date in the
# loaded data; this default is only used by the mock fallback.
SNAPSHOT_DATE = date(2026, 6, 8)
NUM_SKUS = 1000

# Data source: "live" fetches fresh from BigQuery + ERP (see live_source.py),
# "csv" reads the local snapshot. Live falls back to the CSV on any failure.
DATA_SOURCE = os.getenv("DATA_SOURCE", "live").strip().lower()
# "lgbm" trains the model in the background (seasonal-naive serves until ready);
# "naive" uses the baseline only.
FORECAST_MODEL = os.getenv("FORECAST_MODEL", "lgbm").strip().lower()
# Final weekly forecast = FORECAST_BLEND * model + (1 - blend) * SKU run-rate,
# anchoring the global model to each SKU's own level. 1.0 = pure model, 0 = naive.
FORECAST_BLEND = float(os.getenv("FORECAST_BLEND", "0.5"))
# Daily auto-refresh: re-fetch live data once a day at this local time. Set
# DAILY_REFRESH=off to disable. Default 06:00 (before business hours).
DAILY_REFRESH = os.getenv("DAILY_REFRESH", "on").strip().lower() != "off"
REFRESH_HOUR = int(os.getenv("REFRESH_HOUR", "6"))
REFRESH_MINUTE = int(os.getenv("REFRESH_MINUTE", "0"))
# Inventory / production policy (Module 2+6: safety stock + reorder point + MOQ).
# Replenishment lead time = how long a new production lot takes; the default is
# the empirically-observed median (~12 weeks), overridden per-design by the
# delay model's real lot lead times when available (see _lead_days).
PROD_LEAD_DAYS = int(os.getenv("PROD_LEAD_DAYS", "84"))
SERVICE_Z = float(os.getenv("SERVICE_Z", "1.65"))        # 1.65 ≈ 95% service level
REVIEW_DAYS = int(os.getenv("REVIEW_DAYS", "7"))         # weekly planning cycle
PRODUCTION_MOQ = int(os.getenv("PRODUCTION_MOQ", "50"))  # min production lot size
# Local snapshot of the merged order history (fallback / DATA_SOURCE=csv).
DATA_FILE = Path(__file__).resolve().parent.parent / "final_merged_data.csv"
# Columns the plan + top tables need from the source frame.
_SOURCE_COLS = ["product_sku_code", "qty", "order_status", "order_date",
                "DESIGN_NO", "TOTAL_WIP_QTY", "PENDING_QTY_PIECES",
                "buyer_state", "buyer_city", "warehouse_name", "total"]
# Extra columns the LightGBM forecaster needs (product attributes for features).
_FORECAST_COLS = ["listing_sku_code", "DESIGN_GROUP", "COLOR", "SECTION",
                  "CATALOG_NAME", "LAUNCH_DATE"]
_FULL_COLS = _SOURCE_COLS + _FORECAST_COLS
# Order statuses that count as realized demand (exclude cancellations & returns).
_SOLD_STATUSES = {
    "Delivered", "Shipped", "In Transit", "Ready to ship",
    "Packed", "New", "Processing", "Pending",
}
# Weeks of real weekly sales kept in memory for the drill-down history.
_HIST_WEEKS = 8
# Weeks forecast ahead (matches lgbm_forecast.HORIZON_WEEKS; 5 weeks ≈ 35-day plan).
_FC_WEEKS = 6
# Sales windows (in days) reported by the top-selling tables.
_PERIODS = (10, 30, 90)
# (SKU velocity tiers + the lifecycle stages, scores and reorder policy now live
# in lifecycle.py, which classify()s the plan rows.)

SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
STATES = [
    "Maharashtra", "Gujarat", "Delhi", "Karnataka", "West Bengal",
    "Uttar Pradesh", "Tamil Nadu", "Rajasthan", "Madhya Pradesh", "Telangana",
]
CITIES = [
    "Mumbai", "Surat", "New Delhi", "Bengaluru", "Kolkata", "Lucknow",
    "Chennai", "Jaipur", "Indore", "Hyderabad", "Pune", "Ahmedabad",
    "Nagpur", "Bhopal", "Patna",
]
WAREHOUSES = ["Surat DC", "Mumbai FC", "Delhi FC", "Bengaluru FC", "Kolkata DC"]


def _build_plan() -> tuple[list[PlanRow], list[dict]]:
    rng = random.Random(20260608)
    # Enough designs that designs x sizes x 2 variants comfortably exceeds
    # NUM_SKUS, so generation is unique AND always terminates.
    num_designs = 90
    designs = [f"{401 + i:03d}-{(i % 9) + 1:02d}" for i in range(num_designs)]
    iso = SNAPSHOT_DATE.isoformat()
    combos = num_designs * len(SIZES)  # unique (design, size) pairs per variant pass

    rows: list[PlanRow] = []
    facts: list[dict] = []  # geography + units/revenue for the top-N aggregates
    seen: set[str] = set()
    i = 0
    while len(rows) < NUM_SKUS:
        # vary design and size independently so the first pass covers every
        # (design, size) combo; the second pass adds an "-A" variant.
        design = designs[(i // len(SIZES)) % num_designs]
        size = SIZES[i % len(SIZES)]
        suffix = "-A" if i >= combos else ""
        sku = f"{design}-{size}{suffix}"
        i += 1
        if sku in seen:
            continue
        seen.add(sku)

        base_daily = rng.uniform(0.3, 18.0)
        growth = round(rng.uniform(-22, 32), 1)
        gf = 1 + growth / 100

        f7 = round(base_daily * 7 * rng.uniform(0.85, 1.15))
        f10 = round(base_daily * 10 * gf)
        f30 = round(base_daily * 30 * gf)
        f35 = round(base_daily * 35 * gf * rng.uniform(0.96, 1.08))
        f90 = round(base_daily * 90 * gf)
        hist10 = round(base_daily * 10 * rng.uniform(0.78, 1.12))

        inventory = round(f30 * rng.uniform(0.2, 1.8))
        wip = round(f30 * rng.uniform(0.0, 0.7))
        available = inventory + wip
        total = f35
        calculated = max(0, total - available)

        # geography + value for the top-N aggregates (10 / 30 / 90-day sales)
        price = round(rng.uniform(699, 2999) / 10) * 10
        facts.append({
            "state": rng.choice(STATES),
            "city": rng.choice(CITIES),
            "warehouse": rng.choice(WAREHOUSES),
            "units10": f10,
            "units30": f30,
            "units90": f90,
            "revenue10": f10 * price,
            "revenue30": f30 * price,
            "revenue90": f90 * price,
        })

        rows.append(
            PlanRow(
                skuCode=sku,
                designNo=design,
                date=iso,
                forecast7=f7,
                forecast10=f10,
                forecast35=f35,
                inventoryQty=inventory,
                wipQty=wip,
                availableQty=available,
                totalSuggestedProduction=total,
                calculatedProductionSuggestion=calculated,
                stockStatus="In Stock" if available >= total else "Produce",
                historicalLast10d=hist10,
            )
        )
    return rows, facts


def _source_dataframe():
    """Return the merged order history as a DataFrame.

    Uses the live BigQuery + ERP feed when ``DATA_SOURCE='live'`` (the default),
    falling back to the local CSV snapshot on any failure so the API still
    starts. ``DATA_SOURCE='csv'`` forces the local snapshot.
    """
    import pandas as pd

    if DATA_SOURCE == "live":
        try:
            import live_source
            df = live_source.assemble()
            df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
            print(f"[data] live source: {len(df):,} rows fetched", file=sys.stderr)
            return df
        except Exception as exc:  # noqa: BLE001
            print(f"[data] live fetch failed ({exc!r}); falling back to CSV", file=sys.stderr)

    cols = [c for c in _FULL_COLS]  # read what both the plan and forecaster need
    return pd.read_csv(DATA_FILE, usecols=lambda c: c in cols, parse_dates=["order_date"])


def _forecast_week_start(snap_date: date, w: int) -> date:
    """Monday start of the w-th forecast week (w=0 → first full week after the
    snapshot's week), matching lgbm_forecast's Mon-anchored weeks."""
    last_monday = snap_date - timedelta(days=snap_date.weekday())
    return last_monday + timedelta(days=7 * (w + 1))


def _week_festival(week_start: date) -> tuple[float, str | None]:
    """Average festival / sale uplift over a week's 7 days + the dominant event."""
    total = 0.0
    counts: dict[str, int] = {}
    for i in range(7):
        mult, event = festival.festival_factor(week_start + timedelta(days=i))
        total += mult
        if event:
            counts[event] = counts.get(event, 0) + 1
    return total / 7, (max(counts, key=counts.get) if counts else None)


_BACKTEST_MAX_SNAPSHOTS = 21  # ~3 weeks of daily model-forecast snapshots


def _backtest_model_accuracy(
    sales, snap: "pd.Timestamp"
) -> tuple[int, int, int, dict[str, int], dict[str, dict[str, float]]]:
    """Genuine walk-forward accuracy for the deployed (LightGBM-blended)
    forecast, using each day's already-cached raw model output.

    This is the ONLY way to score the actual model rather than the naive
    baseline: a same-day metric can't, because the weeks a fresh forecast
    predicts haven't happened yet. Each day's daily-refresh already saves its
    raw weekly LGBM output to disk (lgbm_forecast.save_cache) — once enough
    time has passed for that snapshot's week-1 to actually occur, we can
    compare its prediction (re-blended exactly as it would have been shown)
    against what really happened.

    Returns (model_pct, naive_pct, snapshots_used, sku_accuracy, sku_week_pred):
      - model_pct/naive_pct/snapshots_used: pooled, volume-weighted (as before).
      - sku_accuracy: {sku: accuracy_pct} — same backtest, not pooled across SKUs.
      - sku_week_pred: {sku: {week_start_iso: model_pred}} — the model's actual
        historical prediction for weeks that were genuinely backtested (the
        most recent snapshot covering each week wins). Used by get_breakdown()
        so the drill-down's "historical forecast" is the real model, not the
        naive baseline, wherever a backtested value is available.
    """
    import json as _json
    import pandas as pd

    cache_dir = Path(__file__).resolve().parent / ".cache"
    # Match every cache version, not just the current one. The on-disk schema
    # ({sku: {"weekly": [...]}})  has been stable across version bumps (a
    # version bump just invalidates in-memory forecast caches to force a
    # retrain, e.g. when new features are added) — restricting this glob to
    # _CACHE_VERSION meant every version bump silently threw away all
    # already-accumulated backtest history and this fell back to the naive
    # proxy for ~2 weeks until enough same-version snapshots re-accumulated.
    #
    # Sort by the DATE embedded in the filename, not the filename string
    # itself: "lgbm_forecasts_v10_..." sorts BEFORE "lgbm_forecasts_v6_..."
    # lexically ('1' < '6'), so once the version reached v10 the plain
    # `sorted(glob(...))` below silently pushed every v10+ snapshot to the
    # FRONT of the list — permanently excluding it from the "most recent N"
    # tail slice, since old v6-v9 files are never pruned and always out-sort
    # a lower-lexical double-digit version. Verified this left the pooled
    # accuracy stuck scoring 16 stale v6 snapshots even right after a fresh
    # v10 retrain.
    def _snap_date(f: Path) -> date:
        try:
            return date.fromisoformat(f.stem.rsplit("_", 1)[-1])
        except ValueError:
            return date.min

    pattern = "lgbm_forecasts_v*_*.json"
    files = sorted(cache_dir.glob(pattern), key=_snap_date)[-_BACKTEST_MAX_SNAPSHOTS:]

    model_err = model_act = naive_err = naive_act = 0.0
    sku_err: dict[str, float] = {}
    sku_act: dict[str, float] = {}
    sku_week_pred: dict[str, dict[str, float]] = {}
    used = 0
    for f in files:
        try:
            snap_date = date.fromisoformat(f.stem.rsplit("_", 1)[-1])
        except ValueError:
            continue

        week1_start = _forecast_week_start(snap_date, 0)
        week1_end = week1_start + timedelta(days=6)
        if pd.Timestamp(week1_end) >= snap:
            continue  # that snapshot's week 1 hasn't fully elapsed yet

        try:
            forecasts = _json.loads(f.read_text())
        except Exception:
            continue
        if not forecasts:
            continue

        wk_sales = sales[
            (sales["order_date"] >= pd.Timestamp(week1_start))
            & (sales["order_date"] <= pd.Timestamp(week1_end))
        ]
        actual = wk_sales.groupby("product_sku_code")["qty"].sum()

        # Naive run-rate AS OF the snapshot date (last 35 days up to it) —
        # reconstructed from today's full history since only the model's own
        # weekly output was ever cached, not the naive figure alongside it.
        lo = pd.Timestamp(snap_date) - pd.Timedelta(days=34)
        hist = sales[(sales["order_date"] >= lo) & (sales["order_date"] <= pd.Timestamp(snap_date))]
        naive_week = hist.groupby("product_sku_code")["qty"].sum() / 5.0

        mult = _week_festival(week1_start)[0]
        used += 1

        for sku, fc in forecasts.items():
            lgbm_weekly = fc.get("weekly") if fc else None
            if not lgbm_weekly:
                continue
            nw  = float(naive_week.get(sku, 0.0))
            act = float(actual.get(sku, 0.0))
            model_pred = max(0.0, (FORECAST_BLEND * lgbm_weekly[0] + (1 - FORECAST_BLEND) * nw) * mult)
            naive_pred = max(0.0, nw * mult)
            model_err += abs(act - model_pred)
            naive_err += abs(act - naive_pred)
            model_act += act
            naive_act += act

            sku_err[sku] = sku_err.get(sku, 0.0) + abs(act - model_pred)
            sku_act[sku] = sku_act.get(sku, 0.0) + act
            # Later (more recent) snapshots overwrite earlier ones for the same
            # week — files are processed oldest-first, so the last write is the
            # freshest prediction made for that week.
            sku_week_pred.setdefault(sku, {})[week1_start.isoformat()] = model_pred

    model_pct = max(0, min(100, round((1 - model_err / model_act) * 100))) if model_act > 0 else 0
    naive_pct = max(0, min(100, round((1 - naive_err / naive_act) * 100))) if naive_act > 0 else 0
    sku_accuracy = {
        sku: max(0, min(100, round((1 - err / sku_act[sku]) * 100)))
        for sku, err in sku_err.items()
        if sku_act[sku] > 0
    }
    return model_pct, naive_pct, used, sku_accuracy, sku_week_pred


def _adjusted_weekly(snap_date: date, lgbm_weekly: list[float] | None,
                     naive_week: float) -> list[float]:
    """The next ``_FC_WEEKS`` weeks of demand for one SKU: LightGBM's weekly
    forecast blended with the seasonal-naive weekly run-rate (``FORECAST_BLEND``),
    with the India festival / sale-season uplift applied. This single series is
    the source of truth for the headline horizons (``forecast7/10/35`` = its
    1/≈1.4/5-week sums) and the drill-down chart, so the numbers reconcile."""
    series: list[float] = []
    for w in range(_FC_WEEKS):
        ws = _forecast_week_start(snap_date, w)
        if lgbm_weekly is not None and w < len(lgbm_weekly):
            base = FORECAST_BLEND * lgbm_weekly[w] + (1 - FORECAST_BLEND) * naive_week
        else:
            base = naive_week
        series.append(max(0.0, base * _week_festival(ws)[0]))
    return series


def _load_real_plan(df, forecasts: dict | None = None) -> tuple[
    list[PlanRow], dict[str, dict[str, int]], dict[str, list[float]], dict[str, float],
    list["TopRegion"], list["TopRegion"], list["TopWarehouse"],
]:
    """Build the SKU production plan + top-selling tables from the merged order
    history. The model's weekly forecast ({sku: {"weekly": [...]}}) is blended with
    each SKU's run-rate and the festival uplift into one weekly series
    (``_adjusted_weekly``); ``forecast7/10/35`` are its 1/≈2/5-week sums (the same
    series the drill-down shows). ``inventoryQty`` = PENDING_QTY_PIECES, ``wipQty``
    = TOTAL_WIP_QTY, ``availableQty`` = inventory + WIP. Top tables = gross revenue
    + units per state/city/warehouse over 10/30/90 days (junk names dropped). Sets
    module-level ``SNAPSHOT_DATE`` and ``OVERALL_ACCURACY``."""
    global SNAPSHOT_DATE, OVERALL_ACCURACY, SKU_ACCURACY, SKU_MODEL_HIST
    import pandas as pd

    df = df.dropna(subset=["product_sku_code"])
    # Match the LightGBM path (data_processing.clean_data) so the naive baseline
    # and the model count the SAME rows: drop exact-duplicate order lines (the
    # source has no order_id) and any future-dated orders.
    df = df.drop_duplicates()
    df = df[df["order_date"] <= pd.Timestamp.today().normalize()]

    snap = pd.Timestamp(df["order_date"].max()).normalize()
    SNAPSHOT_DATE = snap.date()
    sales = df[df["order_status"].isin(_SOLD_STATUSES)]
    # Return-status rows (for lifecycle return-rate); separate from sold demand.
    returns = df[df["order_status"].astype(str).str.contains("return", case=False, na=False)]

    def window_sum(days: int):
        lo = snap - pd.Timedelta(days=days - 1)
        return sales[sales["order_date"] >= lo].groupby("product_sku_code")["qty"].sum()

    last10 = window_sum(10)
    last35 = window_sum(35)
    wip = df.groupby("product_sku_code")["TOTAL_WIP_QTY"].max()
    # PENDING_QTY_PIECES is the on-hand inventory (denormalised per row -> max).
    inventory_by_sku = df.groupby("product_sku_code")["PENDING_QTY_PIECES"].max()
    design = df.groupby("product_sku_code")["DESIGN_NO"].first()
    # Earliest launch date per SKU -> drives days-since-launch / launch tier.
    if "LAUNCH_DATE" in df.columns:
        launched = pd.to_datetime(df["LAUNCH_DATE"], errors="coerce").groupby(
            df["product_sku_code"]).min()
    else:
        launched = pd.Series(dtype="datetime64[ns]")

    # Per-SKU WEEKLY actual sales (Mon-anchored) for the drill-down history.
    last_monday = pd.Timestamp(snap).normalize() - pd.Timedelta(days=pd.Timestamp(snap).weekday())
    wkrec = sales[sales["order_date"] >= last_monday - pd.Timedelta(weeks=_HIST_WEEKS)].copy()
    # Monday week-start (matches the historical loop + _forecast_week_start). NOTE:
    # to_period("W-MON").start_time would give TUESDAY (W-MON = weeks ENDING Monday).
    wkrec["_wk"] = wkrec["order_date"] - pd.to_timedelta(wkrec["order_date"].dt.weekday, unit="D")
    wk_g = wkrec.groupby(["product_sku_code", "_wk"])["qty"].sum()
    week_actual: dict[str, dict[str, int]] = {}
    for (sku, wkts), q in wk_g.items():
        week_actual.setdefault(str(sku), {})[wkts.date().isoformat()] = int(q)

    iso = SNAPSHOT_DATE.isoformat()
    rows: list[PlanRow] = []
    fc_weekly_adj: dict[str, list[float]] = {}
    naive_week_map: dict[str, float] = {}
    for sku in df["product_sku_code"].unique():
        skey = str(sku)
        naive35 = int(last35.get(sku, 0))
        naive_week = naive35 / 5.0          # avg weekly run-rate (last 5 weeks ≈ 35 days)
        fc = forecasts.get(skey) if forecasts else None
        lgbm_weekly = fc.get("weekly") if fc else None
        # One festival-adjusted, LGBM+run-rate-blended WEEKLY series drives BOTH the
        # headline horizons and the drill-down, so they reconcile and production
        # responds to festival / sale spikes.
        wseries = _adjusted_weekly(SNAPSHOT_DATE, lgbm_weekly, naive_week)
        f7 = round(wseries[0])                       # week 1 ≈ 7 days
        f10 = round(wseries[0] + wseries[1] * (3 / 7))  # 1 week + 3 days
        f35 = round(sum(wseries[:5]))               # 5 weeks ≈ 35 days
        fc_weekly_adj[skey] = [round(x, 3) for x in wseries]
        naive_week_map[skey] = naive_week
        hist10 = int(last10.get(sku, 0))
        w = wip.get(sku)
        wip_qty = 0 if pd.isna(w) else int(round(float(w)))
        inv = inventory_by_sku.get(sku)
        inventory = 0 if pd.isna(inv) else int(round(float(inv)))
        available = inventory + wip_qty
        dn = design.get(sku)
        # (s, S) inventory policy: produce when on-hand + WIP falls below the
        # reorder point (lead-time demand + safety stock), up to a target that also
        # covers the weekly review cycle, rounded up to the MOQ. The lead time is
        # the real per-design lot lead from the delay model (else the default).
        lead = _lead_days(None if pd.isna(dn) else dn)
        drr = f35 / 35.0                                   # units/day (model run-rate)
        acts = list(week_actual.get(skey, {}).values())
        sigma_w = float(pd.Series(acts, dtype="float64").std()) if len(acts) >= 2 else 0.0
        safety = max(0, round(SERVICE_Z * sigma_w * (lead / 7.0) ** 0.5))
        reorder = round(drr * lead + safety)
        order_up_to = round(drr * (lead + REVIEW_DAYS) + safety)
        if available <= reorder and order_up_to > available:
            raw = order_up_to - available
            calculated = int(-(-raw // PRODUCTION_MOQ) * PRODUCTION_MOQ) if PRODUCTION_MOQ > 0 else raw
        else:
            calculated = 0
        rows.append(
            PlanRow(
                skuCode=str(sku),
                designNo="" if pd.isna(dn) else str(dn),
                date=iso,
                forecast7=f7,
                forecast10=f10,
                forecast35=f35,
                inventoryQty=inventory,
                wipQty=wip_qty,
                availableQty=available,
                leadTimeDays=int(round(lead)),
                safetyStock=safety,
                reorderPoint=reorder,
                totalSuggestedProduction=order_up_to,
                calculatedProductionSuggestion=calculated,
                stockStatus="In Stock" if available > reorder else "Reorder",
                historicalLast10d=hist10,
            )
        )
    rows.sort(key=lambda r: r.forecast35, reverse=True)
    lifecycle.classify(rows, sales, returns, launched, SNAPSHOT_DATE)

    # --- overall MODEL accuracy: genuine walk-forward backtest ---------------- #
    # This used to compare the seasonal-naive baseline against itself (naive_week
    # projected backward vs actual) — it never touched the LightGBM-blended
    # forecast at all, so "Model Accuracy" on the dashboard could never move no
    # matter how much the model improved. Score the real cached model output
    # instead; fall back to the old naive-only proxy only if no historical
    # snapshot is old enough yet to score (e.g. a brand-new install).
    model_pct, naive_pct, n_snapshots, sku_accuracy, sku_week_pred = _backtest_model_accuracy(sales, snap)
    if n_snapshots > 0:
        OVERALL_ACCURACY = model_pct
        SKU_ACCURACY = sku_accuracy
        SKU_MODEL_HIST = sku_week_pred
        print(
            f"[data] backtest accuracy over {n_snapshots} snapshot(s): "
            f"model={model_pct}% naive={naive_pct}% ({len(sku_accuracy)} SKUs scored)",
            file=sys.stderr,
        )
    else:
        # Volume-weighted (WAPE) naive-only proxy — the only thing computable
        # before any forecast snapshot is old enough to score against reality.
        hist_weeks = [last_monday.date() - timedelta(days=7 * k) for k in range(_HIST_WEEKS, 0, -1)]
        hist_mult = {ws.isoformat(): _week_festival(ws)[0] for ws in hist_weeks}
        abs_err = tot_act = 0.0
        for skey, wkmap in week_actual.items():
            nw = naive_week_map.get(skey, 0.0)
            sku_act = sum(wkmap.get(ws.isoformat(), 0) for ws in hist_weeks)
            sku_exp = sum(max(0, round(nw * hist_mult[ws.isoformat()])) for ws in hist_weeks)
            abs_err += abs(sku_act - sku_exp)
            tot_act += sku_act
        OVERALL_ACCURACY = max(0, min(100, round((1 - abs_err / tot_act) * 100))) if tot_act > 0 else 0
        SKU_ACCURACY = {}
        SKU_MODEL_HIST = {}
        print("[data] no scoreable snapshot yet — using naive-only proxy accuracy", file=sys.stderr)

    # --- top-selling state / city / warehouse (real revenue + quantity) ------ #
    junk = {"", "unknown", "nan", "na", "null", "none", "n/a", "-"}

    def norm_place(s) -> str | None:
        """Clean a state / city name: collapse whitespace, drop a trailing
        pincode-like number ('Chennai 600081' -> 'Chennai'), reject placeholders."""
        if pd.isna(s):
            return None
        t = re.sub(r"\s+", " ", str(s).strip())
        t = re.sub(r"\s+\d+$", "", t)  # strip a trailing standalone number
        if t.lower() in junk or not any(c.isalpha() for c in t):
            return None
        return t.title()

    def norm_warehouse(s) -> str | None:
        """Keep real FC names / codes (BLR8, DEL5, Malur BTS) but drop blanks,
        numeric-only values and the 'DEFAULT …' unassigned catch-all bucket."""
        if pd.isna(s):
            return None
        t = re.sub(r"\s+", " ", str(s).strip())
        tl = t.lower()
        if tl in junk or tl.startswith("default") or not any(c.isalpha() for c in t):
            return None
        return t

    geo = sales.assign(
        _state=sales["buyer_state"].map(norm_place),
        _city=sales["buyer_city"].map(norm_place),
        _warehouse=sales["warehouse_name"].map(norm_warehouse),
    )

    def top_agg(col: str) -> list[dict]:
        g = geo.dropna(subset=[col])
        acc: dict[str, dict] = {}
        for days in _PERIODS:
            lo = snap - pd.Timedelta(days=days - 1)
            w = g[g["order_date"] >= lo]
            rev = w.groupby(col)["total"].sum()
            units = w.groupby(col)["qty"].sum()
            for name in rev.index:
                a = acc.setdefault(name, {})
                a[f"revenue{days}"] = int(round(float(rev[name])))
                a[f"units{days}"] = int(units[name])
        out = [
            {"name": name,
             **{f"{m}{p}": a.get(f"{m}{p}", 0) for m in ("revenue", "units") for p in _PERIODS}}
            for name, a in acc.items()
        ]
        out.sort(key=lambda r: r["units30"], reverse=True)
        return out[:100]  # cap the long noisy tail (buyer_city has ~9k variants)

    top_states = [TopRegion(**d) for d in top_agg("_state")]
    top_cities = [TopRegion(**d) for d in top_agg("_city")]
    top_warehouses = [TopWarehouse(**d) for d in top_agg("_warehouse")]

    return (rows, week_actual, fc_weekly_adj, naive_week_map,
            top_states, top_cities, top_warehouses)


# --------------------------------------------------------------------------- #
# Mock top-selling aggregates — only used as a fallback if the real CSV load
# fails. Each row carries revenue + sale quantity for the last 10 / 30 / 90 days.
# --------------------------------------------------------------------------- #
def _aggregate(key: str) -> list[dict]:
    agg: dict[str, dict] = {}
    for f in _FACTS:
        a = agg.setdefault(
            f[key],
            {f"{m}{p}": 0 for m in ("units", "revenue") for p in _PERIODS},
        )
        for p in _PERIODS:
            a[f"units{p}"] += f[f"units{p}"]
            a[f"revenue{p}"] += f[f"revenue{p}"]
    rows = sorted(agg.items(), key=lambda kv: kv[1]["units30"], reverse=True)
    return [{"name": name, **a} for name, a in rows]


# Mutable module state, (re)populated by rebuild().
_MOCK_ROWS: list[PlanRow]
_FACTS: list[dict]
_MOCK_ROWS, _FACTS = _build_plan()

PLAN_ROWS: list[PlanRow] = []
# Per-SKU WEEKLY actual sales {week_start_iso: qty} over the last _HIST_WEEKS weeks.
_WEEK_ACTUAL: dict[str, dict[str, int]] = {}
# Per-SKU festival-adjusted, blended WEEKLY forecast (next _FC_WEEKS weeks) — the
# single series behind BOTH the headline horizons (forecast7/10/35) and the
# drill-down chart, so they always reconcile.
_FC_WEEKLY: dict[str, list[float]] = {}
# Per-SKU seasonal-naive WEEKLY level (5-week run-rate); the drill-down back-test
# baseline and the fallback when LightGBM doesn't cover a SKU.
_NAIVE_WEEK: dict[str, float] = {}
# Overall forecast accuracy (0-100), volume-weighted WAPE over recent weeks.
OVERALL_ACCURACY: int = 0
# Per-SKU backtested accuracy (0-100) — same real-model walk-forward backtest as
# OVERALL_ACCURACY, just not pooled across SKUs. Only populated for SKUs that
# appeared in at least one scoreable cached snapshot (see _backtest_model_accuracy).
SKU_ACCURACY: dict[str, int] = {}
# Per-SKU real model predictions for past weeks that have actually been
# backtested: {sku: {week_start_iso: model_pred}}. get_breakdown() uses these
# in place of the naive baseline wherever a genuine backtested value exists.
SKU_MODEL_HIST: dict[str, dict[str, float]] = {}
TOP_STATES: list[TopRegion] = []
TOP_CITIES: list[TopRegion] = []
TOP_WAREHOUSES: list[TopWarehouse] = []
PLAN_BY_SKU: dict[str, PlanRow] = {}

# Forecast-model status: "naive" until LightGBM has been swapped in.
ACTIVE_FORECAST_MODEL = "naive"
_SOURCE_DF = None              # cached source frame for the background LGBM step
_LGBM_THREAD: threading.Thread | None = None
# Per-design replenishment lead times (days). Empty → _lead_days falls back to PROD_LEAD_DAYS.
_DESIGN_LEAD: dict[str, float] = {}
_GLOBAL_LEAD: float = 0.0
# The forecasts currently folded into the plan.
_ACTIVE_FORECASTS: dict | None = None
# Design → Section lookup (Top / Bottom / Dupatta) from Master view.
_DESIGN_SECTION: dict[str, str] = {}

def _lead_days(design) -> float:
    """Replenishment lead time for a design: the delay model's real median lot
    lead time when known, else the observed-global / configured default.
    Always returns >= 1 so estimated delivery is never before the issue date."""
    if design is not None and str(design) in _DESIGN_LEAD:
        return max(1.0, _DESIGN_LEAD[str(design)])
    v = _GLOBAL_LEAD or PROD_LEAD_DAYS
    return max(1.0, v)


def _set_plan(result) -> None:
    """Atomically publish a freshly built plan + tables to the module globals."""
    global PLAN_ROWS, _WEEK_ACTUAL, _FC_WEEKLY, _NAIVE_WEEK
    global TOP_STATES, TOP_CITIES, TOP_WAREHOUSES, PLAN_BY_SKU
    rows, week_actual, fc_weekly, naive_week, states, cities, warehouses = result
    PLAN_ROWS, _WEEK_ACTUAL, _FC_WEEKLY, _NAIVE_WEEK = rows, week_actual, fc_weekly, naive_week
    TOP_STATES, TOP_CITIES, TOP_WAREHOUSES = states, cities, warehouses
    PLAN_BY_SKU = {r.skuCode: r for r in rows}


def _upgrade_to_lgbm() -> None:
    """Train LightGBM (or load the cached forecasts) and swap it into the plan.

    Runs in a background thread so the seasonal-naive plan is served immediately.
    On any failure the naive plan is left in place.
    """
    global ACTIVE_FORECAST_MODEL, _ACTIVE_FORECASTS
    try:
        import lgbm_forecast
        snap = SNAPSHOT_DATE.isoformat()
        forecasts = lgbm_forecast.load_cache(snap)
        if forecasts is None:
            print("[data] training XGBoost in background…", file=sys.stderr)
            forecasts = lgbm_forecast.compute(_SOURCE_DF)
            if forecasts:
                lgbm_forecast.save_cache(snap, forecasts)
        if not forecasts:
            print("[data] XGBoost produced no forecasts; keeping naive", file=sys.stderr)
            return
        # _load_real_plan folds the model's weekly series into the published plan
        # (headline horizons + drill-down chart both come from it).
        _ACTIVE_FORECASTS = forecasts
        _set_plan(_load_real_plan(_SOURCE_DF, forecasts=forecasts))
        ACTIVE_FORECAST_MODEL = "xgboost"
        print(f"[data] XGBoost active ({len(forecasts):,} SKUs)", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — never let forecasting kill the API
        print(f"[data] XGBoost upgrade failed ({exc!r}); keeping naive", file=sys.stderr)


_BOTTOM_KEYWORDS = frozenset({"PANT", "PLAZZO", "PALAZZO", "SHARARA", "LEHENGA", "DHOTI", "SALWAR", "SLAWAR", "CHURIDAR", "LEGGING"})
_TOP_KEYWORDS    = frozenset({"BLOUSE", "SHRUGE", "SHRUG", "KURTI", "KURTA", "KAMEEZ", "TOP"})


def _design_section(design: str) -> str:
    """Return section ('Top'/'Bottom'/'Dupatta') for a design string.

    Priority:
    1. Exact key match in _DESIGN_SECTION (master view)
    2. 'DUPATTA' anywhere in the name
    3. Last '-' token matched against known garment-type keywords
    """
    d = str(design).strip()
    sec = _DESIGN_SECTION.get(d, "")
    if sec:
        return sec
    u = d.upper()
    if "DUPATTA" in u:
        return "Dupatta"
    # Extract the last segment after the final hyphen for garment-type matching
    suffix = u.rsplit("-", 1)[-1] if "-" in u else u
    if suffix in _BOTTOM_KEYWORDS:
        return "Bottom"
    if suffix in _TOP_KEYWORDS:
        return "Top"
    return ""




def get_inventory_planning(limit: int = 500) -> dict:
    """Inventory planning: current stock per design+size and days to finish based on actual sales DRR."""
    try:
        if not PLAN_ROWS:
            return {"available": False, "items": [], "total": 0}

        plan_by_key: dict[tuple[str, str], dict] = {}
        for r in PLAN_ROWS:
            design = r.designNo or r.skuCode
            sku = str(r.skuCode)
            parts = sku.replace(design, "", 1).lstrip("-")
            size = parts.split("-")[0] if parts else ""
            key = (design, size)
            if key not in plan_by_key:
                plan_by_key[key] = {"inventoryQty": 0, "actualLast10d": 0}
            p = plan_by_key[key]
            p["inventoryQty"]  += r.inventoryQty
            p["actualLast10d"] += r.historicalLast10d

        records = []
        for (design, size), p in plan_by_key.items():
            stock = p["inventoryQty"]
            actual_10d = p["actualLast10d"]
            daily_rate = round(actual_10d / 10.0, 1)
            days_to_finish = round(stock / daily_rate) if daily_rate > 0 else 0

            records.append({
                "design":        design,
                "size":          size,
                "currentStock":  stock,
                "dailyRunRate":  daily_rate,
                "daysToFinish":  days_to_finish,
            })

        records.sort(key=lambda r: r["daysToFinish"] if r["daysToFinish"] > 0 else 99999)

        return {
            "available": True,
            "total": len(records),
            "items": records[:limit],
        }
    except Exception as exc:
        print(f"[data] get_inventory_planning failed ({exc!r})", file=sys.stderr)
        return {"available": False, "items": [], "total": 0}


def get_job_work(limit: int = 500) -> dict:
    """Removed — Production Delay table has been disabled."""
    return {"available": False, "items": [], "total": 0, "over30": 0}


def get_delays(limit: int = 100, risk: str | None = None) -> dict:
    """Removed — delay prediction via JW view has been disabled."""
    return {"available": False, "items": [], "total": 0}


def rebuild() -> dict:
    """Fetch the source data and rebuild every in-memory table.

    Serves the seasonal-naive plan immediately; if ``FORECAST_MODEL='lgbm'`` it
    then trains LightGBM in the background and swaps it in. Called once at import
    and again by ``POST /admin/refresh``. Falls back to mock data on failure.
    """
    global _SOURCE_DF, ACTIVE_FORECAST_MODEL, _LGBM_THREAD
    try:
        df = _source_dataframe()
        _SOURCE_DF = df
        _set_plan(_load_real_plan(df))
        source = "live" if DATA_SOURCE == "live" else "csv"
        ACTIVE_FORECAST_MODEL = "naive"
        if FORECAST_MODEL == "lgbm":
            _LGBM_THREAD = threading.Thread(target=_upgrade_to_lgbm, daemon=True)
            _LGBM_THREAD.start()
    except Exception as exc:  # noqa: BLE001 — never let data issues kill startup
        print(f"[data] real load failed ({exc!r}); falling back to mock", file=sys.stderr)
        _set_plan((
            _MOCK_ROWS, {}, {}, {},
            [TopRegion(**d) for d in _aggregate("state")],
            [TopRegion(**d) for d in _aggregate("city")],
            [TopWarehouse(**d) for d in _aggregate("warehouse")],
        ))
        source = "mock"

    return {
        "source": source,
        "rows": len(PLAN_ROWS),
        "snapshot": SNAPSHOT_DATE.isoformat(),
        "forecastModel": ACTIVE_FORECAST_MODEL,
        "lgbmPending": FORECAST_MODEL == "lgbm" and ACTIVE_FORECAST_MODEL != "xgboost",
    }


def _seconds_until(hour: int, minute: int) -> float:
    """Seconds from now until the next local HH:MM (today if still ahead, else tomorrow)."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _daily_refresh_loop() -> None:
    """Sleep until REFRESH_HOUR:REFRESH_MINUTE each day, then re-fetch live data."""
    while True:
        time.sleep(_seconds_until(REFRESH_HOUR, REFRESH_MINUTE))
        try:
            print(f"[data] daily auto-refresh ({REFRESH_HOUR:02d}:{REFRESH_MINUTE:02d})…", file=sys.stderr)
            res = rebuild()
            print(f"[data] daily auto-refresh done: {res}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — never let the scheduler die
            print(f"[data] daily auto-refresh failed ({exc!r})", file=sys.stderr)
            time.sleep(300)  # brief back-off, then wait for the next day


def start_daily_refresh() -> None:
    """Launch the once-a-day live-refresh thread (idempotent)."""
    global _REFRESH_THREAD
    if not DAILY_REFRESH or (_REFRESH_THREAD and _REFRESH_THREAD.is_alive()):
        return
    _REFRESH_THREAD = threading.Thread(target=_daily_refresh_loop, daemon=True)
    _REFRESH_THREAD.start()
    print(f"[data] daily auto-refresh scheduled for {REFRESH_HOUR:02d}:{REFRESH_MINUTE:02d} local", file=sys.stderr)


_REFRESH_THREAD: threading.Thread | None = None

# Build once at import, then schedule the daily live refresh.
rebuild()
start_daily_refresh()


def get_plan(
    search: str | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[PlanRow]]:
    """Filtered + paginated plan rows; returns (total_matching, page_items)."""
    rows = PLAN_ROWS
    if search:
        s = search.strip().lower()
        rows = [r for r in rows if s in r.skuCode.lower() or s in r.designNo.lower()]
    if status:
        rows = [r for r in rows if r.stockStatus.lower() == status.strip().lower()]
    total = len(rows)
    return total, rows[offset : offset + limit]


def get_new_design_count(max_age_days: int = 90) -> int:
    """Distinct designs (not SKUs — one design has many size variants) launched
    within the last ``max_age_days`` days. Computed against the FULL PLAN_ROWS,
    not a paginated page, since the dashboard's default page only fetches the
    top 1000-by-forecast rows and would otherwise undercount newly-launched
    designs (which often have low/uncertain forecasts and sort toward the
    bottom)."""
    return len({
        r.designNo for r in PLAN_ROWS
        if 0 <= r.daysSinceLaunch <= max_age_days
    })


def get_new_designs(max_age_days: int = 90) -> list[dict]:
    """One row per distinct design launched within the last ``max_age_days``
    days (not one row per SKU), newest first. Computed against the FULL
    PLAN_ROWS for the same reason as ``get_new_design_count``."""
    by_design: dict[str, dict] = {}
    for r in PLAN_ROWS:
        if not (0 <= r.daysSinceLaunch <= max_age_days):
            continue
        d = by_design.get(r.designNo)
        if d is None:
            by_design[r.designNo] = {
                "designNo": r.designNo,
                "launchDate": r.launchDate,
                "daysSinceLaunch": r.daysSinceLaunch,
                "skuCount": 1,
            }
        else:
            d["skuCount"] += 1
    return sorted(by_design.values(), key=lambda d: d["daysSinceLaunch"])


def get_breakdown(sku: str, weeks: int) -> BreakdownResponse | None:
    """Week-wise forecast vs actual: the last ``_HIST_WEEKS`` weeks (actual vs
    the real backtested model prediction where available, else the naive
    baseline) plus the next ``weeks`` weeks of forecast.

    The future forecast is the SAME festival-adjusted, LightGBM+run-rate-blended
    WEEKLY series the plan's headline horizons are summed from, so the chart and
    the headline reconcile (and both reflect festival / sale spikes). Each point's
    date is the Monday start of its week."""
    row = PLAN_BY_SKU.get(sku)
    if row is None:
        return None

    naive_week = _NAIVE_WEEK.get(sku, row.forecast35 / 5)
    wk_actual = _WEEK_ACTUAL.get(sku, {})
    series = _FC_WEEKLY.get(sku)  # festival-adjusted blended weekly forecast
    model_hist = SKU_MODEL_HIST.get(sku, {})  # real backtested model predictions, by week

    # --- future forecast (weekly) ------------------------------------------- #
    forecast: list[ForecastPoint] = []
    for w in range(weeks):
        ws = _forecast_week_start(SNAPSHOT_DATE, w)
        mult, event = _week_festival(ws)
        qty = max(0, round(series[w])) if (series is not None and w < len(series)) \
            else max(0, round(naive_week * mult))
        forecast.append(ForecastPoint(date=ws.isoformat(), qty=qty, event=event))

    # --- historical: real weekly units vs what was actually predicted -------- #
    # Wherever a genuine backtested model prediction exists for a week (recent
    # weeks only — see SKU_MODEL_HIST), show that instead of the naive
    # baseline, so this chart/table reflects the real deployed model rather
    # than a naive-run-rate proxy.
    last_monday = SNAPSHOT_DATE - timedelta(days=SNAPSHOT_DATE.weekday())
    historical: list[HistoricalPoint] = []
    # range(..., -1, -1) includes k=0: the snapshot's own week, still in
    # progress as of SNAPSHOT_DATE. Without it, the chart jumps straight from
    # the last COMPLETE week to next week's forecast, skipping "now" entirely
    # (e.g. snapshot Thu Jul 16 would show Jul 6 as the latest point, then
    # nothing until Jul 20) even though this week's partial actuals already
    # exist in wk_actual.
    for k in range(_HIST_WEEKS, -1, -1):
        ws = last_monday - timedelta(days=7 * k)
        actual = int(wk_actual.get(ws.isoformat(), 0))
        if ws.isoformat() in model_hist:
            exp = max(0, round(model_hist[ws.isoformat()]))
        else:
            mult, _ = _week_festival(ws)
            exp = max(0, round(naive_week * mult))
        historical.append(
            HistoricalPoint(
                date=ws.isoformat(), forecast=exp, actual=actual, variance=actual - exp,
                partial=(k == 0),
            )
        )

    # Per-SKU accuracy: prefer the real backtested model accuracy (same
    # walk-forward backtest as overallAccuracyPct, just not pooled across
    # SKUs); fall back to a naive-baseline MAPE proxy only for SKUs that never
    # appeared in a scoreable snapshot (e.g. a brand-new SKU).
    if sku in SKU_ACCURACY:
        accuracy = SKU_ACCURACY[sku]
    else:
        used = [p for p in historical if p.actual > 0]
        mape = sum(abs(p.actual - p.forecast) / p.actual for p in used) / len(used) if used else 1.0
        accuracy = max(0, min(100, round((1 - mape) * 100)))

    return BreakdownResponse(
        sku=sku,
        snapshotDate=SNAPSHOT_DATE.isoformat(),
        periodWeeks=weeks,
        accuracyPct=accuracy,
        overallAccuracyPct=OVERALL_ACCURACY,
        historical=historical,
        forecast=forecast,
    )


def get_all_breakdowns(
    weeks: int, offset: int, limit: int
) -> tuple[int, list[BreakdownResponse]]:
    """Week-wise breakdown for a page of SKUs. Returns (total_skus, page_items)."""
    page = PLAN_ROWS[offset : offset + limit]
    items = [b for r in page if (b := get_breakdown(r.skuCode, weeks)) is not None]
    return len(PLAN_ROWS), items
