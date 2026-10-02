
from __future__ import annotations

import calendar
import os
import random
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import design_attributes
import festival
import festival_calendar
import lifecycle
from models import (
    ChannelSourceWeeklyResponse,
    ChannelSourceWeeklySeries,
    FestivalColorSales,
    FestivalOutlook,
    FestivalSubCategorySales,
    ForecastPoint,
    HistoricalPoint,
    PlanRow,
    TopRegion,
    TopWarehouse,
    UpcomingEventOutlook,
    WeeklyGridCell,
    WeeklyGridMonth,
    WeeklyGridResponse,
    WeeklyGridRow,
    WeeklyGridTotals,
    WeeklyGridEvent,
    WeeklyGridWeek,
)

# Snapshot ("data as of") date. Set dynamically to the latest order_date in the
# loaded data; this default is only used by the mock fallback.
SNAPSHOT_DATE = date(2026, 6, 8)
NUM_SKUS = 1000

# Data source: "live" fetches fresh from BigQuery + ERP (see live_source.py),
# "csv" reads the local snapshot. Live falls back to the CSV on any failure.
DATA_SOURCE = os.getenv("DATA_SOURCE", "live").strip().lower()
# "lgbm" serves the saved model forecasts (this snapshot's, else the newest
# previous one) and trains any missing ones in the background; "naive" uses
# the baseline only.
FORECAST_MODEL = os.getenv("FORECAST_MODEL", "lgbm").strip().lower()
# Final weekly forecast = FORECAST_BLEND * model + (1 - blend) * SKU run-rate,
# anchoring the global model to each SKU's own level. 1.0 = pure model, 0 = naive.
# Verified 2026-08-19 via api/backtest_sweep.py (real walk-forward backtest
# against cached forecasts + actual sales, not a guess): swept blend in
# {0, 0.25, 0.5, 0.75, 1.0} x {festival multiplier on, off}. 0.25 beat the old
# 0.5 default (41% vs 39% model accuracy, festival-on), and 1.0 (pure model)
# was clearly worse than 0.0 (naive-only) at 32% vs 40% -- the model alone
# was NOT yet beating the naive run-rate on this dataset. CAVEAT: only 2
# snapshots were scoreable (thin sample) and this evidence is from the
# PRE-FIX (v17) cached forecasts -- the lifecycle-audit fixes landing
# alongside this change (point-in-time design_level, rolling-origin CV,
# error-weighted blend, recency weighting) target exactly the kind of
# leakage-inflated-but-poorly-generalizing model this result would produce,
# so this should be RE-SWEPT once v18 has accumulated a few weeks of fresh
# backtested snapshots -- don't treat 0.25 as final.
FORECAST_BLEND = float(os.getenv("FORECAST_BLEND", "0.25"))
# Daily auto-refresh: re-fetch live data once a day at this local time. Set
# DAILY_REFRESH=off to disable. Default 06:00 (before business hours).
DAILY_REFRESH = os.getenv("DAILY_REFRESH", "on").strip().lower() != "off"
REFRESH_HOUR = int(os.getenv("REFRESH_HOUR", "6"))
REFRESH_MINUTE = int(os.getenv("REFRESH_MINUTE", "0"))
# SKU/design walk-forward backtest - OFF by default (2026-09-24,
# user-requested: do not run this automatically on any rebuild - daily
# refresh, manual refresh, or retrain - until explicitly asked to turn it
# back on again; don't re-enable it unilaterally, e.g. as a side effect of
# some other change, without that explicit ask). Set RUN_BACKTEST=1 to
# enable. While off, OVERALL_ACCURACY/SKU_ACCURACY/DESIGN_ACCURACY
# etc. fall back to the same "no scoreable snapshot yet"
# naive-only proxy path already used before any snapshot is old enough to
# score.
RUN_BACKTEST = os.getenv("RUN_BACKTEST", "0").strip().lower() in ("1", "true", "on")
# Replenishment lead time = how long a new production lot takes; the default is
# the empirically-observed median (~12 weeks), overridden per-design by the
# delay model's real lot lead times when available (see _lead_days). Lead time
# and safety stock are informational only (see _load_real_plan's 10-week policy).
PROD_LEAD_DAYS = int(os.getenv("PROD_LEAD_DAYS", "84"))
SERVICE_Z = float(os.getenv("SERVICE_Z", "1.65"))        # 1.65 ≈ 95% service level
# Local snapshot of the merged order history (fallback / DATA_SOURCE=csv).
DATA_FILE = Path(__file__).resolve().parent.parent / "final_merged_data.csv"
# Columns the plan + top tables need from the source frame.
_SOURCE_COLS = ["product_sku_code", "qty", "order_status", "order_date",
                "DESIGN_NO", "TOTAL_WIP_QTY", "PENDING_QTY_PIECES",
                "buyer_state", "buyer_city", "warehouse_name", "total"]
# Extra columns the daily demand-forecasting model needs (api/lgbm_forecast.py).
_FORECAST_COLS = ["listing_sku_code", "LAUNCH_DATE", "channel_name",
                   "category_name", "brand_name", "promo_discount"]
_FULL_COLS = _SOURCE_COLS + _FORECAST_COLS
# Order statuses that count as realized demand (exclude cancellations & returns
# — this catalog has a 30.7% return rate, so counting Return Received etc. as
# demand measurably hurt forecast accuracy; verified 2026-08-03, reverted from
# a denylist back to this allowlist for that reason). Matched case-
# insensitively against a lowercased order_status (matches lgbm_forecast.py's
# _SOLD_NORM — keep both sets in sync).
# 2026-08-22: added 5 in-flight statuses (manifested/out for delivery/picked
# up/reached at destination/delayed) that now appear in the live status
# vocabulary but aren't returns/cancellations — see lgbm_forecast.py's
# _SOLD_NORM comment for the full reasoning. RTO/reverse-logistics and
# outright cancellation statuses are deliberately still excluded (user-
# confirmed 2026-08-22: forecast stays net-of-returns — counting gross
# orders as demand was tried before and measurably hurt accuracy).
_SOLD_STATUSES = {
    "delivered", "shipped", "in transit", "ready to ship", "ready for pickup",
    "packed", "new", "processing", "pending",
    "manifested", "out for delivery", "picked up", "reached at destination", "delayed",
}
# Gross Sale (2026-09-15, user-specified explicit allowlist - supersedes the
# earlier "everything except Cancelled/Cancelled Before Shipping" denylist,
# then extended 2026-09-15 to also include Lost/Damaged/Misrouted/Out Of
# Delivery Area/Reached At Origin/Reverse Picked Up, which the user's first
# list had left out - but "Cancel Request Approved" (added in that same
# batch) was then explicitly EXCLUDED again: it means a cancellation was
# approved, i.e. this order IS being cancelled, same as Cancelled/Cancelled
# Before Shipping. Deliberately still NOT the same list as _SOLD_STATUSES
# above: this one includes returns/RTO/reverse-logistics statuses too (a
# returned order still WAS a sale before it came back - the standard Gross
# vs. Net distinction, Net = Gross - Returns). Excluded from Gross now:
# Cancelled, Cancelled Before Shipping, Cancel Request Approved - matched
# case-insensitively, keep in sync with lgbm_forecast.py's _GROSS_SALE_NORM.
_GROSS_SALE_STATUSES = {
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
# Weeks of RECENT real weekly sales used for the safety-stock demand-variance
# term (sigma_w) - deliberately a short window so a long lookback doesn't
# dilute that recent-volatility signal. NOT the drill-down history length
# (see _BREAKDOWN_HIST_START below) - that's a separate, much longer window.
_HIST_WEEKS = 8
# Earliest Monday the SKU drill-down's "Recent weeks: forecast vs actual"
# table shows (2026-09-11, user-requested: show the full history back to
# April 2025 instead of just the last _HIST_WEEKS).
_BREAKDOWN_HIST_START = date(2025, 4, 1)
# Weeks forecast ahead (matches lgbm_forecast.HORIZON_WEEKS: 13 weeks =~ 3
# months, 2026-09-12 - was 6/35-day-plan). forecast7/10/35 (the dashboard's
# headline horizons) still only sum the first 5 weeks of this series.
_FC_WEEKS = 13
# Sales windows (in days) reported by the top-selling tables.
_PERIODS = (10, 30, 90)
# Optimistic per-group festival forecast boost (see get_weekly_grid(),
# 2026-09-12, user-requested) - a group's own historical festival-week actual
# vs. its own pre-festival control-week actual must total at least this many
# units before its ratio is trusted (otherwise a couple of noisy units could
# imply a huge, meaningless "uplift"), and the resulting ratio is capped here
# regardless (matching the magnitude of festival.py's own hand-tuned peak
# multipliers, the largest of which is 2.3) so one outlier week can't blow
# the boosted forecast up unreasonably.
_MIN_FESTIVAL_CONTROL_ACTUAL = 5
_MAX_FESTIVAL_BOOST_RATIO = 4.0
# Spike lead-time detection (see _detect_spike_lead_days(), 2026-09-13,
# user-requested) - "quiet baseline" is the average daily sales 15-45 days
# before the historical event started (far enough back to predate any
# pre-event ramp), EXCLUDING any day that falls inside a DIFFERENT known
# festival_calendar event - Indian festivals/sales cluster densely enough
# (esp. Aug-Oct) that a naive fixed baseline window can land on a different
# event's own real spike (verified: 2025 Ganesh Chaturthi's naive -30..-15
# baseline included Raksha Bandhan's Aug 2 peak, inflating the "normal"
# level enough to mask Ganesh Chaturthi's own smaller, genuine ramp). A
# day's OWN trailing-3-day average must reach this many times that clean
# baseline to count as "the spike started here". Below _MIN_SPIKE_BASELINE_DAILY
# (units/day) or _MIN_SPIKE_BASELINE_DAYS (clean days found), the signal is
# too thin to trust at all.
_SPIKE_BASELINE_WINDOW = (45, 15)  # days-before-event-start: (far edge, near edge)
_SPIKE_LOOKBACK_DAYS = 50          # how far back the whole scan looks
_SPIKE_THRESHOLD_RATIO = 1.2
_MIN_SPIKE_BASELINE_DAILY = 3.0
_MIN_SPIKE_BASELINE_DAYS = 8
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
                price=price,
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


# 2026-08-22: raised from 21 -- caught in the act dropping real backtest
# history. A cluster of same-day cache-version bumps (several restarts in
# one session) pushed the file count from 19 to 23, past the old 21 cap;
# the 2 oldest files dropped turned out to be the ONLY snapshots covering
# the 2026-07-27 target week, silently cutting the backtest from 2 matured
# weeks down to 1 (reported accuracy dropped 41%->38%, not because the model
# got worse -- verified by replaying the exact selection logic against the
# actual cache files on disk). Raised again the same day, 90 -> 365
# (user-requested, "take a full year") -- the earliest cache file on disk is
# 2026-07-26, so this is a forward-looking ceiling only; it has zero effect
# today; it starts mattering once the app has run continuously for months.
# CAVEAT: the glob below pools snapshots across every model VERSION ever run
# (deliberately, so a version bump doesn't wipe out history -- see its own
# comment), so a full year will eventually mix in how much-older, since-
# superseded versions of the model performed, diluting the signal for "how
# good is TODAY's model" specifically. Worth remembering if a future
# accuracy number looks off relative to a recent change.
_BACKTEST_MAX_SNAPSHOTS = 365  # ~1 year of daily model-forecast snapshots
# Orders don't finalize on order_date - status updates (and the BigQuery sync
# itself) keep trickling in for days after a week calendar-ends (verified
# 2026-08-18: one SKU's "last week" actual grew 9 -> 28 -> 43 over two days).
# Scoring a week the instant it ends compares the forecast against a still-
# undercounted actual, unfairly dragging the reported accuracy down. Empirical
# check (scratch backtest re-run against live data): model accuracy rose from
# 34% at 0-day buffer to 39% at 3-7 days to 47% at 10-14 days, and only past
# ~7 days did the model clearly separate from the naive baseline.
MATURATION_DAYS = 7


def _backtest_model_accuracy(
    sales, snap: "pd.Timestamp"
) -> tuple[int, int, int, dict[str, int], dict[str, dict[str, float]], int, str]:
    """Genuine walk-forward accuracy for the deployed (LightGBM-blended)
    forecast, using each day's already-cached raw model output.

    This is the ONLY way to score the actual model rather than the naive
    baseline: a same-day metric can't, because the weeks a fresh forecast
    predicts haven't happened yet. Each day's daily-refresh already saves its
    raw weekly LGBM output to disk (lgbm_forecast.save_cache) — once enough
    time has passed for that snapshot's week-1 to actually occur, we can
    compare its prediction (re-blended exactly as it would have been shown)
    against what really happened.

    Returns (model_pct, naive_pct, snapshots_used, sku_accuracy, sku_week_pred,
    latest_week_pct, latest_week_iso):
      - model_pct/naive_pct/snapshots_used: pooled, volume-weighted across every
        matured week in the retention window (as before) — a multi-week
        blend, not "how did the model do most recently."
      - sku_accuracy: {sku: accuracy_pct} — same backtest, not pooled across SKUs.
      - sku_week_pred: {sku: {week_start_iso: model_pred}} — the model's actual
        historical prediction for weeks that were genuinely backtested (the
        most recent snapshot covering each week wins). Used by get_breakdown()
        so the drill-down's "historical forecast" is the real model, not the
        naive baseline, wherever a backtested value is available.
      - latest_week_pct: accuracy for ONLY the single most recently matured
        week (2026-08-22, user-requested — "show it for the already-completed
        week" instead of only the multi-week pooled figure). "" / 0 if no
        week has matured yet.
      - latest_week_iso: ISO date of that week's Monday, for labeling ("week
        of 2026-08-03" etc.) — "" if latest_week_pct wasn't computed.
    """
    import json as _json
    import pandas as pd

    global HORIZON_ACCURACY

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

    sku_week_pred: dict[str, dict[str, float]] = {}
    # Keyed by (sku, week1_start_iso) — a later (more recent) snapshot's
    # prediction overwrites an earlier one for the SAME target week, so each
    # week is scored exactly once no matter how many snapshots happened to
    # predict it. Without this, any gap in the daily cadence (e.g. the
    # process being down for a stretch) leaves several snapshots all
    # predicting the same not-yet-happened week; once that week finally
    # elapses, it got pooled in once per snapshot — silently outweighing
    # every other, more distinct week in the backtest (verified 2026-08-17:
    # a 9-day outage left 4 of 6 "snapshots" all pointing at one volatile
    # pre-festival week, which alone was dragging the whole pooled score down).
    week_data: dict[tuple[str, str], dict[str, float]] = {}
    used_weeks: set[str] = set()
    for f in files:
        try:
            snap_date = date.fromisoformat(f.stem.rsplit("_", 1)[-1])
        except ValueError:
            continue

        week1_start = _forecast_week_start(snap_date, 0)
        week1_end = week1_start + timedelta(days=6)
        if pd.Timestamp(week1_end) + timedelta(days=MATURATION_DAYS) >= snap:
            continue  # week hasn't calendar-ended, or its actuals haven't matured yet

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
        week_iso = week1_start.isoformat()
        used_weeks.add(week_iso)

        for sku, fc in forecasts.items():
            lgbm_weekly = fc.get("weekly") if fc else None
            if not lgbm_weekly:
                continue
            nw  = float(naive_week.get(sku, 0.0))
            act = float(actual.get(sku, 0.0))
            model_pred = max(0.0, (FORECAST_BLEND * lgbm_weekly[0] + (1 - FORECAST_BLEND) * nw) * mult)
            naive_pred = max(0.0, nw * mult)

            # Later (more recent) snapshots overwrite earlier ones for the
            # same (sku, week) — files are processed oldest-first, so this
            # keeps only the freshest prediction made for that week.
            week_data[(sku, week_iso)] = {
                "model_pred": model_pred, "naive_pred": naive_pred, "actual": act,
            }
            sku_week_pred.setdefault(sku, {})[week_iso] = model_pred

    model_err = model_act = naive_err = naive_act = 0.0
    sku_err: dict[str, float] = {}
    sku_act: dict[str, float] = {}
    for (sku, _week_iso), d in week_data.items():
        act, model_pred, naive_pred = d["actual"], d["model_pred"], d["naive_pred"]
        model_err += abs(act - model_pred)
        naive_err += abs(act - naive_pred)
        model_act += act
        naive_act += act
        sku_err[sku] = sku_err.get(sku, 0.0) + abs(act - model_pred)
        sku_act[sku] = sku_act.get(sku, 0.0) + act

    used = len(used_weeks)
    model_pct = max(0, min(100, round((1 - model_err / model_act) * 100))) if model_act > 0 else 0
    naive_pct = max(0, min(100, round((1 - naive_err / naive_act) * 100))) if naive_act > 0 else 0
    sku_accuracy = {
        sku: max(0, min(100, round((1 - err / sku_act[sku]) * 100)))
        for sku, err in sku_err.items()
        if sku_act[sku] > 0
    }

    # --- latest-completed-week accuracy (2026-08-22, user-requested) --------
    # Same pooled/volume-weighted formula as model_pct above, but restricted
    # to ONLY the single most recently matured week, not blended across the
    # whole retention window — "how did the model do on the week that JUST
    # finished," as distinct from "how has it done over the last N weeks."
    latest_week_iso = max(used_weeks) if used_weeks else ""
    latest_err = latest_act = 0.0
    if latest_week_iso:
        for (_sku, week_iso), d in week_data.items():
            if week_iso != latest_week_iso:
                continue
            latest_err += abs(d["actual"] - d["model_pred"])
            latest_act += d["actual"]
    latest_week_pct = max(0, min(100, round((1 - latest_err / latest_act) * 100))) if latest_act > 0 else 0

    # --- per-horizon-week accuracy (weeks 2-6, not just week-1 above) --------
    # Same walk-forward idea, from the SAME cached snapshots (no extra I/O),
    # broken out by how many weeks ahead each prediction was made. Additive:
    # doesn't touch the week-1-only pooled/per-SKU accuracy above, which
    # get_breakdown() depends on for the drill-down's "historical forecast".
    horizon_err: dict[int, float] = {}
    horizon_act: dict[int, float] = {}
    horizon_snaps: dict[int, set[str]] = {}
    for f in files:
        try:
            snap_date = date.fromisoformat(f.stem.rsplit("_", 1)[-1])
        except ValueError:
            continue
        try:
            forecasts = _json.loads(f.read_text())
        except Exception:
            continue
        if not forecasts:
            continue

        max_horizon = max((len(fc.get("weekly") or []) for fc in forecasts.values()), default=0)
        lo = pd.Timestamp(snap_date) - pd.Timedelta(days=34)
        hist = sales[(sales["order_date"] >= lo) & (sales["order_date"] <= pd.Timestamp(snap_date))]
        naive_week = hist.groupby("product_sku_code")["qty"].sum() / 5.0

        for w in range(max_horizon):
            week_start = _forecast_week_start(snap_date, w)
            week_end = week_start + timedelta(days=6)
            if pd.Timestamp(week_end) + timedelta(days=MATURATION_DAYS) >= snap:
                continue  # this horizon week hasn't matured yet
            wk_sales = sales[
                (sales["order_date"] >= pd.Timestamp(week_start))
                & (sales["order_date"] <= pd.Timestamp(week_end))
            ]
            actual = wk_sales.groupby("product_sku_code")["qty"].sum()
            mult = _week_festival(week_start)[0]
            horizon_snaps.setdefault(w, set()).add(f"{snap_date.isoformat()}:{week_start.isoformat()}")

            for sku, fc in forecasts.items():
                lgbm_weekly = fc.get("weekly") if fc else None
                if not lgbm_weekly or w >= len(lgbm_weekly):
                    continue
                nw = float(naive_week.get(sku, 0.0))
                act = float(actual.get(sku, 0.0))
                model_pred = max(0.0, (FORECAST_BLEND * lgbm_weekly[w] + (1 - FORECAST_BLEND) * nw) * mult)
                horizon_err[w] = horizon_err.get(w, 0.0) + abs(act - model_pred)
                horizon_act[w] = horizon_act.get(w, 0.0) + act

    HORIZON_ACCURACY = {
        w: max(0, min(100, round((1 - horizon_err[w] / horizon_act[w]) * 100)))
        for w in horizon_err if horizon_act.get(w, 0.0) > 0
    }
    if HORIZON_ACCURACY:
        _snap_counts = {w: len(s) for w, s in horizon_snaps.items()}
        print(f"[data] per-horizon-week backtest accuracy: {HORIZON_ACCURACY} "
              f"(snapshot-weeks scored per offset: {_snap_counts})", file=sys.stderr)

    return model_pct, naive_pct, used, sku_accuracy, sku_week_pred, latest_week_pct, latest_week_iso


def _backtest_design_model_accuracy(
    sales, snap: "pd.Timestamp"
) -> tuple[int, int, dict[str, dict[str, float]], dict[str, dict]]:
    """Genuine walk-forward accuracy for the design-level model
    (lgbm_forecast.compute_design()), at DESIGN_NO grain — the design-level
    counterpart of _backtest_model_accuracy() above (2026-09-18,
    user-requested demand-forecasting audit). Previously this model was
    retrained every night and never actually checked against reality in
    production — only its SKU-level sibling was backtested, even though
    compute_design()'s own docstring says design grain is measurably more
    accurate than summing SKU-level forecasts.

    ``sales`` must already be filtered to whichever demand definition the
    model was trained on (GROSS — see lgbm_forecast._GROSS_SALE_NORM /
    this module's _GROSS_SALE_STATUSES) — callers must pass ``gross_sales``,
    not the NET-filtered ``sales`` local variable, so the ground truth
    matches what the model was actually trained to predict.

    Returns (design_pct, snapshots_used, design_model_hist, tier_accuracy):
      - design_pct: pooled, volume-weighted accuracy (same formula as
        _backtest_model_accuracy's model_pct), across every matured week.
      - design_model_hist: {design_no: {week_start_iso: model_pred}} — real
        backtested design-level predictions, used by _aggregate_group_series()
        in place of a summed-SKU historical estimate wherever available.
      - tier_accuracy: same accuracy, broken out per catalog tier — see
        _tier_accuracy_breakdown() below.
    """
    import json as _json
    import pandas as pd

    cache_dir = Path(__file__).resolve().parent / ".cache"

    def _snap_date(f: Path) -> date:
        try:
            return date.fromisoformat(f.stem.rsplit("_", 1)[-1])
        except ValueError:
            return date.min

    files = sorted(
        cache_dir.glob("lgbm_design_forecasts_d*_*.json"), key=_snap_date
    )[-_BACKTEST_MAX_SNAPSHOTS:]

    design_model_hist: dict[str, dict[str, float]] = {}
    week_data: dict[tuple[str, str], dict[str, float]] = {}
    used_weeks: set[str] = set()
    for f in files:
        try:
            snap_date = date.fromisoformat(f.stem.rsplit("_", 1)[-1])
        except ValueError:
            continue

        week1_start = _forecast_week_start(snap_date, 0)
        week1_end = week1_start + timedelta(days=6)
        if pd.Timestamp(week1_end) + timedelta(days=MATURATION_DAYS) >= snap:
            continue  # week hasn't calendar-ended, or its actuals haven't matured yet

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
        actual = wk_sales.groupby("DESIGN_NO")["qty"].sum()

        lo = pd.Timestamp(snap_date) - pd.Timedelta(days=34)
        hist = sales[(sales["order_date"] >= lo) & (sales["order_date"] <= pd.Timestamp(snap_date))]
        naive_week = hist.groupby("DESIGN_NO")["qty"].sum() / 5.0

        mult = _week_festival(week1_start)[0]
        week_iso = week1_start.isoformat()
        used_weeks.add(week_iso)

        for design_no, fc in forecasts.items():
            dweekly = fc.get("weekly") if fc else None
            if not dweekly:
                continue
            nw = float(naive_week.get(design_no, 0.0))
            act = float(actual.get(design_no, 0.0))
            model_pred = max(0.0, (FORECAST_BLEND * dweekly[0] + (1 - FORECAST_BLEND) * nw) * mult)
            week_data[(design_no, week_iso)] = {"model_pred": model_pred, "actual": act}
            design_model_hist.setdefault(design_no, {})[week_iso] = model_pred

    model_err = model_act = 0.0
    for (_design_no, _week_iso), d in week_data.items():
        model_err += abs(d["actual"] - d["model_pred"])
        model_act += d["actual"]

    used = len(used_weeks)
    design_pct = max(0, min(100, round((1 - model_err / model_act) * 100))) if model_act > 0 else 0
    tier_accuracy = _tier_accuracy_breakdown(week_data)
    return design_pct, used, design_model_hist, tier_accuracy


def _tier_accuracy_breakdown(week_data: dict[tuple[str, str], dict[str, float]]) -> dict[str, dict]:
    """Pooled, volume-weighted accuracy (same formula as everywhere else in
    this file) from an already-computed design-level backtest's per-(design,
    week) errors, grouped by catalog tier — "Run the backtest for each tier"
    (2026-09-22, user-requested), to check whether the T0/T1/T2 cold-start
    caps (see lgbm_forecast._COLD_START_TIER_CAP) are actually paying off per
    tier rather than just in aggregate.

    Grouped by the tier that was in EFFECT AS OF that week (via
    catalog_style.get_tier_history()'s point-in-time log), not today's
    current tier — same leakage-safety reasoning as lgbm_forecast.py's
    _point_in_time_tier(): scoring a historical week against today's tier
    would judge the model on a promotion/demotion (e.g. T1 -> T0) it had no
    way of knowing about yet. "Unknown" covers any (design, week) with no
    tier history on record for that design at all.

    Returns {tier: {"accuracy": pct, "designs": n, "weeks": n}}; a tier with
    zero matured (design, week) pairs is simply absent from the result.
    """
    if not week_data:
        return {}
    import pandas as pd
    import catalog_style

    hist = catalog_style.get_tier_history()
    tier_map: dict[tuple[str, str], str] = {}
    if hist:
        pairs = pd.DataFrame(
            [{"design_no": d, "week_iso": w} for (d, w) in week_data]
        )
        hist_df = pd.DataFrame(hist).rename(columns={"name": "design_no"})
        # See lgbm_forecast._point_in_time_tier()'s docstring — merge_asof
        # needs identical datetime64 RESOLUTION on both sides, not just dtype.
        hist_df["effectiveFrom"] = pd.to_datetime(hist_df["effectiveFrom"]).astype("datetime64[ns]")
        hist_df = hist_df.sort_values("effectiveFrom")
        pairs["_week_dt"] = pd.to_datetime(pairs["week_iso"]).astype("datetime64[ns]")
        pairs = pairs.sort_values("_week_dt")
        merged = pd.merge_asof(
            pairs, hist_df[["design_no", "effectiveFrom", "tier"]],
            left_on="_week_dt", right_on="effectiveFrom", by="design_no", direction="backward",
        )
        tier_map = {
            (row.design_no, row.week_iso): (row.tier if pd.notna(row.tier) else "Unknown")
            for row in merged.itertuples()
        }

    err: dict[str, float] = {}
    act: dict[str, float] = {}
    designs: dict[str, set] = {}
    weeks: dict[str, set] = {}
    for (design_no, week_iso), d in week_data.items():
        tier = tier_map.get((design_no, week_iso), "Unknown")
        err[tier] = err.get(tier, 0.0) + abs(d["actual"] - d["model_pred"])
        act[tier] = act.get(tier, 0.0) + d["actual"]
        designs.setdefault(tier, set()).add(design_no)
        weeks.setdefault(tier, set()).add(week_iso)

    out: dict[str, dict] = {}
    for tier, a in act.items():
        if a <= 0:
            continue
        pct = max(0, min(100, round((1 - err[tier] / a) * 100)))
        out[tier] = {"accuracy": pct, "designs": len(designs[tier]), "weeks": len(weeks[tier])}
    return out


# Marketplace name extraction for the Channel & Source drill-down (2026-09-16,
# user-requested: name the actual platform, not the raw internal channel_name -
# "DiEGO International - Amazon FBA" and "Turritopsis - Amazon FBA" are the
# same platform, different legal entities, and should roll up into one
# "Amazon" row). Verified against every channel_name actually present in the
# data (2026-09-16): every OMS row matched one of five keywords back then,
# every WEBSITE row is one of the two site domains below. ("shopify" was
# added the same day for "ORLIN APPAREL PRIVATE LIMITED - Shopify", then
# removed 2026-09-18 when the user had that whole channel's sales excluded
# upstream in live_source.py - no row can match it anymore.) "wishlink"
# added 2026-09-18 for "DiEGO International - Wishlink" - a genuinely new
# channel (first order 2026-09-17, a real platform - creator/influencer
# shoppable links - not junk); user-requested it be rolled into "Amazon"
# rather than shown as its own row (matches the existing "DiEGO
# International - Amazon FBA" pattern above - both are just one channel's
# orders under a different legal-entity prefix).
_OMS_MARKETPLACE_KEYWORDS = [
    ("amazon", "Amazon"),
    ("flipkart", "Flipkart"),
    ("meesho", "Meesho"),
    ("myntra", "Myntra"),
    ("nykaa", "Nykaa"),
    ("wishlink", "Amazon"),
]


def _marketplace_of(channel_name: str, source: str) -> str:
    name = str(channel_name).strip().lower()
    if str(source) == "WEBSITE":
        if "mokosh" in name:
            return "MOKOSH"
        if "colorsofearth" in name:
            return "Color's Of Earth"
        return str(channel_name)  # unrecognized website domain - show raw name rather than guess
    for keyword, label in _OMS_MARKETPLACE_KEYWORDS:
        if keyword in name:
            return label
    return str(channel_name)  # unrecognized OMS channel - show raw name rather than guess


def _adjusted_weekly(snap_date: date, lgbm_weekly: list[float] | None,
                     naive_week: float) -> list[float]:
    """The next ``_FC_WEEKS`` weeks of demand for one SKU: LightGBM's weekly
    forecast blended with the seasonal-naive weekly run-rate (``FORECAST_BLEND``),
    with the India festival / sale-season uplift applied. This single series is
    the source of truth for the headline horizons (``forecast7/10/35`` = its
    1/≈1.4/5-week sums) and the drill-down chart, so the numbers reconcile.

    The model is ALSO trained with a festival_mult/is_festival/etc. feature
    (see lgbm_forecast.py), so this post-hoc multiply looked like a possible
    double-count. Checked 2026-08-19 via api/backtest_sweep.py (real
    walk-forward backtest, festival-multiplier on vs. off): festival-on was
    never worse than off across every FORECAST_BLEND value tested (only 2
    snapshots were scoreable, so this is weak evidence, not proof) — no sign
    of double-counting, so the post-hoc multiplier stays. Re-run the sweep
    with more accumulated snapshots before revisiting this."""
    series: list[float] = []
    for w in range(_FC_WEEKS):
        ws = _forecast_week_start(snap_date, w)
        if lgbm_weekly is not None and w < len(lgbm_weekly):
            base = FORECAST_BLEND * lgbm_weekly[w] + (1 - FORECAST_BLEND) * naive_week
        else:
            base = naive_week
        series.append(max(0.0, base * _week_festival(ws)[0]))
    return series


def _forecast_windows() -> tuple[list[tuple[date, date]], list[tuple[str, date, date, float]]]:
    """(week_bounds, festival_windows) for the current ``_FC_WEEKS``-week
    forecast horizon — ``week_bounds[w]`` is the (Monday, Sunday) span of
    forecast week ``w``; ``festival_windows`` are the real named sale/festival
    date ranges (see ``festival.windows_between``) overlapping that horizon."""
    week_bounds = [
        (_forecast_week_start(SNAPSHOT_DATE, w), _forecast_week_start(SNAPSHOT_DATE, w) + timedelta(days=6))
        for w in range(_FC_WEEKS)
    ]
    windows = festival.windows_between(week_bounds[0][0], week_bounds[-1][1])
    return week_bounds, windows


def _design_festival_spike(
    weekly: list[float],
    week_bounds: list[tuple[date, date]],
    windows: list[tuple[str, date, date, float]],
) -> dict | None:
    """The single best genuine festival/sale uplift (if any) in ``weekly``
    (one design's aggregated per-week forecast, aligned to ``week_bounds``),
    measured over each window's REAL calendar dates (e.g. "8-15 Aug" for
    Independence Day Sale), not the Monday-anchored bucket that overlaps it.
    Quantity is pro-rated across buckets by day-overlap; uplift compares
    daily rates so windows of different lengths stay comparable. ``None`` if
    nothing clears the 10%-uplift noise floor."""
    horizon_start, horizon_end = week_bounds[0][0], week_bounds[-1][1]

    def week_is_festival(ws: date, we: date) -> bool:
        return any(not (we < w_start or ws > w_end) for _n, w_start, w_end, _p in windows)

    def qty_for_range(r_start: date, r_end: date) -> float:
        total = 0.0
        for (ws, we), wk_qty in zip(week_bounds, weekly):
            overlap_days = (min(we, r_end) - max(ws, r_start)).days + 1
            if overlap_days > 0:
                total += (wk_qty / 7) * overlap_days
        return total

    non_festival_vals = [wk for (ws, we), wk in zip(week_bounds, weekly) if not week_is_festival(ws, we)]
    baseline_weekly = (sum(non_festival_vals) / len(non_festival_vals)) if non_festival_vals \
        else (sum(weekly) / len(weekly) if weekly else 0.0)
    if baseline_weekly <= 0:
        return None
    baseline_daily = baseline_weekly / 7

    best = None
    for name, w_start, w_end, _peak in windows:
        clipped_start, clipped_end = max(w_start, horizon_start), min(w_end, horizon_end)
        if clipped_start > clipped_end:
            continue
        window_days = (clipped_end - clipped_start).days + 1
        qty = qty_for_range(clipped_start, clipped_end)
        uplift_pct = round((qty / window_days / baseline_daily - 1) * 100)
        if uplift_pct < 10:  # not a real spike, just noise around baseline
            continue
        if best is None or uplift_pct > best["upliftPct"]:
            best = {
                "event": name,
                "eventStart": clipped_start.isoformat(),
                "eventEnd": clipped_end.isoformat(),
                "predictedQty": round(qty),
                "upliftPct": uplift_pct,
            }
    return best


def _design_weekly_series(skus: list[str], fc_weekly: dict[str, list[float]]) -> list[float] | None:
    """Sum each SKU's festival-adjusted weekly forecast into one series, or
    ``None`` if none of the SKUs have a cached forecast."""
    weekly = [0.0] * _FC_WEEKS
    has_series = False
    for sku in skus:
        series = fc_weekly.get(sku)
        if not series:
            continue
        has_series = True
        for i, v in enumerate(series[:_FC_WEEKS]):
            weekly[i] += v
    return weekly if has_series else None


def _design_weekly_series_preferred(
    design_no: str, skus: list[str], fc_weekly: dict[str, list[float]],
    design_fc: dict[str, dict] | None, naive_week_map: dict[str, float] | None,
) -> list[float] | None:
    """Design-level weekly FUTURE forecast: prefer lgbm_forecast.
    compute_design()'s own trained forecast for this design (measurably more
    accurate than summing SKU forecasts — see that function's docstring)
    when it covers this design_no, else fall back to summing each SKU's own
    festival-adjusted series (_design_weekly_series). Used for the Festival
    Spike signal AND (2026-09-18, user-requested demand-forecasting audit)
    the Weekly Sales Report's own Style/Sub Category row series via
    _aggregate_group_series() — see _design_model_hist_preferred below for
    the equivalent policy on the HISTORICAL (backtested) side.

    The design-level model's raw output isn't festival/naive-blended the way
    each SKU's own series already is, so it gets the SAME _adjusted_weekly
    treatment here (a design-level naive rate = sum of its SKUs' own naive
    rates) to stay comparable in scale and festival-awareness."""
    if design_fc and design_no in design_fc:
        dseries = design_fc[design_no].get("weekly")
        if dseries:
            naive_d = sum((naive_week_map or {}).get(s, 0.0) for s in skus)
            return _adjusted_weekly(SNAPSHOT_DATE, dseries, naive_d)
    return _design_weekly_series(skus, fc_weekly)


def _design_model_hist_preferred(
    design_no: str, skus: list[str],
    sku_model_hist: dict[str, dict[str, float]],
    design_model_hist: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Per-week backtested model prediction for one design (HISTORICAL side):
    prefer the design-level model's own real backtested output
    (DESIGN_MODEL_HIST, from _backtest_design_model_accuracy) over summing
    its SKUs' own SKU-level backtested predictions (SKU_MODEL_HIST) — the
    same "prefer design-level" policy _design_weekly_series_preferred
    applies to the FUTURE forecast, kept consistent here so the Weekly Sales
    Report's chart doesn't show a seam right at the past/future boundary
    (2026-09-18, user-requested demand-forecasting audit)."""
    dh = design_model_hist.get(design_no)
    if dh:
        return dict(dh)
    out: dict[str, float] = {}
    for s in skus:
        for wk_iso, v in sku_model_hist.get(s, {}).items():
            out[wk_iso] = out.get(wk_iso, 0.0) + v
    return out


def attach_festival_spikes(
    rows: list[PlanRow], fc_weekly: dict[str, list[float]],
    design_fc: dict[str, dict] | None = None,
    naive_week_map: dict[str, float] | None = None,
) -> None:
    """Populate every row's ``festivalEvent*``/``festivalQty``/
    ``festivalUpliftPct`` from its design's aggregated forecast — ALL
    designs, unlike ``get_new_design_festival_spikes`` (cold-start dialog
    only, age-filtered). Powers the "Predicted Festival Spike" column.
    Rows with no qualifying window keep the field defaults. Takes
    ``fc_weekly`` explicitly, not the module-level ``_FC_WEEKLY``, since this
    runs inside ``_load_real_plan`` before that global is published (same
    reason ``design_fc``/``naive_week_map`` are passed explicitly here rather
    than read from ``_FC_WEEKLY_DESIGN``/``_NAIVE_WEEK``)."""
    week_bounds, windows = _forecast_windows()
    if not windows:
        return

    by_design: dict[str, list[str]] = {}
    for r in rows:
        by_design.setdefault(r.designNo, []).append(r.skuCode)

    spike_by_design: dict[str, dict] = {}
    for design_no, skus in by_design.items():
        weekly = _design_weekly_series_preferred(design_no, skus, fc_weekly, design_fc, naive_week_map)
        if weekly is None:
            continue
        spike = _design_festival_spike(weekly, week_bounds, windows)
        if spike:
            spike_by_design[design_no] = spike

    for r in rows:
        spike = spike_by_design.get(r.designNo)
        if spike:
            r.festivalEvent = spike["event"]
            r.festivalEventStart = spike["eventStart"]
            r.festivalEventEnd = spike["eventEnd"]
            r.festivalQty = spike["predictedQty"]
            r.festivalUpliftPct = spike["upliftPct"]


_JUNK_PLACE_NAMES = {"", "unknown", "nan", "na", "null", "none", "n/a", "-"}


def _norm_place(s) -> str | None:
    """Clean a state / city name: collapse whitespace, drop a trailing
    pincode-like number ('Chennai 600081' -> 'Chennai'), reject placeholders."""
    import pandas as pd

    if pd.isna(s):
        return None
    t = re.sub(r"\s+", " ", str(s).strip())
    t = re.sub(r"\s+\d+$", "", t)  # strip a trailing standalone number
    if t.lower() in _JUNK_PLACE_NAMES or not any(c.isalpha() for c in t):
        return None
    return t.title()


def _norm_warehouse(s) -> str | None:
    """Keep real FC names / codes (BLR8, DEL5, Malur BTS) but drop blanks,
    numeric-only values and the 'DEFAULT …' unassigned catch-all bucket."""
    import pandas as pd

    if pd.isna(s):
        return None
    t = re.sub(r"\s+", " ", str(s).strip())
    tl = t.lower()
    if tl in _JUNK_PLACE_NAMES or tl.startswith("default") or not any(c.isalpha() for c in t):
        return None
    return t


def _geo_frame(sales) -> "pd.DataFrame":
    """``sales`` (already status-filtered order rows) with normalized
    _state/_city/_warehouse columns added — the shared input to ``_top_agg``,
    used for both the plan-wide top tables and a single SKU's top regions."""
    return sales.assign(
        _state=sales["buyer_state"].map(_norm_place),
        _city=sales["buyer_city"].map(_norm_place),
        _warehouse=sales["warehouse_name"].map(_norm_warehouse),
    )


def _top_agg(geo, col: str, snap: "pd.Timestamp", cap: int = 100, sort_key: str = "units30") -> list[dict]:
    """Revenue + sale quantity by ``col`` (_state/_city/_warehouse) over the
    last 10/30/90 days, sorted by ``sort_key`` (default 30-day quantity), top
    ``cap`` kept."""
    import pandas as pd

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
    out.sort(key=lambda r: r[sort_key], reverse=True)
    return out[:cap]


def _load_real_plan(df, forecasts: dict | None = None, design_forecasts: dict | None = None) -> tuple[
    list[PlanRow], dict[str, dict[str, int]], dict[str, list[float]], dict[str, float],
    list["TopRegion"], list["TopRegion"], list["TopWarehouse"],
]:
    """Build the SKU production plan + top-selling tables from the merged order
    history. The model's weekly forecast ({sku: {"weekly": [...]}}) is blended with
    each SKU's run-rate and the festival uplift into one weekly series
    (``_adjusted_weekly``); ``forecast7/10/35`` are its 1/≈2/5-week sums (the same
    series the drill-down shows). ``inventoryQty`` = PENDING_QTY_PIECES, ``wipQty``
    = TOTAL_WIP_QTY (the Planning view's own combined figure — open production
    order + in-house + job-work), ``availableQty`` = inventory + WIP. Top tables = gross revenue
    + units per state/city/warehouse over 10/30/90 days (junk names dropped). Sets
    module-level ``SNAPSHOT_DATE`` and ``OVERALL_ACCURACY``."""
    global SNAPSHOT_DATE, OVERALL_ACCURACY, SKU_ACCURACY, SKU_MODEL_HIST
    global LATEST_WEEK_ACCURACY, LATEST_WEEK_ISO, _WEEK_ACTUAL_GROSS, _DAY_ACTUAL_GROSS
    global DESIGN_ACCURACY, DESIGN_MODEL_HIST, TIER_ACCURACY
    import pandas as pd

    df = df.dropna(subset=["product_sku_code"])
    # Drop exact-duplicate order lines (the source has no order_id) and any
    # future-dated orders.
    df = df.drop_duplicates()
    df = df[df["order_date"] <= pd.Timestamp.today().normalize()]

    snap = pd.Timestamp(df["order_date"].max()).normalize()
    SNAPSHOT_DATE = snap.date()
    sales = df[df["order_status"].astype(str).str.strip().str.lower().isin(_SOLD_STATUSES)]
    # GROSS counterpart of `sales` above - the user's own explicit allowlist
    # (see _GROSS_SALE_STATUSES), so returns/RTO/reverse-logistics count.
    # Feeds ONLY _WEEK_ACTUAL_GROSS/_DAY_ACTUAL_GROSS below - the Weekly
    # Sales Report's own "Actual Sale" (2026-09-15, user-requested), nothing
    # else.
    gross_sales = df[df["order_status"].astype(str).str.strip().str.lower().isin(_GROSS_SALE_STATUSES)]
    # Return-status rows (for lifecycle return-rate); separate from sold demand.
    returns = df[df["order_status"].astype(str).str.contains("return", case=False, na=False)]

    def window_sum(days: int, col: str = "qty", src=None):
        lo = snap - pd.Timedelta(days=days - 1)
        frame = sales if src is None else src
        return frame[frame["order_date"] >= lo].groupby("product_sku_code")[col].sum()

    last10 = window_sum(10)
    # GROSS, not Net (2026-09-18, user-requested: demand forecasting must be
    # for Gross Sale) - naive_week below (the seasonal-naive baseline blended
    # into every SKU's published forecast via _adjusted_weekly) was being
    # computed from NET-only `sales` while lgbm_forecast.py trains the model
    # itself on GROSS (_GROSS_SALE_NORM, since v30) and the Weekly Sales
    # Report displays a GROSS "Actual Sale" (_WEEK_ACTUAL_GROSS) right next
    # to this same forecast number - a real unit mismatch: with
    # FORECAST_BLEND=0.25, 75% of every published forecast was a NET run-rate
    # shown against a GROSS actual, a structural downward bias having nothing
    # to do with model quality.
    last35 = window_sum(35, src=gross_sales)
    # Realized avg selling price (revenue/qty) over the last 90 days.
    qty90 = window_sum(90)
    revenue90 = window_sum(90, col="total")
    wip = df.groupby("product_sku_code")["TOTAL_WIP_QTY"].max()
    # PENDING_QTY_PIECES is the on-hand inventory (denormalised per row -> max).
    inventory_by_sku = df.groupby("product_sku_code")["PENDING_QTY_PIECES"].max()
    design = df.groupby("product_sku_code")["DESIGN_NO"].first()

    # (2026-08-24, user-requested: removed the separate Job Work / Inhouse /
    # FOB tracker addition that used to sit here. It was double-counting —
    # the Planning view's own TOTAL_WIP_QTY already includes an
    # IN_HOUSE_QTY_WIP component, and the separate "inhouse" tracker was
    # adding a SECOND, larger figure for what is the same in-house-production
    # category: verified against real data for design 474-01, Planning view
    # reported IN_HOUSE_QTY_WIP=11,984 while the separate inhouse tracker
    # added another 28,888 on top, roughly matching the inflated ~32-42k WIP
    # figures that prompted this check. TOTAL_WIP_QTY alone is the ERP's own
    # single combined figure and is used as-is now.)
    # Earliest launch date per SKU -> drives days-since-launch / launch tier.
    # NOT sourced from df["LAUNCH_DATE"]: that column is populated by
    # live_source.assemble()'s sales-row merge onto a regex-extracted
    # design_key prefix, deduplicated by keeping an arbitrary "first" master
    # row per key - when a design_key collides with more than one master
    # row, some of a design's own order rows can join to the WRONG one
    # (observed: a spurious Excel-epoch 1900-01-01 placeholder for design
    # 417-04, whose real LAUNCH_DATE of 2024-06-25 is intact in the master
    # view fetched directly). Fetch the master view directly instead (one
    # row per DESIGN_NO, no join/collision) and map each SKU to its own
    # design's launch date.
    launched = pd.Series(dtype="datetime64[ns]")
    master_launch: dict[str, "pd.Timestamp"] = {}
    try:
        import live_source
        master = live_source.fetch_erp_view(live_source.VIEW_MASTER)
        if not master.empty and "DESIGN_NO" in master.columns:
            master_dt = pd.to_datetime(master.get("LAUNCH_DATE"), errors="coerce")
            for dn, dt in zip(master["DESIGN_NO"].astype(str), master_dt):
                if pd.isna(dt) or dt < pd.Timestamp("2015-01-01"):
                    continue  # implausible placeholder (e.g. Excel epoch) - treat as unknown
                if dn not in master_launch or dt < master_launch[dn]:
                    master_launch[dn] = dt
            launched = design.astype(str).map(master_launch)
    except Exception as exc:  # noqa: BLE001 — never let this block the plan rebuild
        print(f"[data] direct launch-date fetch failed: {exc!r}", file=sys.stderr)

    # --- Style lifecycle: per-Sub-Category style counts (2026-09-22, user-
    # requested: "in a particular sub category how many/which styles are
    # present in last year and how many are added in this current year") -
    # "present" = had >=1 real (Gross) sale anywhere in the PRIOR calendar
    # year; "added" = real ERP launch date (master_launch above, not the
    # collision-prone sales-row LAUNCH_DATE) falls in the CURRENT calendar
    # year. Both years are derived from `snap`, not hardcoded, so this stays
    # correct without a manual edit every January. A style can appear in
    # both sets if its ERP launch date was corrected/back-dated after it had
    # already sold - reported as-is rather than silently forced exclusive,
    # since that reflects a real (if imperfect) source-data quirk worth
    # seeing, not something to paper over.
    global STYLE_LIFECYCLE
    _this_year = pd.Timestamp(snap).year
    _last_year = _this_year - 1
    _gross_dates = pd.to_datetime(gross_sales["order_date"], errors="coerce")
    _gross_design = gross_sales["DESIGN_NO"].astype(str).str.strip().str.upper()
    # dropna(): pandas 3's astype(str) keeps blank DESIGN_NO as NaN (not
    # "nan"), which would put a float into the set and crash sorted() below.
    _present_designs = set(_gross_design[_gross_dates.dt.year == _last_year].dropna().unique())
    _added_designs = {
        str(dn).strip().upper() for dn, dt in master_launch.items() if dt.year == _this_year
    }
    _lifecycle_raw: dict[str, dict[str, set]] = {}
    for _dn in _present_designs | _added_designs:
        _sc = design_attributes.sub_category_of(_dn) or "Unclassified"
        _bucket = _lifecycle_raw.setdefault(_sc, {"present": set(), "added": set()})
        if _dn in _present_designs:
            _bucket["present"].add(_dn)
        if _dn in _added_designs:
            _bucket["added"].add(_dn)
    STYLE_LIFECYCLE = {
        "lastYear": _last_year,
        "thisYear": _this_year,
        "bySubCategory": {
            _sc: {
                "presentCount": len(_v["present"]),
                "presentStyles": sorted(_v["present"]),
                "addedCount": len(_v["added"]),
                "addedStyles": sorted(_v["added"]),
            }
            for _sc, _v in _lifecycle_raw.items()
        },
    }

    # Per-SKU WEEKLY actual sales (Mon-anchored), back to _BREAKDOWN_HIST_START —
    # feeds BOTH the drill-down's full history table and (via the RECENT-only
    # slice below) the safety-stock demand-variance term (sigma_w).
    last_monday = pd.Timestamp(snap).normalize() - pd.Timedelta(days=pd.Timestamp(snap).weekday())
    wkrec = sales[sales["order_date"] >= pd.Timestamp(_BREAKDOWN_HIST_START)].copy()
    # Monday week-start. NOTE: to_period("W-MON").start_time would give TUESDAY
    # (W-MON = weeks ENDING Monday).
    wkrec["_wk"] = wkrec["order_date"] - pd.to_timedelta(wkrec["order_date"].dt.weekday, unit="D")
    wk_g = wkrec.groupby(["product_sku_code", "_wk"])["qty"].sum()
    week_actual: dict[str, dict[str, int]] = {}
    for (sku, wkts), q in wk_g.items():
        week_actual.setdefault(str(sku), {})[wkts.date().isoformat()] = int(q)
    # Per-SKU DAILY actual sales, same source rows as week_actual above, just
    # grouped by calendar day instead of Mon-Sun week - feeds ONLY the Weekly
    # Sales Report's calendar-day week grid (2026-09-14, user-requested: "W1
    # must be start from the 1st Date of the month" - a fixed 1-7/8-15/16-23/
    # 24-end split per month (2026-09-17, was 1-8/9-15/16-22/23-end),
    # independent of weekday, unlike every other week-bucket in this app).
    # See get_weekly_grid()/_calendar_chunk_axis().
    day_g = wkrec.groupby(["product_sku_code", wkrec["order_date"].dt.normalize()])["qty"].sum()
    day_actual: dict[str, dict[str, int]] = {}
    for (sku, dts), q in day_g.items():
        day_actual.setdefault(str(sku), {})[dts.date().isoformat()] = int(q)

    # GROSS mirror of the two aggregations just above, built from
    # `gross_sales` instead of `sales` (2026-09-15, user-requested) - published
    # directly to the module globals here rather than threaded through this
    # function's return tuple/_set_plan, since only the Weekly Sales Report
    # reads them and every other caller of this function's return value is
    # unaffected either way.
    wkrec_gross = gross_sales[gross_sales["order_date"] >= pd.Timestamp(_BREAKDOWN_HIST_START)].copy()
    wkrec_gross["_wk"] = wkrec_gross["order_date"] - pd.to_timedelta(wkrec_gross["order_date"].dt.weekday, unit="D")
    wk_g_gross = wkrec_gross.groupby(["product_sku_code", "_wk"])["qty"].sum()
    week_actual_gross: dict[str, dict[str, int]] = {}
    for (sku, wkts), q in wk_g_gross.items():
        week_actual_gross.setdefault(str(sku), {})[wkts.date().isoformat()] = int(q)
    day_g_gross = wkrec_gross.groupby(["product_sku_code", wkrec_gross["order_date"].dt.normalize()])["qty"].sum()
    day_actual_gross: dict[str, dict[str, int]] = {}
    for (sku, dts), q in day_g_gross.items():
        day_actual_gross.setdefault(str(sku), {})[dts.date().isoformat()] = int(q)
    _WEEK_ACTUAL_GROSS = week_actual_gross
    _DAY_ACTUAL_GROSS = day_actual_gross

    # Recent-only week keys for sigma_w (see _HIST_WEEKS' comment) - week_actual
    # itself now spans all the way back to _BREAKDOWN_HIST_START.
    _recent_week_keys = {
        (last_monday - pd.Timedelta(weeks=k)).date().isoformat() for k in range(_HIST_WEEKS)
    }

    iso = SNAPSHOT_DATE.isoformat()
    rows: list[PlanRow] = []
    fc_weekly_adj: dict[str, list[float]] = {}
    naive_week_map: dict[str, float] = {}
    for sku in df["product_sku_code"].unique():
        skey = str(sku)
        naive35 = int(last35.get(sku, 0))
        naive_week = naive35 / 5.0          # avg weekly run-rate (last 5 weeks ≈ 35 days)
        q90 = float(qty90.get(sku, 0))
        price = round(float(revenue90.get(sku, 0)) / q90, 2) if q90 > 0 else 0.0
        fc = forecasts.get(skey) if forecasts else None
        lgbm_weekly = fc.get("weekly") if fc else None
        # One festival-adjusted, LGBM+run-rate-blended WEEKLY series drives BOTH the
        # headline horizons and the drill-down, so they reconcile and production
        # responds to festival / sale spikes.
        wseries = _adjusted_weekly(SNAPSHOT_DATE, lgbm_weekly, naive_week)
        f7 = round(wseries[0])                       # week 1 ≈ 7 days
        f10 = round(wseries[0] + wseries[1] * (3 / 7))  # 1 week + 3 days
        # Sum of each week ROUNDED first (not round-of-the-sum), matching how the
        # drill-down graph rounds per week — so the two always tie out exactly.
        f35 = sum(round(x) for x in wseries[:5])     # 5 weeks ≈ 35 days
        fc_weekly_adj[skey] = [round(x, 3) for x in wseries]
        naive_week_map[skey] = naive_week
        hist10 = int(last10.get(sku, 0))
        dn = design.get(sku)
        w = wip.get(sku)
        wip_qty = 0 if pd.isna(w) else int(round(float(w)))
        inv = inventory_by_sku.get(sku)
        inventory = 0 if pd.isna(inv) else int(round(float(inv)))
        available = inventory + wip_qty
        # Lead time + safety stock kept as informational fields only (Produce
        # Now is set below, after lifecycle.classify, from currentDrr).
        lead = _lead_days(None if pd.isna(dn) else dn)
        acts = [v for wk_iso, v in week_actual.get(skey, {}).items() if wk_iso in _recent_week_keys]
        sigma_w = float(pd.Series(acts, dtype="float64").std()) if len(acts) >= 2 else 0.0
        safety = max(0, round(SERVICE_Z * sigma_w * (lead / 7.0) ** 0.5))
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
                reorderPoint=0,
                totalSuggestedProduction=0,
                calculatedProductionSuggestion=0,
                stockStatus="In Stock",
                historicalLast10d=hist10,
                subCategory=design_attributes.sub_category_of(None if pd.isna(dn) else dn),
                category=design_attributes.category_of(None if pd.isna(dn) else dn),
                price=price,
            )
        )
    rows.sort(key=lambda r: r.forecast35, reverse=True)
    lifecycle.classify(rows, sales, returns, launched, SNAPSHOT_DATE)
    attach_festival_spikes(rows, fc_weekly_adj, design_forecasts, naive_week_map)

    # Flat 35-day-demand production policy (2026-08-24, user-specified):
    # Target = DRR * 35 days (matching forecast35's own horizon). Produce Now
    # = Target - Available, triggered only when Available < forecast35.
    # Target uses currentDrr — the HISTORICAL trailing-30-day actual sales
    # rate set by lifecycle.classify above (same number shown in the
    # dashboard's DRR column) — not the forward forecast.
    for r in rows:
        target = round(r.currentDrr * 35)
        r.reorderPoint = target
        r.totalSuggestedProduction = target
        r.calculatedProductionSuggestion = (target - r.availableQty) if r.availableQty < r.forecast35 else 0
        r.stockStatus = "In Stock" if r.availableQty >= r.forecast35 else "Needs Production"

    # Design-level 5-week forecast, for context alongside each SKU's own
    # forecast35 — this table's DRR/reorder/production math above stays
    # driven by the SKU-level model; see get_breakdown()'s docstring for why
    # redistributing a design-level total back down to SKU grain was tested
    # and found worse for that kind of decision. Purely an extra column here.
    by_design_rows: dict[str, list[PlanRow]] = {}
    for r in rows:
        by_design_rows.setdefault(r.designNo, []).append(r)
    for design_no, drows in by_design_rows.items():
        skus = [r.skuCode for r in drows]
        dseries = _design_weekly_series_preferred(design_no, skus, fc_weekly_adj, design_forecasts, naive_week_map)
        d_forecast35 = round(sum(dseries[:5])) if dseries else 0
        for r in drows:
            r.designForecast35 = d_forecast35

    # --- overall MODEL accuracy: genuine walk-forward backtest ---------------- #
    # This used to compare the seasonal-naive baseline against itself (naive_week
    # projected backward vs actual) — it never touched the LightGBM-blended
    # forecast at all, so "Model Accuracy" on the dashboard could never move no
    # matter how much the model improved. Score the real cached model output
    # instead; fall back to the old naive-only proxy only if no historical
    # snapshot is old enough yet to score (e.g. a brand-new install).
    #
    # Scored against `gross_sales`, NOT the NET-filtered `sales` above
    # (2026-09-18, user-requested: demand forecasting must be for Gross Sale)
    # - lgbm_forecast.py trains the deployed model on GROSS (_GROSS_SALE_NORM,
    # since v30), so scoring it against NET ground truth compared the model's
    # own predictions to the wrong target - Gross always >= Net (it includes
    # returns/RTO), so this was structurally UNDERSTATING accuracy, not just
    # measuring it noisily.
    model_pct, naive_pct, n_snapshots, sku_accuracy, sku_week_pred, latest_week_pct, latest_week_iso = (
        _backtest_model_accuracy(gross_sales, snap) if RUN_BACKTEST else (0, 0, 0, {}, {}, 0, "")
    )
    if n_snapshots > 0:
        OVERALL_ACCURACY = model_pct
        SKU_ACCURACY = sku_accuracy
        SKU_MODEL_HIST = sku_week_pred
        LATEST_WEEK_ACCURACY = latest_week_pct
        LATEST_WEEK_ISO = latest_week_iso
        print(
            f"[data] backtest accuracy over {n_snapshots} snapshot(s): "
            f"model={model_pct}% naive={naive_pct}% ({len(sku_accuracy)} SKUs scored) | "
            f"latest week ({latest_week_iso}): {latest_week_pct}%",
            file=sys.stderr,
        )
    else:
        # Volume-weighted (WAPE) naive-only proxy — the only thing computable
        # before any forecast snapshot is old enough to score against reality.
        # GROSS actuals (week_actual_gross), matching naive_week_map now
        # being Gross-sourced too (see window_sum's `last35` above) - both
        # sides of this comparison must be the same demand definition.
        hist_weeks = [last_monday.date() - timedelta(days=7 * k) for k in range(_HIST_WEEKS, 0, -1)]
        hist_mult = {ws.isoformat(): _week_festival(ws)[0] for ws in hist_weeks}
        abs_err = tot_act = 0.0
        for skey, wkmap in week_actual_gross.items():
            nw = naive_week_map.get(skey, 0.0)
            sku_act = sum(wkmap.get(ws.isoformat(), 0) for ws in hist_weeks)
            sku_exp = sum(max(0, round(nw * hist_mult[ws.isoformat()])) for ws in hist_weeks)
            abs_err += abs(sku_act - sku_exp)
            tot_act += sku_act
        OVERALL_ACCURACY = max(0, min(100, round((1 - abs_err / tot_act) * 100))) if tot_act > 0 else 0
        SKU_ACCURACY = {}
        SKU_MODEL_HIST = {}
        LATEST_WEEK_ACCURACY = 0
        LATEST_WEEK_ISO = ""
        print("[data] no scoreable snapshot yet — using naive-only proxy accuracy", file=sys.stderr)

    # --- design-level MODEL accuracy: same walk-forward backtest, DESIGN_NO
    # grain (2026-09-18, user-requested demand-forecasting audit) - this model
    # was previously trained every night and never actually checked against
    # reality in production, only its SKU-level sibling above was. Also
    # populates DESIGN_MODEL_HIST, which _aggregate_group_series() prefers
    # over summed SKU-level backtested predictions for the Weekly Sales
    # Report's historical (already-elapsed) weeks - keeping the same
    # "prefer design-level" policy on the historical side as the future
    # forecast side (see _design_weekly_series_preferred). Scored against
    # gross_sales, same reasoning as the SKU-level backtest above.
    design_pct, design_used, design_model_hist, tier_accuracy = (
        _backtest_design_model_accuracy(gross_sales, snap) if RUN_BACKTEST else (0, 0, {}, {})
    )
    if design_used > 0:
        DESIGN_ACCURACY = design_pct
        DESIGN_MODEL_HIST = design_model_hist
        TIER_ACCURACY = tier_accuracy
        print(f"[data] design-level backtest accuracy over {design_used} snapshot(s): "
              f"{design_pct}% ({len(design_model_hist)} designs scored)", file=sys.stderr)
        if tier_accuracy:
            print(f"[data] backtest accuracy by tier: {tier_accuracy}", file=sys.stderr)
    else:
        DESIGN_ACCURACY = 0
        DESIGN_MODEL_HIST = {}
        TIER_ACCURACY = {}

    # --- top-selling state / city / warehouse (real revenue + quantity) ------ #
    geo = _geo_frame(sales)
    # cap=100: the long noisy tail (buyer_city has ~9k variants) doesn't need
    # to be shown on the plan-wide tables. get_sku_top_regions() below reuses
    # this same _top_agg with a much smaller cap for a single SKU's drawer.
    top_states = [TopRegion(**d) for d in _top_agg(geo, "_state", snap, cap=100)]
    top_cities = [TopRegion(**d) for d in _top_agg(geo, "_city", snap, cap=100)]
    top_warehouses = [TopWarehouse(**d) for d in _top_agg(geo, "_warehouse", snap, cap=100)]

    return (rows, week_actual, day_actual, fc_weekly_adj, naive_week_map,
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
# Per-SKU WEEKLY actual sales {week_start_iso: qty}, back to _BREAKDOWN_HIST_START.
_WEEK_ACTUAL: dict[str, dict[str, int]] = {}
# Per-SKU DAILY actual sales {day_iso: qty}, back to _BREAKDOWN_HIST_START -
# only the Weekly Sales Report's calendar-day week grid uses this (see
# get_weekly_grid()); everything else stays on _WEEK_ACTUAL's Mon-Sun weeks.
_DAY_ACTUAL: dict[str, dict[str, int]] = {}
# GROSS counterparts of the two above - same shape, same source rows, just
# filtered by _GROSS_SALE_STATUSES (the user's own explicit allowlist)
# instead of _SOLD_STATUSES, i.e. includes returns/RTO/reverse-logistics.
# Only the Weekly Sales Report reads these (2026-09-15, user-requested: show Gross, not Net,
# as "Actual Sale" everywhere on that page) - every other feature (SKU/
# Sub Category drill-down, DRR, cold-start, the model's own training target)
# stays on the NET dicts above unchanged.
_WEEK_ACTUAL_GROSS: dict[str, dict[str, int]] = {}
_DAY_ACTUAL_GROSS: dict[str, dict[str, int]] = {}
# Per-SKU festival-adjusted, blended WEEKLY forecast (next _FC_WEEKS weeks) — the
# single series behind BOTH the headline horizons (forecast7/10/35) and the
# drill-down chart, so they always reconcile.
_FC_WEEKLY: dict[str, list[float]] = {}
# Per-design RAW weekly forecast from lgbm_forecast.compute_design() — a
# second, independently-trained model at DESIGN_NO grain (see that function's
# docstring for why it's measurably more accurate than summing SKU forecasts
# for a design's total demand). Preferred (see _design_weekly_series_preferred)
# over summing SKU-level series for the Festival Spike signal AND (2026-09-18,
# user-requested demand-forecasting audit) for the Weekly Sales Report's own
# Style/Sub Category row series via _aggregate_group_series() - previously
# this model was trained every night and used ONLY for the festival-spike
# badge, so the numbers everyone actually reads never benefited from it.
# Never redistributes back down to SKU level. Empty until the background
# retrain covers a design.
_FC_WEEKLY_DESIGN: dict[str, dict] = {}
# Per-SKU seasonal-naive WEEKLY level (5-week run-rate); the drill-down back-test
# baseline and the fallback when LightGBM doesn't cover a SKU.
_NAIVE_WEEK: dict[str, float] = {}
# Overall forecast accuracy (0-100), volume-weighted WAPE over recent weeks.
OVERALL_ACCURACY: int = 0
# Accuracy (0-100) for ONLY the single most recently matured week, distinct
# from OVERALL_ACCURACY's multi-week pooled figure — "how did the model do
# on the week that just finished" (2026-08-22, user-requested). LATEST_WEEK_ISO
# is that week's Monday date, for labeling; both stay at defaults (0 / "")
# until at least one week has matured.
LATEST_WEEK_ACCURACY: int = 0
LATEST_WEEK_ISO: str = ""
# Per-SKU backtested accuracy (0-100) — same real-model walk-forward backtest as
# OVERALL_ACCURACY, just not pooled across SKUs. Only populated for SKUs that
# appeared in at least one scoreable cached snapshot (see _backtest_model_accuracy).
SKU_ACCURACY: dict[str, int] = {}
# Per-SKU real model predictions for past weeks that have actually been
# backtested: {sku: {week_start_iso: model_pred}}. _aggregate_group_series()
# uses these in place of the naive baseline wherever a genuine backtested
# value exists (falling back to DESIGN_MODEL_HIST first when the SKU's own
# design has one - see _design_model_hist_preferred).
SKU_MODEL_HIST: dict[str, dict[str, float]] = {}
# Design-level counterpart of OVERALL_ACCURACY/SKU_MODEL_HIST, from
# _backtest_design_model_accuracy() (2026-09-18, user-requested
# demand-forecasting audit) - the design-level model (lgbm_forecast.
# compute_design()) used to be trained nightly and never checked against
# reality in production. DESIGN_MODEL_HIST is {design_no: {week_start_iso:
# model_pred}}, preferred by _aggregate_group_series() over summing a
# design's own SKUs' SKU_MODEL_HIST entries wherever it covers that design.
DESIGN_ACCURACY: int = 0
DESIGN_MODEL_HIST: dict[str, dict[str, float]] = {}
# Design-level backtest accuracy broken out per catalog tier, from
# _tier_accuracy_breakdown() (2026-09-22, user-requested: "Run the Backtest
# for each tier") - {tier: {"accuracy": pct, "designs": n, "weeks": n}},
# empty until at least one matured (design, week) pair exists.
TIER_ACCURACY: dict[str, dict] = {}
# Per-Sub-Category style counts: styles present (sold) in the prior calendar
# year vs newly launched in the current one, from rebuild() (2026-09-22,
# user-requested). {"lastYear": int, "thisYear": int, "bySubCategory":
# {sub_category: {presentCount, presentStyles, addedCount, addedStyles}}}.
STYLE_LIFECYCLE: dict = {}
# Per-horizon-week backtested accuracy (0-100): {week_offset: accuracy_pct}.
# OVERALL_ACCURACY only ever scores week-1 of each snapshot's forecast — this
# extends the same walk-forward backtest across the full HORIZON_WEEKS, since
# weeks 2-6 are most of the 35-day production-planning horizon and were
# previously never actually checked against reality. Set by
# _backtest_model_accuracy; empty until enough snapshots have matured at each
# offset. Diagnostic/visibility only — not consumed by the plan itself.
HORIZON_ACCURACY: dict[int, int] = {}
TOP_STATES: list[TopRegion] = []
TOP_CITIES: list[TopRegion] = []
TOP_WAREHOUSES: list[TopWarehouse] = []
PLAN_BY_SKU: dict[str, PlanRow] = {}
# Bumped by _set_plan() on every publish (initial load, daily auto-refresh,
# LGBM upgrade, manual /admin/refresh) - part of the cache key below so a
# cached response is only ever served for the plan snapshot it was computed
# from, with no separate invalidation logic needed.
_DATA_VERSION: int = 0
# get_weekly_grid() response cache, keyed on every request param plus
# _DATA_VERSION (2026-09-14, user-requested: the Weekly Sales Report re-runs
# a full per-group (~1,500 Styles) forecast aggregation on every single
# request - expensive, and the frontend now polls it every 60s (see
# useWeeklyGrid's refetchInterval) - so identical requests between two data
# refreshes should be near-instant instead of redoing that work each time).
# Cleared wholesale on every _set_plan() rather than using per-entry TTLs -
# simplest correct option since the underlying data only ever changes there.
_WEEKLY_GRID_CACHE: dict[tuple, WeeklyGridResponse] = {}
# Crude cap: search text can produce unbounded distinct cache keys - if a lot
# of one-off searches pile up, drop the whole cache rather than growing an
# LRU eviction path for what is, in practice, a handful of common queries.
_WEEKLY_GRID_CACHE_MAX = 200

# _channel_source_base_df() base dataframe (deduplicated, Gross-filtered,
# month-labeled) - keyed on _DATA_VERSION so it's rebuilt once per refresh,
# not per request (2026-09-16, user-requested per-Style monthly Actual
# drill-down into marketplace/OMS-vs-Website breakdown).
_CHANNEL_SOURCE_BASE_CACHE: tuple[int, object] | None = None

# get_festival_outlook() result - keyed on (_DATA_VERSION, today, top_n), same
# reasoning as _CHANNEL_SOURCE_BASE_CACHE (2026-09-26, user-reported: the
# Channel & Source drill-down took 6-8s to load every single time). Root
# cause: _upcoming_event_outlook() scans the full ~2.5M-row _SOURCE_DF up to
# 4 times (event + control window, x2 categories) to rank top-selling Sub
# Categories/Colors - the main Weekly Sales Report row already pays this cost
# once per request but reuses it via _WEEKLY_GRID_CACHE, while the
# drill-down's own call had no such cache and repeated the full scan from
# scratch on every click.
_FESTIVAL_OUTLOOK_CACHE: tuple[int, date, int, "FestivalOutlook"] | None = None

# Forecast-model status: "lightgbm" once model forecasts are folded into the
# plan, "naive" only on a cold start with no saved model.
ACTIVE_FORECAST_MODEL = "naive"
# Snapshot date (ISO) of the model forecasts currently served ("" = none).
FORECAST_SNAPSHOT = ""
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
    global PLAN_ROWS, _WEEK_ACTUAL, _DAY_ACTUAL, _FC_WEEKLY, _NAIVE_WEEK
    global TOP_STATES, TOP_CITIES, TOP_WAREHOUSES, PLAN_BY_SKU, _DATA_VERSION
    rows, week_actual, day_actual, fc_weekly, naive_week, states, cities, warehouses = result
    PLAN_ROWS, _WEEK_ACTUAL, _DAY_ACTUAL, _FC_WEEKLY, _NAIVE_WEEK = (
        rows, week_actual, day_actual, fc_weekly, naive_week
    )
    TOP_STATES, TOP_CITIES, TOP_WAREHOUSES = states, cities, warehouses
    PLAN_BY_SKU = {r.skuCode: r for r in rows}
    # Bumped on every publish (initial load, daily auto-refresh, LGBM
    # upgrade, manual /admin/refresh) so response caches keyed on it
    # (see get_weekly_grid's _WEEKLY_GRID_CACHE) invalidate themselves
    # automatically instead of needing an explicit "clear cache" call here.
    _DATA_VERSION += 1
    _WEEKLY_GRID_CACHE.clear()


# --------------------------------------------------------------------------- #
# Serving snapshot (2026-10-02): the daily job (job.py) builds everything with
# the full data + models, then saves just what the endpoints read into one
# file, so a small API can serve the dashboard by loading that file instead of
# 2.6M sales rows + model training. See save/load_serving_snapshot().
# --------------------------------------------------------------------------- #
SERVING_SNAPSHOT_FILE = "serving_snapshot.pkl"
_SNAPSHOT_VARS = (
    "SNAPSHOT_DATE", "PLAN_ROWS", "PLAN_BY_SKU", "_WEEK_ACTUAL", "_DAY_ACTUAL",
    "_WEEK_ACTUAL_GROSS", "_DAY_ACTUAL_GROSS", "_FC_WEEKLY", "_FC_WEEKLY_DESIGN",
    "_NAIVE_WEEK", "_ACTIVE_FORECASTS", "ACTIVE_FORECAST_MODEL", "FORECAST_SNAPSHOT",
    "OVERALL_ACCURACY", "LATEST_WEEK_ACCURACY", "LATEST_WEEK_ISO", "SKU_ACCURACY",
    "SKU_MODEL_HIST", "DESIGN_ACCURACY", "DESIGN_MODEL_HIST", "TIER_ACCURACY",
    "HORIZON_ACCURACY", "STYLE_LIFECYCLE", "TOP_STATES", "TOP_CITIES", "TOP_WAREHOUSES",
    "_DESIGN_LEAD", "_GLOBAL_LEAD", "_DESIGN_SECTION",
)
# The only _SOURCE_DF columns the endpoints read (festival outlook, spike
# lead time, Channel & Source drill-down) - all of them on Gross rows only.
_SERVING_SOURCE_COLS = ["order_date", "order_status", "qty", "DESIGN_NO", "channel_name", "source"]


def _serving_cache_path() -> Path:
    return Path(__file__).resolve().parent / ".cache" / SERVING_SNAPSHOT_FILE


def save_serving_snapshot(path: Path | None = None) -> Path:
    """Write every module-level table the endpoints read, plus a slim copy of
    the sales rows (Gross rows, _SERVING_SOURCE_COLS, deduplicated against the
    full row first) and the calibrated festival effects, to one pickle."""
    import pickle

    src = None
    if _SOURCE_DF is not None:
        full = _SOURCE_DF.drop_duplicates()
        gross = full["order_status"].astype(str).str.strip().str.lower().isin(_GROSS_SALE_STATUSES)
        src = full.loc[gross, [c for c in _SERVING_SOURCE_COLS if c in full.columns]].reset_index(drop=True)
        # A unique row id, so the endpoints' own drop_duplicates() calls can
        # never merge two distinct orders that look alike in these columns.
        src["_row"] = range(len(src))
    state = {name: globals()[name] for name in _SNAPSHOT_VARS}
    state["_SOURCE_DF"] = src
    state["festival_effects"] = festival.effects()
    path = path or _serving_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(state, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    print(f"[data] serving snapshot saved: {path.name} ({path.stat().st_size / 1e6:.0f} MB, "
          f"{len(PLAN_ROWS):,} SKUs, {0 if src is None else len(src):,} sales rows)", file=sys.stderr)
    return path


def load_serving_snapshot(path: Path | None = None) -> bool:
    """Restore save_serving_snapshot()'s file into this process (no BigQuery,
    no training) and re-warm the response caches. False if there is none."""
    import pickle
    global _SOURCE_DF, _DATA_VERSION, _CHANNEL_SOURCE_BASE_CACHE, _FESTIVAL_OUTLOOK_CACHE

    path = path or _serving_cache_path()
    if not path.exists():
        return False
    with open(path, "rb") as fh:
        state = pickle.load(fh)
    globals().update({name: state[name] for name in _SNAPSHOT_VARS if name in state})
    _SOURCE_DF = state.get("_SOURCE_DF")
    with festival._LOCK:
        festival._EFFECTS = dict(state.get("festival_effects") or festival._EFFECTS)
        festival._DAY_CACHE.clear()
    _DATA_VERSION += 1
    _WEEKLY_GRID_CACHE.clear()
    _CHANNEL_SOURCE_BASE_CACHE = None
    _FESTIVAL_OUTLOOK_CACHE = None
    print(f"[data] serving snapshot loaded: data through {SNAPSHOT_DATE}, "
          f"model {ACTIVE_FORECAST_MODEL} ({FORECAST_SNAPSHOT or 'none'})", file=sys.stderr)
    return True


def _snapshot_of(df) -> date:
    """The SNAPSHOT_DATE _load_real_plan() will publish for ``df`` (latest real
    order date, capped at today), computed up-front so rebuild() can look up
    that snapshot's saved model forecasts BEFORE building the plan."""
    import pandas as pd
    od = df.loc[df["product_sku_code"].notna(), "order_date"]
    od = od[od <= pd.Timestamp.today().normalize()]
    return pd.Timestamp(od.max()).normalize().date()


def _daily_gross_units(df):
    """Total GROSS units per calendar day (the model's own training target,
    see lgbm_forecast._GROSS_SALE_NORM) - festival.calibrate()'s input."""
    import lgbm_forecast
    rows = df.dropna(subset=["product_sku_code", "order_date"]).drop_duplicates()
    rows = rows[rows["order_status"].astype(str).str.strip().str.lower().isin(lgbm_forecast._GROSS_SALE_NORM)]
    return rows.groupby(rows["order_date"].dt.normalize())["qty"].sum().astype(float)


def _shift_weekly(obj, weeks: int):
    """Drop the first ``weeks`` entries of every "weekly" list in a saved
    forecast (SKU/design ``{key: {"weekly": [...]}}``), so an OLDER
    snapshot's week 0 lines up with the current snapshot's week 0. Weeks
    past the shortened series fall back to the naive run-rate in
    _adjusted_weekly()."""
    if not weeks or not isinstance(obj, dict):
        return obj
    return {
        k: (v[weeks:] if k == "weekly" and isinstance(v, list) else _shift_weekly(v, weeks))
        for k, v in obj.items()
    }


# How old a previous snapshot's saved model forecast may be and still be
# served while the current snapshot's model trains (older -> naive instead).
_PREVIOUS_MODEL_MAX_AGE_DAYS = 28


def _load_saved_forecasts(kind: str, snap: date) -> tuple[dict | None, str, bool]:
    """(forecasts, source snapshot ISO, up_to_date) for ``kind``
    ("sku"/"design") from disk: the newest saved forecast with
    snapshot <= ``snap`` and within _PREVIOUS_MODEL_MAX_AGE_DAYS, of any
    cache version, week-shifted to line up with ``snap``. ``up_to_date`` is
    True only for ``snap``'s own forecast of the CURRENT cache version -
    anything else is served as a stand-in while the model retrains.
    (None, "", False) when nothing usable is saved."""
    import json as _json
    import lgbm_forecast
    found = lgbm_forecast.latest_cached_file(kind, snap.isoformat())
    if found is None:
        return None, "", False
    src, path, current_version = found
    src_date = date.fromisoformat(src)
    if (snap - src_date).days > _PREVIOUS_MODEL_MAX_AGE_DAYS:
        return None, "", False
    try:
        forecasts = _json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — a corrupt file is just "nothing saved"
        return None, "", False
    if not forecasts:
        return None, "", False
    shift = (_forecast_week_start(snap, 0) - _forecast_week_start(src_date, 0)).days // 7
    return _shift_weekly(forecasts, shift), src, (current_version and src == snap.isoformat())


def _publish_model_plan(df, forecasts: dict, design_forecasts: dict | None,
                        forecast_snapshot: str) -> None:
    """Fold the model's forecasts into the published plan. The model and the
    naive run-rate are never shown as two competing numbers: every SKU's
    weekly forecast is FORECAST_BLEND * model + (1 - blend) * its own naive
    run-rate (_adjusted_weekly()), and naive alone only fills in SKUs/weeks
    the model doesn't cover."""
    global ACTIVE_FORECAST_MODEL, _ACTIVE_FORECASTS, _FC_WEEKLY_DESIGN, FORECAST_SNAPSHOT
    _FC_WEEKLY_DESIGN = design_forecasts or {}
    _ACTIVE_FORECASTS = forecasts
    _set_plan(_load_real_plan(df, forecasts=forecasts, design_forecasts=_FC_WEEKLY_DESIGN))
    ACTIVE_FORECAST_MODEL = "lightgbm"
    FORECAST_SNAPSHOT = forecast_snapshot


def _train_and_publish(df, snap: date) -> None:
    """Background step: train whichever of the SKU / design models
    has no saved forecast for ``snap`` yet, save it, then publish. Whatever
    rebuild() already published (the previous snapshot's model, or naive on
    a cold start) keeps serving until this finishes, and stays in place on
    any failure.

    Design-level (style) model: a second, independently-trained model at
    DESIGN_NO grain, measurably more accurate than summing SKU-level
    forecasts for a design's total demand (see compute_design()'s
    docstring). A failure there keeps the design forecasts already being
    served, and never undoes the SKU-level model."""
    try:
        import lgbm_forecast
        s = snap.isoformat()
        forecasts = lgbm_forecast.load_cache(s)
        if forecasts is None:
            print("[data] training LightGBM in background…", file=sys.stderr)
            forecasts = lgbm_forecast.compute(df)
            if forecasts:
                lgbm_forecast.save_cache(s, forecasts)
        if not forecasts:
            print("[data] LightGBM produced no forecasts; keeping current plan", file=sys.stderr)
            return

        design_forecasts = _FC_WEEKLY_DESIGN
        try:
            fresh = lgbm_forecast.load_design_cache(s)
            if fresh is None:
                print("[data] training design-level model in background…", file=sys.stderr)
                fresh = lgbm_forecast.compute_design(df)
                if fresh:
                    lgbm_forecast.save_design_cache(s, fresh)
            design_forecasts = fresh or design_forecasts
            print(f"[data] design-level model active ({len(design_forecasts):,} designs)", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — design-level is a bonus signal, not required
            print(f"[data] design-level model failed ({exc!r}); keeping current design forecasts", file=sys.stderr)

        _publish_model_plan(df, forecasts, design_forecasts, s)
        # Re-warm (2026-09-26, user-requested) - _set_plan() bumped
        # _DATA_VERSION and evicted rebuild()'s warm-up.
        _warm_caches()
        _record_weekly_production()
        import cache_sync
        cache_sync.upload_changed()
        print(f"[data] LightGBM active ({len(forecasts):,} SKUs, snapshot {s})", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — never let forecasting kill the API
        print(f"[data] model training failed ({exc!r}); keeping current plan", file=sys.stderr)


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


def _warm_caches() -> None:
    """Pre-build every response cache that would otherwise pay its cost on
    whichever request happens to hit it first (2026-09-26, user-requested:
    "store the cache at the start of the app so it's not take the time to
    load" — the Channel & Source drill-down's first click after a
    restart/refresh was measured at 6-9s purely from _channel_source_base_df()
    and get_festival_outlook() building cold, see those functions'
    docstrings). Called once at the end of rebuild(), after _set_plan() has
    published the new PLAN_ROWS/_SOURCE_DF/_DATA_VERSION these caches key off
    of - covers startup, the daily auto-refresh, AND a manual /admin/refresh
    alike, since all three go through rebuild(). Never allowed to fail loudly:
    a warm-up miss just means the first real request pays the cost instead,
    exactly like today, not a broken app."""
    try:
        _channel_source_base_df()
        get_festival_outlook()
        # Matches the Weekly Sales Report's own default request exactly
        # (groupBy=style, weeks=9, PAGE_SIZE=200, no filters) - see
        # WeeklySalesGrid.tsx's useWeeklyGrid() call - so the page a user
        # actually lands on is already cached by the time they load it.
        get_weekly_grid("style", 9, 200, 0, "", "", "")
        # Also records the Sub Category rows' running-week forecasts (see
        # forecast_freeze.py) even on days nobody opens that grouping.
        get_weekly_grid("subCategory", 9, 200, 0, "", "", "")
    except Exception as exc:  # noqa: BLE001 — a slow first request beats a broken rebuild
        print(f"[data] cache warm-up failed ({exc!r})", file=sys.stderr)


def rebuild() -> dict:
    """Fetch the source data and rebuild every in-memory table.

    Model-first (2026-09-28, user-requested: the dashboard showed the naive
    number for a few minutes after every restart/refresh, then jumped to
    the model's): the plan is published ONCE, with model forecasts already
    folded in, whenever any are saved on disk - this snapshot's own, or the
    newest previous one (week-shifted, see _load_saved_forecasts()) while
    this snapshot's model trains in the background and replaces it. Naive
    alone is served only on a genuine cold start (no saved model at all).
    Called once at import, by the daily auto-refresh, and by
    ``POST /admin/refresh``. Falls back to mock data on failure.
    """
    global _SOURCE_DF, ACTIVE_FORECAST_MODEL, _LGBM_THREAD, FORECAST_SNAPSHOT
    global _ACTIVE_FORECASTS, _FC_WEEKLY_DESIGN
    try:
        df = _source_dataframe()
        _SOURCE_DF = df
        source = "live" if DATA_SOURCE == "live" else "csv"
        # Re-fit festival/sale multipliers on the latest sales BEFORE the
        # plan (and any training) reads them - see festival.calibrate().
        festival.calibrate(_daily_gross_units(df))
        forecasts = None
        needs_training = False
        if FORECAST_MODEL == "lgbm":
            snap = _snapshot_of(df)
            forecasts, sku_snap, sku_ok = _load_saved_forecasts("sku", snap)
            design_forecasts, _design_snap, design_ok = _load_saved_forecasts("design", snap)
            needs_training = not (sku_ok and design_ok)
            if forecasts:
                _publish_model_plan(df, forecasts, design_forecasts, sku_snap)
                print(f"[data] model forecasts from snapshot {sku_snap} active "
                      f"({len(forecasts):,} SKUs)", file=sys.stderr)
        if not forecasts:
            # Clear any previous run's model state so a stale design
            # forecast can't leak into the naive plan.
            _ACTIVE_FORECASTS, _FC_WEEKLY_DESIGN = None, {}
            _set_plan(_load_real_plan(df))
            ACTIVE_FORECAST_MODEL = "naive"
            FORECAST_SNAPSHOT = ""
        _warm_caches()
        if forecasts and not needs_training:
            _record_weekly_production()
        if needs_training and not (_LGBM_THREAD and _LGBM_THREAD.is_alive()):
            _LGBM_THREAD = threading.Thread(target=_train_and_publish, args=(df, snap), daemon=True)
            _LGBM_THREAD.start()
        import cache_sync
        cache_sync.upload_changed()
    except Exception as exc:  # noqa: BLE001 — never let data issues kill startup
        print(f"[data] real load failed ({exc!r}); falling back to mock", file=sys.stderr)
        _set_plan((
            _MOCK_ROWS, {}, {}, {}, {},
            [TopRegion(**d) for d in _aggregate("state")],
            [TopRegion(**d) for d in _aggregate("city")],
            [TopWarehouse(**d) for d in _aggregate("warehouse")],
        ))
        source = "mock"

    return {
        "source": source,
        "rows": len(PLAN_ROWS),
        "snapshot": SNAPSHOT_DATE.isoformat(),
        **model_status(),
    }


def model_status() -> dict:
    """Forecast-model fields shared by rebuild()'s result and GET /health."""
    return {
        "forecastModel": ACTIVE_FORECAST_MODEL,
        # Snapshot the served model was trained on - older than "snapshot"
        # while that snapshot's own model is still training.
        "forecastSnapshot": FORECAST_SNAPSHOT,
        "modelTraining": bool(_LGBM_THREAD and _LGBM_THREAD.is_alive()),
        "lgbmPending": FORECAST_MODEL == "lgbm" and ACTIVE_FORECAST_MODEL != "lightgbm",
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
        # The sleep itself must be inside the guard too, not just rebuild() —
        # an uncaught exception here (e.g. from a system suspend/resume
        # interrupting the sleep) would otherwise kill this whole daemon
        # thread silently: no log line, no restart, no more daily refreshes
        # for the rest of the process's life (observed 2026-08-04: the
        # scheduler never even printed its wake-up message after ~17h of
        # total stderr silence, despite the process itself staying up).
        try:
            time.sleep(_seconds_until(REFRESH_HOUR, REFRESH_MINUTE))
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


def get_subcategory_options() -> list[str]:
    """Every distinct Sub Category across the FULL plan, sorted - computed
    against the full PLAN_ROWS rather than any paginated page, since a Sub
    Category whose SKUs are all low/uncertain forecast (e.g. slow movers)
    could otherwise be entirely absent from a page and silently dropped from
    the filter dropdown."""
    return sorted({r.subCategory for r in PLAN_ROWS if r.subCategory})


def get_category_options() -> list[str]:
    """Every distinct top-level Category across the FULL plan, sorted - same
    full-PLAN_ROWS rationale as get_subcategory_options() above (2026-09-21,
    user-requested Category filter)."""
    return sorted({r.category for r in PLAN_ROWS if r.category})


def get_category_subcategory_map() -> dict[str, list[str]]:
    """{category: [its own Sub Categories, sorted]} across the FULL plan
    (2026-09-23, user-requested: picking a Category should narrow the Sub
    Category dropdown to only the ones that actually belong to it, instead
    of always listing every Sub Category regardless of which Category is
    selected). Same full-PLAN_ROWS rationale as get_subcategory_options()."""
    out: dict[str, set[str]] = {}
    for r in PLAN_ROWS:
        if r.category and r.subCategory:
            out.setdefault(r.category, set()).add(r.subCategory)
    return {cat: sorted(subs) for cat, subs in out.items()}


def _week_axis(weeks: int) -> tuple[list[date], list[date]]:
    """(historical Mondays, forecast Mondays) - the date grid every row of a
    breakdown/grid shares, independent of any particular SKU/group's own
    numbers. See _build_week_points() for why the historical side is anchored
    to the REAL current date rather than SNAPSHOT_DATE."""
    today = date.today()
    last_monday = today - timedelta(days=today.weekday())
    hist_start_monday = _BREAKDOWN_HIST_START - timedelta(days=_BREAKDOWN_HIST_START.weekday())
    weeks_back = max(0, (last_monday - hist_start_monday).days // 7)
    # range(..., -1, -1) includes k=0: the snapshot's own week, still in
    # progress as of SNAPSHOT_DATE. Without it, the chart jumps straight from
    # the last COMPLETE week to next week's forecast, skipping "now" entirely.
    historical_mondays = [last_monday - timedelta(days=7 * k) for k in range(weeks_back, -1, -1)]
    # Forecast weeks are computed independently from SNAPSHOT_DATE, which can
    # lag the real "today" by anywhere from 0 to a few days (ERP-to-BigQuery
    # sync delay). When that lag is small enough, SNAPSHOT_DATE's own "next
    # forecast week" can land on the SAME Monday as historical_mondays' last
    # (current, partial) entry - filter those out so a week never appears as
    # both a historical AND a forecast column (verified 2026-09-14:
    # SNAPSHOT_DATE was a Sunday only one day behind the real Monday "today",
    # producing exactly this collision - two grid columns sharing one date).
    forecast_mondays = [
        d for d in (_forecast_week_start(SNAPSHOT_DATE, w) for w in range(weeks))
        if d > historical_mondays[-1]
    ]
    return historical_mondays, forecast_mondays


def _build_week_points(
    weeks: int, naive_week: float, wk_actual: dict[str, int], series: list[float] | None,
    model_hist: dict[str, float],
) -> tuple[list[ForecastPoint], list[HistoricalPoint]]:
    """Shared week-wise forecast + historical construction for get_breakdown()
    (one SKU), get_subcategory_breakdown() (a Sub Category's SKUs pooled),
    and get_weekly_grid() (every Sub Category/Style at once) — same shape
    either way, just built from a single SKU's own series/actuals or an
    already-summed one.

    The future forecast is the SAME festival-adjusted, LightGBM+run-rate-blended
    WEEKLY series the plan's headline horizons are summed from, so the chart and
    the headline reconcile (and both reflect festival / sale spikes). Each point's
    date is the Monday start of its week.

    Always live (2026-09-28, user-requested): values are recomputed from the
    current model + data on every rebuild - no frozen snapshot."""
    historical_mondays, forecast_mondays = _week_axis(weeks)

    # --- future forecast (weekly) ------------------------------------------- #
    forecast: list[ForecastPoint] = []
    for w, ws in enumerate(forecast_mondays):
        mult, event = _week_festival(ws)
        qty = max(0, round(series[w])) if (series is not None and w < len(series)) \
            else max(0, round(naive_week * mult))
        forecast.append(ForecastPoint(date=ws.isoformat(), qty=qty, event=event))

    # --- historical: real weekly units vs what was actually predicted -------- #
    # Wherever a genuine backtested model prediction exists for a week (recent
    # weeks only — see SKU_MODEL_HIST), show that instead of the naive
    # baseline, so this chart/table reflects the real deployed model rather
    # than a naive-run-rate proxy.
    #
    # Anchored to the REAL current date, not SNAPSHOT_DATE (the latest
    # order_date actually loaded) — SNAPSHOT_DATE lags "today" by the
    # ERP-to-BigQuery sync delay (often a day or more), so anchoring here
    # would leave the table with no row at all for the actual current week
    # until sync catches up (verified 2026-08-17: SNAPSHOT_DATE sat on
    # 2026-08-16, a Sunday, so SNAPSHOT_DATE's own week — Aug 10-16 — was
    # already complete, and the real current week, Aug 17-23, never
    # appeared). `partial` below still keys off SNAPSHOT_DATE, so a week
    # with no synced data yet correctly shows as "0 so far", not hidden.
    historical: list[HistoricalPoint] = []
    for ws in historical_mondays:
        actual = int(wk_actual.get(ws.isoformat(), 0))
        if ws.isoformat() in model_hist:
            exp = max(0, round(model_hist[ws.isoformat()]))
        else:
            mult, _ = _week_festival(ws)
            exp = max(0, round(naive_week * mult))
        # A week is only genuinely partial if SNAPSHOT_DATE falls BEFORE its
        # last day (Sunday) — not just "is this the newest bucket" (k == 0).
        # Those usually coincide, but not when SNAPSHOT_DATE (the latest
        # order_date actually loaded) happens to land exactly on a Sunday:
        # that week's data is already complete, yet the old `k == 0` check
        # flagged it partial anyway, hiding a real, full week of actuals from
        # the chart (verified 2026-08-17: snapshot 2026-08-16 is a Sunday,
        # so the Aug 10-16 week was wrongly hidden despite having a real
        # actual=261 already).
        partial = (ws + timedelta(days=6)) > SNAPSHOT_DATE
        historical.append(
            HistoricalPoint(
                date=ws.isoformat(), forecast=exp, actual=actual, variance=actual - exp,
                partial=partial,
            )
        )
    return forecast, historical


def _aggregate_group_series(
    skus: list[str], gross: bool = False,
) -> tuple[float, dict[str, int], list[float] | None, dict[str, float]]:
    """(naive_week, wk_actual, series, model_hist) pooled across ``skus`` -
    shared aggregation used by get_subcategory_breakdown() and
    get_weekly_grid() to build a multi-SKU group's numbers the same way
    get_breakdown() builds a single SKU's. ``gross=True`` sources wk_actual
    from _WEEK_ACTUAL_GROSS instead of _WEEK_ACTUAL (2026-09-15,
    user-requested: the Weekly Sales Report shows Gross, not Net, as
    "Actual Sale").

    ``series``/``model_hist`` are built PER-DESIGN, preferring each pooled
    design's own design-level model output over summing its SKUs' own
    series/backtested predictions design-by-design (2026-09-18,
    user-requested demand-forecasting audit) — see
    _design_weekly_series_preferred (future) and _design_model_hist_preferred
    (historical/backtested). Previously this always summed raw SKU-level
    output even though compute_design()'s own docstring says design grain is
    measurably more accurate — the design-level model was trained every
    night and used ONLY for the festival-spike badge, never for the actual
    Style/Sub Category numbers shown here. SKUs whose design can't be
    resolved (rare — see PLAN_BY_SKU) fall back to being summed individually,
    same as before."""
    naive_week = sum(_NAIVE_WEEK.get(s, 0.0) for s in skus)

    by_design: dict[str, list[str]] = {}
    no_design: list[str] = []
    for s in skus:
        row = PLAN_BY_SKU.get(s)
        d = row.designNo if row else None
        if d:
            by_design.setdefault(d, []).append(s)
        else:
            no_design.append(s)

    weekly = [0.0] * _FC_WEEKS
    has_series = False
    for design_no, dskus in by_design.items():
        dseries = _design_weekly_series_preferred(design_no, dskus, _FC_WEEKLY, _FC_WEEKLY_DESIGN, _NAIVE_WEEK)
        if dseries is None:
            continue
        has_series = True
        for i, v in enumerate(dseries[:_FC_WEEKS]):
            weekly[i] += v
    if no_design:
        rest = _design_weekly_series(no_design, _FC_WEEKLY)
        if rest is not None:
            has_series = True
            for i, v in enumerate(rest[:_FC_WEEKS]):
                weekly[i] += v
    series = weekly if has_series else None

    wk_actual_source = _WEEK_ACTUAL_GROSS if gross else _WEEK_ACTUAL
    wk_actual: dict[str, int] = {}
    for s in skus:
        for wk_iso, q in wk_actual_source.get(s, {}).items():
            wk_actual[wk_iso] = wk_actual.get(wk_iso, 0) + q

    # Sums whatever backtested predictions exist per week, per design
    # (preferring DESIGN_MODEL_HIST over summed SKU_MODEL_HIST — see
    # _design_model_hist_preferred) across whichever designs have one — a
    # week where only some designs have a scoreable prediction slightly
    # understates the pooled total for that week (the rest fall back to
    # naive individually rather than being summed in here too), but in
    # practice a snapshot's backtest covers every forecastable design/SKU at
    # once, so this is a rare, small edge case.
    model_hist: dict[str, float] = {}
    for design_no, dskus in by_design.items():
        for wk_iso, v in _design_model_hist_preferred(design_no, dskus, SKU_MODEL_HIST, DESIGN_MODEL_HIST).items():
            model_hist[wk_iso] = model_hist.get(wk_iso, 0.0) + v
    for s in no_design:
        for wk_iso, v in SKU_MODEL_HIST.get(s, {}).items():
            model_hist[wk_iso] = model_hist.get(wk_iso, 0.0) + v

    return naive_week, wk_actual, series, model_hist


def _aggregate_group_day_actual(skus: list[str], gross: bool = False) -> dict[str, int]:
    """Per-CALENDAR-DAY actual sales pooled across ``skus`` - the daily-grain
    counterpart of _aggregate_group_series' wk_actual, used only to build the
    Weekly Sales Report's calendar-day week cells (see get_weekly_grid()).
    ``gross=True`` sources from _DAY_ACTUAL_GROSS instead of _DAY_ACTUAL
    (2026-09-15, user-requested: the Weekly Sales Report shows Gross, not
    Net, as "Actual Sale")."""
    source = _DAY_ACTUAL_GROSS if gross else _DAY_ACTUAL
    day_actual: dict[str, int] = {}
    for s in skus:
        for d_iso, q in source.get(s, {}).items():
            day_actual[d_iso] = day_actual.get(d_iso, 0) + q
    return day_actual


def _month_chunks(year: int, month: int) -> list[tuple[date, date]]:
    """One month's fixed calendar-day week buckets: 1-7 (W1), 8-15 (W2),
    16-23 (W3), 24-end (W4) - ALWAYS exactly 4 buckets, W1 always starting on
    the 1st regardless of which weekday it falls on (2026-09-17,
    user-specified split: "W1: 1-7, W2: 8-15, W3: 16-23, W4: 24-end" -
    previously "1-8, 9-15, 16-22, 23-end"). This is deliberately NOT the
    Mon-anchored ISO week used everywhere else in this app (_week_axis, the
    forecasting model itself, SKU/Sub Category drill-downs) - see
    get_weekly_grid()'s docstring."""
    last_day = calendar.monthrange(year, month)[1]
    return [
        (date(year, month, 1), date(year, month, 7)),
        (date(year, month, 8), date(year, month, 15)),
        (date(year, month, 16), date(year, month, 23)),
        (date(year, month, 24), date(year, month, last_day)),
    ]


def _calendar_chunk_axis(weeks: int) -> list[tuple[date, date]]:
    """Every fixed calendar-day week bucket (see _month_chunks) from
    _BREAKDOWN_HIST_START (2025-04-01, itself a month's 1st - no ragged
    leading partial bucket) through the end of the month containing the
    forecast horizon's last day. Reuses _week_axis' Mon-Sun week list only to
    find that end date; the actual grid this returns is calendar-day-based,
    independent of weekday alignment."""
    historical_mondays, forecast_mondays = _week_axis(weeks)
    horizon_end = (forecast_mondays[-1] if forecast_mondays else historical_mondays[-1]) + timedelta(days=6)
    chunks: list[tuple[date, date]] = []
    y, m = _BREAKDOWN_HIST_START.year, _BREAKDOWN_HIST_START.month
    while (y, m) <= (horizon_end.year, horizon_end.month):
        chunks.extend(_month_chunks(y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return chunks



def _detect_spike_lead_days(event_name: str, hist_start: date) -> int | None:
    """How many days BEFORE ``hist_start`` (a historical Festival/Sale
    occurrence) real daily sales started a sustained rise above their normal
    baseline - "how many days before the event does demand start spiking"
    (2026-09-13, user-requested).

    Baseline = average daily sales over a "quiet" window well before the
    event (_SPIKE_BASELINE_WINDOW days out, i.e. far enough back to predate
    any pre-event ramp), EXCLUDING any day inside a DIFFERENT known
    festival_calendar event so a nearby event's own spike can't inflate what
    should be a "normal" reference level (see _SPIKE_BASELINE_WINDOW's
    comment for the real example this fixed). Scanning forward from
    _SPIKE_LOOKBACK_DAYS days out to the day before the event, returns the
    FIRST day whose OWN trailing-3-day average first crosses
    _SPIKE_THRESHOLD_RATIO x baseline - a first-crossing detector, not "stays
    elevated forever after" (real daily sales are noisy enough that
    requiring every later day to also clear the bar would make a single
    quiet day invalidate an otherwise real ramp). None if the clean baseline
    is too thin to trust, or no day ever crosses it."""
    if _SOURCE_DF is None:
        return None
    import pandas as pd

    lookback_start = hist_start - timedelta(days=_SPIKE_LOOKBACK_DAYS)
    mask = (
        (_SOURCE_DF["order_date"] >= pd.Timestamp(lookback_start))
        & (_SOURCE_DF["order_date"] < pd.Timestamp(hist_start))
        # GROSS, not net (2026-09-15, user-requested) - matches the rest of
        # this page's "Actual Sale" now being Gross.
        & _SOURCE_DF["order_status"].astype(str).str.strip().str.lower().isin(_GROSS_SALE_STATUSES)
    )
    window = _SOURCE_DF.loc[mask].drop_duplicates()
    if window.empty:
        return None

    daily_actual = window.groupby(window["order_date"].dt.date)["qty"].sum()
    days = [lookback_start + timedelta(days=i) for i in range(_SPIKE_LOOKBACK_DAYS)]
    series = pd.Series([float(daily_actual.get(d, 0)) for d in days], index=days)

    far, near = _SPIKE_BASELINE_WINDOW
    baseline_days = [
        d for d in days
        if -far <= (d - hist_start).days <= -near and not festival_calendar.is_event_day(d, exclude_name=event_name)
    ]
    if len(baseline_days) < _MIN_SPIKE_BASELINE_DAYS:
        return None
    baseline = float(series.loc[baseline_days].mean())
    if baseline < _MIN_SPIKE_BASELINE_DAILY:
        return None

    threshold_level = baseline * _SPIKE_THRESHOLD_RATIO
    rolling = series.rolling(3, min_periods=1).mean()
    for d in days:
        offset = (d - hist_start).days
        if offset >= 0:
            break
        if offset <= -near:
            # Still inside (or before) the baseline window itself - not a
            # candidate ramp-start day. Also skips the earliest few lookback
            # days, whose 3-day rolling average is under-smoothed (1-2 real
            # data points) and prone to a false trigger right at the edge of
            # the scan window.
            continue
        if rolling.loc[d] >= threshold_level:
            return -offset
    return None


def _upcoming_event_outlook(category: str, top_n: int) -> UpcomingEventOutlook | None:
    """The next upcoming event of ``category`` ("festival" or "sale"), plus
    the top-selling Sub Categories during that SAME event's most recent past
    occurrence, from REAL actual sales (not a forecast).

    Sourced from festival_calendar.py (the real Indian festival + e-commerce
    sale calendar CSV, 2026-09-13 user-requested) rather than festival.py's
    own hand-curated/partly-estimated windows - see that module's docstring
    for why this is kept separate from festival.py's actual demand-multiplier
    system. ``None`` if no event of that category is found (shouldn't
    normally happen inside the sheet's 2025-2028 coverage) or
    ``_SOURCE_DF`` isn't loaded yet.

    "Upcoming" includes a currently-in-progress event, not just ones that
    haven't started (festival_calendar.upcoming() only returns events whose
    END hasn't passed) - matches how a user would read "upcoming" on the
    day of."""
    if _SOURCE_DF is None:
        return None
    import pandas as pd

    today = date.today()
    # Every event of this category running at the SAME TIME as the primary
    # upcoming one, not just that one alone (2026-09-15, user-requested:
    # "what if there are multiple sales at once in the same date range" -
    # e.g. two platforms both running a sale the same week). Merged into one
    # combined outlook below rather than shown as separate cards, so this
    # needs no model/UI changes: the upcoming AND historical windows both
    # widen to the union of every concurrent event's own range, which is
    # exactly what _event_boost_windows() and the top-selling/color
    # aggregation already key off.
    group = festival_calendar.overlapping_upcoming(category, today)
    if not group:
        return None
    name = group[0][0]  # primary (earliest-starting) event's raw name - used for spike-lead-time below
    start = min(s for _n, s, _e in group)
    end = max(e for _n, _s, e in group)
    # e.g. "Meesho Mega Blockbuster Sale (Meesho)" -> "Meesho Mega Blockbuster
    # Sale" for display - the trailing "(Festival)"/platform tag is only
    # needed to match the SAME event name across years below, not to show
    # the user (redundant next to a panel already labelled "UPCOMING SALE").
    # Concurrent events are joined so a genuinely overlapping second sale
    # isn't silently dropped from the card.
    seen_names: list[str] = []
    for n, _s, _e in group:
        dn = n.rsplit(" (", 1)[0]
        if dn not in seen_names:
            seen_names.append(dn)
    display_name = " + ".join(seen_names)

    # Most recent PAST occurrence of EACH concurrent event's own same name,
    # merged the same way (union of their individual historical windows) -
    # real actual sales during any of them count toward "what sold well
    # during this stretch", not just the primary event's own history.
    hist_ranges = []
    for n, s, _e in group:
        h = festival_calendar.most_recent_past(n, s)
        if h is not None and h[1] >= _BREAKDOWN_HIST_START:
            hist_ranges.append(h)
    hist_start = min((h[0] for h in hist_ranges), default=None)
    hist_end = max((h[1] for h in hist_ranges), default=None)

    if hist_start is None:
        return UpcomingEventOutlook(
            eventName=display_name, eventStart=start.isoformat(), eventEnd=end.isoformat(),
            historicalYear=None, topSubCategories=[], topColors=[],
        )

    # GROSS, not net (2026-09-15, user-requested) - matches the rest of this
    # page's "Actual Sale" now being Gross (includes returns/RTO, excludes
    # only Cancelled/Cancelled Before Shipping).
    mask = (
        (_SOURCE_DF["order_date"] >= pd.Timestamp(hist_start))
        & (_SOURCE_DF["order_date"] <= pd.Timestamp(hist_end))
        & _SOURCE_DF["order_status"].astype(str).str.strip().str.lower().isin(_GROSS_SALE_STATUSES)
    )
    window_sales = _SOURCE_DF.loc[mask].drop_duplicates()

    # Regular (non-event) baseline per Sub Category (2026-09-14,
    # user-requested "high sale" color-coding): the SAME sub-category's real
    # sales over an equal-length control period immediately BEFORE the
    # event, so "high" means genuinely elevated vs. that sub-category's own
    # normal level, not just whichever sub-category sold the most overall
    # (a naturally big sub-category would always top the list even with no
    # real festival lift).
    event_days = (hist_end - hist_start).days + 1
    control_end = hist_start - timedelta(days=1)
    control_start = control_end - timedelta(days=event_days - 1)
    control_mask = (
        (_SOURCE_DF["order_date"] >= pd.Timestamp(control_start))
        & (_SOURCE_DF["order_date"] <= pd.Timestamp(control_end))
        & _SOURCE_DF["order_status"].astype(str).str.strip().str.lower().isin(_GROSS_SALE_STATUSES)
    )
    control_sales = _SOURCE_DF.loc[control_mask].drop_duplicates()
    control_totals = pd.Series(dtype="float64")
    control_color_totals = pd.Series(dtype="float64")
    if not control_sales.empty:
        control_subcats = control_sales["DESIGN_NO"].map(design_attributes.sub_category_of)
        control_totals = control_sales.groupby(control_subcats)["qty"].sum()
        control_colors = control_sales["DESIGN_NO"].map(design_attributes.color_of)
        control_color_sales = control_sales[control_colors != ""]
        if not control_color_sales.empty:
            control_color_totals = control_color_sales.groupby(control_colors[control_colors != ""])["qty"].sum()

    top: list[FestivalSubCategorySales] = []
    top_colors: list[FestivalColorSales] = []
    if not window_sales.empty:
        subcats = window_sales["DESIGN_NO"].map(design_attributes.sub_category_of)
        totals = window_sales.groupby(subcats)["qty"].sum().sort_values(ascending=False)
        for sc, q in totals.head(top_n).items():
            control_q = float(control_totals.get(sc, 0.0))
            # None (not 0) when there's no real control-period baseline to
            # compare against - "infinitely higher than zero" isn't a
            # meaningful percentage, so the frontend treats it as its own
            # "new/no baseline" case rather than a numeric uplift.
            uplift_pct = round((int(q) / control_q - 1) * 100) if control_q > 0 else None
            top.append(FestivalSubCategorySales(subCategory=str(sc), qty=int(q), upliftPct=uplift_pct))

        # Which Design COLOR sold best during this event (2026-09-14,
        # user-requested) - same real-actual-sales + vs-own-control-period
        # uplift methodology as topSubCategories above, just grouped by
        # design_attributes.color_of() instead. Rows with no known color
        # (color_of() == "") are dropped rather than pooled into an
        # "Unknown" bucket - that's not an actionable color to restock.
        colors = window_sales["DESIGN_NO"].map(design_attributes.color_of)
        color_sales = window_sales[colors != ""]
        if not color_sales.empty:
            color_totals = color_sales.groupby(colors[colors != ""])["qty"].sum().sort_values(ascending=False)
            for c, q in color_totals.head(top_n).items():
                control_q = float(control_color_totals.get(c, 0.0))
                uplift_pct = round((int(q) / control_q - 1) * 100) if control_q > 0 else None
                top_colors.append(FestivalColorSales(color=str(c), qty=int(q), upliftPct=uplift_pct))

    return UpcomingEventOutlook(
        eventName=display_name, eventStart=start.isoformat(), eventEnd=end.isoformat(),
        historicalYear=hist_start.year,
        historicalStart=hist_start.isoformat(), historicalEnd=hist_end.isoformat(),
        # Shown for both Festivals and Sales (2026-09-13, user-requested -
        # briefly Festival-only, reverted back to both).
        spikeLeadDays=_detect_spike_lead_days(name, hist_start),
        topSubCategories=top,
        topColors=top_colors,
    )


def get_festival_outlook(top_n: int = 5) -> FestivalOutlook:
    """The next upcoming Festival and next upcoming Sale, from the real
    Indian festival/e-commerce calendar (festival_calendar.py) - the Weekly
    Sales Report's festival outlook card. Either field can be None (no
    source data loaded yet, or no event of that category found).

    Cached per (_DATA_VERSION, today, top_n) - see _FESTIVAL_OUTLOOK_CACHE."""
    global _FESTIVAL_OUTLOOK_CACHE
    today = date.today()
    if (
        _FESTIVAL_OUTLOOK_CACHE is not None
        and _FESTIVAL_OUTLOOK_CACHE[0] == _DATA_VERSION
        and _FESTIVAL_OUTLOOK_CACHE[1] == today
        and _FESTIVAL_OUTLOOK_CACHE[2] == top_n
    ):
        return _FESTIVAL_OUTLOOK_CACHE[3]
    outlook = FestivalOutlook(
        upcomingFestival=_upcoming_event_outlook("festival", top_n),
        upcomingSale=_upcoming_event_outlook("sale", top_n),
    )
    _FESTIVAL_OUTLOOK_CACHE = (_DATA_VERSION, today, top_n, outlook)
    return outlook


def _event_boost_windows(
    ev: UpcomingEventOutlook | None, forecast_mondays: list[date], historical_mondays: list[date],
) -> tuple[set[str], list[str], list[str]] | None:
    """(upcoming forecast week ISOs overlapping ``ev``, matching historical
    week ISOs, equal-length pre-event control week ISOs) for get_weekly_grid()'s
    festival boost - or None if ``ev`` has no historical occurrence to learn
    from (nothing to compare against) or doesn't overlap the forecast horizon
    at all."""
    if ev is None or ev.historicalYear is None:
        return None
    f_start, f_end = date.fromisoformat(ev.eventStart), date.fromisoformat(ev.eventEnd)
    upcoming_weeks = {
        d.isoformat() for d in forecast_mondays
        if d <= f_end and (d + timedelta(days=6)) >= f_start
    }
    if not upcoming_weeks:
        return None
    h_start, h_end = date.fromisoformat(ev.historicalStart), date.fromisoformat(ev.historicalEnd)
    historical_weeks = [
        ws.isoformat() for ws in historical_mondays
        if ws <= h_end and (ws + timedelta(days=6)) >= h_start
    ]
    if not historical_weeks:
        return None
    span = len(historical_weeks)
    first_event_monday = date.fromisoformat(historical_weeks[0])
    control_weeks = [
        (first_event_monday - timedelta(weeks=k)).isoformat()
        for k in range(span, 0, -1)
    ]
    return upcoming_weeks, historical_weeks, control_weeks


def _apply_festival_boost(
    forecast: list[ForecastPoint], naive_week: float, wk_actual: dict[str, int],
    boost_windows: list[tuple[set[str], list[str], list[str]]],
) -> tuple[list[ForecastPoint], bool]:
    """Optimistic festival-aware forecast boost (2026-09-12, user-requested;
    factored out of _compute_weekly_grid() 2026-09-18).

    The model + festival.py's generic per-day multiplier already lift EVERY
    group's forecast during a festival/sale window by the same hand-tuned
    factor (see _adjusted_weekly()) - this layers a SECOND, GROUP-SPECIFIC
    signal on top, from this exact group's own real sales during last
    year's SAME event vs. an equal-length pre-event control window. Only
    ever RAISES a week's forecast (never lowers it), and only once the
    historical ratio clears _MIN_FESTIVAL_CONTROL_ACTUAL, so a group that's
    actually proven to spike harder than the generic multiplier assumes
    doesn't get under-forecast (and therefore under-produced) for it.

    Every window's candidate for a given week is computed FIRST and the
    largest one wins (2026-09-24, user-reported: a week can fall inside BOTH
    an upcoming Festival's and an upcoming Sale's window at once).

    Returns (possibly-boosted forecast, whether anything was actually
    raised)."""
    best_candidate: dict[str, float] = {}
    for upcoming_weeks, historical_weeks, control_weeks in boost_windows:
        fest_actual = sum(wk_actual.get(w, 0) for w in historical_weeks)
        control_actual = sum(wk_actual.get(w, 0) for w in control_weeks)
        if control_actual < _MIN_FESTIVAL_CONTROL_ACTUAL:
            continue
        ratio = min(_MAX_FESTIVAL_BOOST_RATIO, fest_actual / control_actual)
        if ratio <= 1.0:
            continue
        for week_iso in upcoming_weeks:
            candidate = naive_week * ratio
            if candidate > best_candidate.get(week_iso, 0.0):
                best_candidate[week_iso] = candidate

    if not best_candidate:
        return forecast, False

    festival_boosted = False
    boosted: list[ForecastPoint] = []
    for p in forecast:
        if p.date in best_candidate:
            candidate = max(round(best_candidate[p.date]), p.qty)
            if candidate != p.qty:
                p = ForecastPoint(date=p.date, qty=candidate, event=p.event)
                festival_boosted = True
        boosted.append(p)
    return boosted, festival_boosted


def _channel_source_base_df():
    """Deduplicated, Gross-filtered real sales rows with a per-row calendar
    month and OMS/WEBSITE source, shared by every Channel & Source drill-down
    request (2026-09-16, user-requested). Cached per _DATA_VERSION - the
    dedup + status filter over the full ~2.5M-row _SOURCE_DF is too slow to
    redo per request (same reasoning as _WEEKLY_GRID_CACHE)."""
    global _CHANNEL_SOURCE_BASE_CACHE
    if _CHANNEL_SOURCE_BASE_CACHE is not None and _CHANNEL_SOURCE_BASE_CACHE[0] == _DATA_VERSION:
        return _CHANNEL_SOURCE_BASE_CACHE[1]
    import pandas as pd

    if _SOURCE_DF is None:
        return None
    df = _SOURCE_DF.drop_duplicates()
    status_norm = df["order_status"].astype(str).str.strip().str.lower()
    df = df.loc[status_norm.isin(_GROSS_SALE_STATUSES)].copy()
    df = df.dropna(subset=["DESIGN_NO", "order_date"])
    df["_month"] = df["order_date"].dt.to_period("M")
    # source (2026-09-16): may be entirely missing on an older cached CSV
    # snapshot (added to the live BigQuery fetch this same day) - treat any
    # gap as OMS, the overwhelming majority value, rather than dropping rows.
    if "source" not in df.columns:
        df["source"] = "OMS"
    df["source"] = df["source"].fillna("OMS").replace("", "OMS")
    _CHANNEL_SOURCE_BASE_CACHE = (_DATA_VERSION, df)
    return df


def _week_chunk_start(d: date) -> date:
    """Which fixed calendar-day week bucket a date falls into - same 1-7 /
    8-15 / 16-23 / 24-end split as _month_chunks/_calendar_chunk_axis, so a
    date maps to the exact same weekStart key the Weekly Sales Report's own
    cells use."""
    if d.day <= 7:
        start_day = 1
    elif d.day <= 15:
        start_day = 8
    elif d.day <= 23:
        start_day = 16
    else:
        start_day = 24
    return date(d.year, d.month, start_day)


def _channel_source_series(sub: "pd.DataFrame", chunks) -> list[ChannelSourceWeeklySeries]:
    """Shared per-marketplace/week aggregation for get_channel_source_weekly()
    and get_channel_source_weekly_total() (2026-09-18, user-requested: the
    TOTAL row's own channel breakdown, factored out so both share the exact
    same marketplace rollup / week-bucketing logic). ``sub`` must already be
    filtered to the desired DESIGN_NO(s)."""
    if sub.empty:
        return []
    sub = sub.copy()
    sub["_marketplace"] = [
        _marketplace_of(ch, src) for ch, src in zip(sub["channel_name"], sub["source"])
    ]
    sub["_weekStart"] = sub["order_date"].dt.date.map(_week_chunk_start)
    valid_starts = {c[0] for c in chunks}
    sub = sub[sub["_weekStart"].isin(valid_starts)]
    if sub.empty:
        return []

    grouped = sub.groupby(["_marketplace", "source", "_weekStart"], observed=True)["qty"].sum()

    totals: dict[tuple[str, str], int] = {}
    cells: dict[tuple[str, str], dict[str, int]] = {}
    for (marketplace, src, wk_start), qty in grouped.items():
        key = (str(marketplace), str(src))
        qty = int(qty)
        totals[key] = totals.get(key, 0) + qty
        cells.setdefault(key, {})[wk_start.isoformat()] = qty

    sorted_keys = sorted(totals, key=lambda k: totals[k], reverse=True)
    return [
        ChannelSourceWeeklySeries(
            marketplace=marketplace, source=src, totalQty=totals[(marketplace, src)],
            cells=cells[(marketplace, src)],
        )
        for marketplace, src in sorted_keys
    ]


def get_channel_source_weekly(style: str, weeks: int = 9) -> ChannelSourceWeeklyResponse:
    """Per-marketplace week-wise Gross Sale for one Style, on the SAME
    calendar-day week axis as the Weekly Sales Report's own row cells - the
    inline expandable-row drill-down behind the Demand Forecasting page's
    per-Style rows (2026-09-16, user-requested: replace the month-level
    dialog with an inline dropdown, shown week-wise, matching the app's
    existing size-level expand/collapse row pattern). Several raw
    channel_name values (different legal entities on the same platform) roll
    up into one marketplace row - see _marketplace_of(). Actual sales only -
    per-channel forecasts were removed 2026-10-01 (user-requested)."""
    chunks = _calendar_chunk_axis(weeks)
    week_starts_iso = [c[0].isoformat() for c in chunks]

    df = _channel_source_base_df()
    if df is None or df.empty:
        return ChannelSourceWeeklyResponse(style=style, weekStarts=week_starts_iso, series=[])

    sub = df[df["DESIGN_NO"].astype(str) == style]
    return ChannelSourceWeeklyResponse(
        style=style, weekStarts=week_starts_iso, series=_channel_source_series(sub, chunks),
    )


def _matching_style_keys(search: str, sub_category: str, category: str = "") -> set[str]:
    """Every Style (DESIGN_NO) that would appear as its own row in
    get_weekly_grid('style', ...) under this search/Sub Category/Category
    filter - shared with get_channel_source_weekly_total() so the TOTAL
    row's channel breakdown always covers exactly the same Styles the TOTAL
    row's own numbers are summed from (2026-09-18, user-requested; Category
    added 2026-09-21, user-requested)."""
    keys: set[str] = set()
    for r in PLAN_ROWS:
        if not r.designNo:
            continue
        if sub_category.strip() and r.subCategory != sub_category:
            continue
        if category.strip() and r.category != category:
            continue
        keys.add(r.designNo)
    if search.strip():
        needle = search.strip().lower()
        keys = {k for k in keys if needle in k.lower()}
    return keys


def get_channel_source_weekly_total(
    weeks: int = 9, search: str = "", sub_category: str = "", category: str = "",
) -> ChannelSourceWeeklyResponse:
    """Per-marketplace week-wise Gross Sale summed across EVERY Style
    currently matching the Weekly Sales Report's own search/Sub Category/
    Category filter (2026-09-18, user-requested: a channel breakdown for the
    TOTAL row, not just individual Style rows; Category filter added
    2026-09-21) - same marketplace rollup and week axis as
    get_channel_source_weekly(), pooled across every matching design instead
    of one. ``style`` on the response is the literal "TOTAL" label."""
    chunks = _calendar_chunk_axis(weeks)
    week_starts_iso = [c[0].isoformat() for c in chunks]

    df = _channel_source_base_df()
    if df is None or df.empty:
        return ChannelSourceWeeklyResponse(style="TOTAL", weekStarts=week_starts_iso, series=[])

    matching = _matching_style_keys(search, sub_category, category)
    if not matching:
        return ChannelSourceWeeklyResponse(style="TOTAL", weekStarts=week_starts_iso, series=[])

    sub = df[df["DESIGN_NO"].astype(str).isin(matching)]
    return ChannelSourceWeeklyResponse(
        style="TOTAL", weekStarts=week_starts_iso, series=_channel_source_series(sub, chunks),
    )


def get_style_lifecycle(sub_category: str = "") -> dict:
    """STYLE_LIFECYCLE for one Sub Category, or every Sub Category if
    ``sub_category`` is blank (2026-09-22, user-requested Weekly Sales Report
    feature: styles present last calendar year vs newly launched this one)."""
    if not sub_category:
        return STYLE_LIFECYCLE
    entry = STYLE_LIFECYCLE.get("bySubCategory", {}).get(sub_category, {
        "presentCount": 0, "presentStyles": [], "addedCount": 0, "addedStyles": [],
    })
    return {
        "lastYear": STYLE_LIFECYCLE.get("lastYear"),
        "thisYear": STYLE_LIFECYCLE.get("thisYear"),
        "bySubCategory": {sub_category: entry},
    }


# Forward horizon of the Weekly Sales Report (dataService.weeklyGrid's
# default, ~2 months) - the suggestion the weekly production log records.
_LOG_WEEKS = 9


def _record_weekly_production() -> None:
    """Weekly production log (weekly_log.py), run after every up-to-date
    model publish: first completes (and locks) every logged week the data
    now fully covers - filling in its actual sale, its other values staying
    as last refreshed - then saves the running week: each Style's ~2-month
    forecast, stock + WIP and suggested production, refreshed on every
    publish until the week completes."""
    import weekly_log
    try:
        rows = get_weekly_grid("style", _LOG_WEEKS, 5000, 0, "", "", "").rows
        # A week locks only once its last day is both in the data and over
        # (a late manual refresh on that day could otherwise miss its last orders).
        for ws in weekly_log.open_weeks_ending_by(min(SNAPSHOT_DATE, date.today() - timedelta(days=1))):
            actual = {r.key: (r.cells[ws].actual or 0) for r in rows if ws in r.cells}
            weekly_log.complete_week(ws, actual, SNAPSHOT_DATE)
            print(f"[weekly_log] week {ws} completed ({len(actual):,} styles)", file=sys.stderr)

        today = date.today()
        chunks = _month_chunks(today.year, today.month)
        idx = next(i for i, (c_start, c_end) in enumerate(chunks) if c_start <= today <= c_end)
        week_start, week_end = chunks[idx]
        key = week_start.isoformat()
        records = [{
            "style": r.key, "sub_category": r.subCategory, "category": r.category,
            "forecast_qty": r.cells[key].forecast if key in r.cells else 0,
            "forecast_2m_qty": r.futureForecastTotal,
            "available_qty": r.availableQty,
            "suggested_production_qty": r.suggestedProduction,
        } for r in rows]
        saved = weekly_log.save_running_week(records, week_start.strftime("%B %Y"), f"W{idx + 1}",
                                             week_start, week_end, SNAPSHOT_DATE)
        print(f"[weekly_log] running week {key} saved ({saved:,} styles, data through {SNAPSHOT_DATE})", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — the log is a bonus, never break a rebuild
        print(f"[weekly_log] update failed ({exc!r})", file=sys.stderr)


def get_weekly_production_log(style: str = "", status: str = "") -> list[dict]:
    """The weekly production log's rows (weekly_log.read()), with
    ``actual_so_far`` added to every row of a still-running week: the gross
    units sold in that week so far, from today's live Weekly Sales Report."""
    import weekly_log
    rows = weekly_log.read(style, status)
    open_weeks = {r["week_start"] for r in rows if r["status"] == "open"}
    live: dict[str, dict] = {}
    if open_weeks:
        try:
            live = {r.key: r.cells for r in get_weekly_grid("style", _LOG_WEEKS, 5000, 0, "", "", "").rows}
        except Exception as exc:  # noqa: BLE001 — the running figure is a bonus
            print(f"[weekly_log] live actuals unavailable ({exc!r})", file=sys.stderr)
    for r in rows:
        if r["status"] == "open":
            cell = live.get(r["style"], {}).get(r["week_start"])
            r["actual_so_far"] = cell.actual if cell is not None and cell.actual is not None else 0
        else:
            r["actual_so_far"] = None
    return rows


def get_weekly_grid(
    group_by: str, weeks: int = 6, limit: int = 50, offset: int = 0, search: str = "",
    sub_category: str = "", category: str = "",
) -> WeeklyGridResponse:
    """Cached wrapper around _compute_weekly_grid() - see that function's
    docstring for what's actually computed. Keyed on every param plus
    _DATA_VERSION, so the same request served twice between two data
    refreshes skips the expensive per-group aggregation entirely on the
    second call (2026-09-14, user-requested: page load felt slow).

    ALSO keyed on today's own date (2026-09-15, user-requested: Festival/Sale
    must update live as the calendar date changes) - _compute_weekly_grid()
    calls date.today() itself for "today"/"this week"/"upcoming event"
    (cur_chunk, is_historical, festivalOutlook, ...), so without this a
    response computed yesterday and still sitting in cache (the daily
    auto-refresh bumps _DATA_VERSION once at 06:00, not at midnight, and
    could even fail some day) would keep answering with yesterday's "today"
    until the next real data refresh happened to land."""
    cache_key = (group_by, weeks, limit, offset, search, sub_category, category, _DATA_VERSION, date.today())
    cached = _WEEKLY_GRID_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if len(_WEEKLY_GRID_CACHE) >= _WEEKLY_GRID_CACHE_MAX:
        _WEEKLY_GRID_CACHE.clear()
    result = _compute_weekly_grid(group_by, weeks, limit, offset, search, sub_category, category)
    _WEEKLY_GRID_CACHE[cache_key] = result
    return result


def _compute_weekly_grid(
    group_by: str, weeks: int = 6, limit: int = 50, offset: int = 0, search: str = "",
    sub_category: str = "", category: str = "",
) -> WeeklyGridResponse:
    """Forecasted + Actual sales pivoted by week (grouped into months), one
    row per Sub Category or Style (DESIGN_NO) - the Weekly Sales Report
    page's data source. Each week cell carries BOTH numbers at once
    (2026-09-12, user-requested): ``actual`` for historical weeks is the real
    total sold that week, ``forecast`` is what the model predicted for it
    (backtested where available, else naive+festival - same as
    get_breakdown()'s "Recent weeks" table); future weeks have no ``actual``
    yet (None) and only the forward ``forecast``.

    Rows are sorted by combined actual+forecast volume descending and
    paginated with limit/offset - Style grain can be ~1,500 rows, too many
    to render or transfer unpaginated at once."""
    if group_by not in ("subCategory", "style"):
        raise ValueError(f"group_by must be 'subCategory' or 'style', got {group_by!r}")

    groups: dict[str, list[str]] = {}
    group_styles: dict[str, set[str]] = {}  # key -> distinct DESIGN_NOs pooled into it
    group_available: dict[str, int] = {}  # key -> summed inventoryQty + wipQty across its SKUs
    # key -> its Sub Category (for group_by="style", so each Style row can
    # show which Sub Category it belongs to - a design's SUB CATEGORY is
    # consistent across all its own SKUs, so last-write-wins is fine; for
    # group_by="subCategory" this is trivially the key itself).
    group_subcategory: dict[str, str] = {}
    # key -> its top-level Category (2026-09-21, user-requested Category
    # filter) - same last-write-wins reasoning as group_subcategory above;
    # for group_by="subCategory" every row sharing that Sub Category also
    # shares the same Category (Category is the broader grouping ABOVE Sub
    # Category), so this is consistent there too.
    group_category: dict[str, str] = {}
    for r in PLAN_ROWS:
        key = r.subCategory if group_by == "subCategory" else r.designNo
        if not key:
            continue
        groups.setdefault(key, []).append(r.skuCode)
        group_styles.setdefault(key, set()).add(r.designNo)
        group_available[key] = group_available.get(key, 0) + r.inventoryQty + r.wipQty
        group_subcategory[key] = r.subCategory
        group_category[key] = r.category

    # Every distinct Sub Category / Category in the current plan (not
    # filtered by group_by/search/sub_category/category) - populates the
    # respective filter dropdowns.
    subcategory_options = get_subcategory_options()
    category_options = get_category_options()
    category_subcategory_map = get_category_subcategory_map()

    if sub_category.strip():
        # Exact match against group_subcategory (built above from every
        # PLAN_ROWS row's own subCategory) - works for BOTH groupings: for
        # group_by="style" it narrows to that Sub Category's styles; for
        # group_by="subCategory" it just isolates that one row.
        groups = {k: v for k, v in groups.items() if group_subcategory.get(k) == sub_category}
    if category.strip():
        # Same exact-match narrowing, one level up (2026-09-21, user-requested).
        groups = {k: v for k, v in groups.items() if group_category.get(k) == category}
    if search.strip():
        needle = search.strip().lower()
        groups = {k: v for k, v in groups.items() if needle in k.lower()}
    total = len(groups)

    # Two SEPARATE axes, deliberately:
    #  - historical_mondays/forecast_mondays (Mon-Sun) still drive every actual
    #    per-group NUMBER (model blending, backtesting, the festival boost
    #    below) - unchanged, so those stay exactly as accurate as before.
    #  - `chunks` (fixed calendar-day weeks: 1-7/8-15/16-23/24-end, see
    #    _calendar_chunk_axis) is ONLY the DISPLAY grid's column layout
    #    (2026-09-17, user-specified: "W1: 1-7, W2: 8-15, W3: 16-23,
    #    W4: 24-end" - was "W1: 1-8, W2: 9-15, W3: 16-22, W4: 23-31" -
    #    W1 must start on the month's 1st regardless of weekday). Actual
    #    sales are re-summed EXACTLY per calendar day from
    #    _DAY_ACTUAL for these cells (still exact, just re-bucketed);
    #    forecast - which the model only produces per Mon-Sun week, not per
    #    day - is approximated by splitting each week's total evenly across
    #    its 7 days and re-summing whichever days fall in each calendar
    #    chunk. This is a real approximation (documented, not hidden): a
    #    chunk straddling two model weeks blends a slice of each.
    historical_mondays, forecast_mondays = _week_axis(weeks)
    chunks = _calendar_chunk_axis(weeks)
    today = date.today()
    cur_chunk = next((c for c in chunks if c[0] <= today <= c[1]), None)

    months_map: dict[str, list[tuple[date, date]]] = {}
    for c in chunks:
        months_map.setdefault(c[0].strftime("%B %Y"), []).append(c)
    month_labels_sorted = sorted(months_map, key=lambda label: months_map[label][0][0])
    def _week_event(c_start: date, c_end: date) -> tuple[str, str, list["WeeklyGridEvent"]]:
        # "both" when a chunk overlaps a Festival AND a Sale at once
        # (2026-09-24, user-requested: flag this distinctly, e.g. this
        # week's Ganesh Chaturthi overlapping the Meesho/Myntra/Flipkart
        # sale window) - previously Festival silently took priority and the
        # Sale side of an overlap was never surfaced at all. See
        # festival_calendar.py's own module docstring for why "festival" vs
        # "sale" is defined per event name.
        overlapping = festival_calendar.events_overlapping(c_start, c_end)
        if not overlapping:
            return "", "", []
        has_festival = any(c == "festival" for _n, _s, _e, c in overlapping)
        has_sale = any(c == "sale" for _n, _s, _e, c in overlapping)
        category = "both" if has_festival and has_sale else ("festival" if has_festival else "sale")
        names = [n for n, _s, _e, _c in overlapping]
        # Per-event exact dates (2026-09-24, user-requested: clicking a
        # W1..W4 header should show each event's OWN real start/end, not
        # just the calendar chunk's dates - a chunk can be a partial slice
        # of a longer event, or straddle more than one).
        events = [
            WeeklyGridEvent(name=n, category=c, start=s.isoformat(), end=e.isoformat())
            for n, s, e, c in overlapping
        ]
        return category, " + ".join(names), events

    def _build_week(i: int, c_start: date, c_end: date) -> "WeeklyGridWeek":
        category, name, events = _week_event(c_start, c_end)
        return WeeklyGridWeek(
            weekStart=c_start.isoformat(), label=f"W{i + 1}", eventCategory=category, eventName=name, events=events,
        )

    months_payload = [
        WeeklyGridMonth(
            label=label,
            weeks=[_build_week(i, c_start, c_end) for i, (c_start, c_end) in enumerate(months_map[label])],
        )
        for label in month_labels_sorted
    ]
    chunk_month_label = {
        c_start.isoformat(): label
        for label in month_labels_sorted
        for c_start, _c_end in months_map[label]
    }

    outlook = get_festival_outlook()  # also carried into the response as festivalOutlook, below

    # Optimistic festival-aware forecast boost (2026-09-12, user-requested;
    # applied per group via _apply_festival_boost()). Applied
    # independently for the upcoming Festival AND the upcoming Sale (see
    # festival_calendar.py) - either, both, or neither may end up boosting
    # a given group.
    boost_windows = [
        w for w in (
            _event_boost_windows(outlook.upcomingFestival, forecast_mondays, historical_mondays),
            _event_boost_windows(outlook.upcomingSale, forecast_mondays, historical_mondays),
        ) if w is not None
    ]

    built: list[tuple[str, int, int, list[ForecastPoint], list[HistoricalPoint], bool]] = []
    week_qty_by_key: dict[str, dict[str, float]] = {}  # key -> {monday_iso: week qty}, for chunk proration
    for key, skus in groups.items():
        naive_week, wk_actual, series, model_hist = _aggregate_group_series(skus, gross=True)
        forecast, historical = _build_week_points(
            weeks, naive_week, wk_actual, series, model_hist,
        )
        forecast, festival_boosted = _apply_festival_boost(forecast, naive_week, wk_actual, boost_windows)

        built.append((key, len(skus), len(group_styles[key]), forecast, historical, festival_boosted))
        week_qty: dict[str, float] = {p.date: float(p.forecast) for p in historical}
        week_qty.update({p.date: float(p.qty) for p in forecast})
        week_qty_by_key[key] = week_qty

    built.sort(
        key=lambda item: sum(p.actual for p in item[4]) + sum(p.qty for p in item[3]),
        reverse=True,
    )

    # Per-group forecast for every calendar-week cell (each Mon-Sun week's
    # total spread evenly over its 7 days, re-summed per cell), with every
    # COMPLETED cell replaced by its locked value (2026-09-29, user-requested:
    # completed weeks/months must not change on a data refresh - see
    # forecast_freeze.py). Built for EVERY matching group, not just the page,
    # so the TOTAL row is the sum of the same frozen cells and every group's
    # running week gets recorded before it completes.
    chunk_parts: list[tuple[str, list[tuple[str, int]]]] = []
    for c_start, c_end in chunks:
        parts: dict[str, int] = {}
        d = c_start
        while d <= c_end:
            m_iso = (d - timedelta(days=d.weekday())).isoformat()
            parts[m_iso] = parts.get(m_iso, 0) + 1
            d += timedelta(days=1)
        chunk_parts.append((c_start.isoformat(), list(parts.items())))
    live_cells = {
        item[0]: {
            c_iso: round(sum(week_qty_by_key[item[0]].get(m_iso, 0.0) * n / 7.0 for m_iso, n in parts))
            for c_iso, parts in chunk_parts
        }
        for item in built
    }
    try:
        import forecast_freeze
        fc_cells = forecast_freeze.apply(
            f"grid:{group_by}", live_cells, {c_start.isoformat(): c_end for c_start, c_end in chunks}, today,
        )
    except Exception as exc:  # noqa: BLE001 — never break the report over the freeze store
        print(f"[data] forecast freeze unavailable ({exc!r}); serving live values", file=sys.stderr)
        fc_cells = live_cells
    # Headline totals across EVERY matching group (search-filtered, but not
    # paginated) - each SKU belongs to exactly one Sub Category and exactly
    # one Style, so these come out the same regardless of which grouping is
    # active (a useful cross-check).
    total_forecast = sum(sum(p.qty for p in item[3]) for item in built)
    # GROSS, not net - total_actual is built from historical/HistoricalPoint,
    # whose wk_actual now comes from _aggregate_group_series(gross=True)
    # above (2026-09-15, user-requested: show Gross, not Net, as "Actual
    # Sale" everywhere on this page).
    total_actual = sum(sum(p.actual for p in item[4]) for item in built)
    all_matching_skus = [s for skus in groups.values() for s in skus]
    # Current CALENDAR-DAY week (the chunk containing real "today") across
    # EVERY matching group, not just the page - actual is the exact real
    # sum for whichever of its days have already happened (_DAY_ACTUAL);
    # forecast is each touched Mon-Sun week's own total, prorated by day
    # (see the chunk-axis comment above `chunks` for why).
    current_week_forecast = 0
    current_week_actual = 0
    current_week_label = ""
    if cur_chunk is not None:
        cur_days = [
            (cur_chunk[0] + timedelta(days=i)).isoformat()
            for i in range((cur_chunk[1] - cur_chunk[0]).days + 1)
        ]
        cur_iso = cur_chunk[0].isoformat()
        current_week_forecast = int(sum(round(fc_cells[item[0]].get(cur_iso, 0)) for item in built))
        for s in all_matching_skus:
            day_map = _DAY_ACTUAL_GROSS.get(s)
            if not day_map:
                continue
            for d_iso in cur_days:
                current_week_actual += day_map.get(d_iso, 0)
        current_week_label = f"{cur_chunk[0].strftime('%b')} {cur_chunk[0].day}-{cur_chunk[1].day}"
    # Same per-group formula as the "Suggested Production Qty" column below
    # (forward forecast minus available inventory+WIP, floored at 0), summed
    # across EVERY matching group rather than just the current page.
    total_suggested_production = sum(
        max(0, sum(p.qty for p in item[3]) - group_available.get(item[0], 0))
        for item in built
    )

    # Weekly Total row (2026-09-14, user-requested): the SAME per-chunk
    # cell/month-total aggregation as each row below, but summed across
    # EVERY matching group (search/Sub Category filtered, but not
    # paginated) rather than just the current page. Forecast = the sum of
    # every group's own (frozen-if-completed) cell, so the TOTAL row always
    # equals the sum of its rows; actual = one combined {day_iso: qty}
    # series so the per-chunk loop costs O(chunks), not O(chunks * groups).
    total_day_actual: dict[str, int] = {}
    for skus in groups.values():
        for s in skus:
            day_map = _DAY_ACTUAL_GROSS.get(s)
            if not day_map:
                continue
            for d_iso, q in day_map.items():
                total_day_actual[d_iso] = total_day_actual.get(d_iso, 0) + q

    totals_cells: dict[str, WeeklyGridCell] = {}
    totals_month_actual: dict[str, int] = {}
    totals_month_forecast: dict[str, int] = {}
    for c_start, c_end in chunks:
        is_historical = c_start <= today
        key_iso = c_start.isoformat()
        actual_total = 0
        if is_historical:
            d = c_start
            while d <= c_end:
                actual_total += total_day_actual.get(d.isoformat(), 0)
                d += timedelta(days=1)
        forecast_qty = int(sum(round(fc_cells[item[0]].get(key_iso, 0)) for item in built))
        totals_cells[key_iso] = WeeklyGridCell(
            actual=actual_total if is_historical else None,
            forecast=forecast_qty, partial=(c_end > SNAPSHOT_DATE),
        )
        label = chunk_month_label.get(key_iso, "")
        totals_month_forecast[label] = totals_month_forecast.get(label, 0) + forecast_qty
        if is_historical:
            totals_month_actual[label] = totals_month_actual.get(label, 0) + actual_total

    weekly_totals = WeeklyGridTotals(
        cells=totals_cells,
        monthActualTotal=totals_month_actual,
        monthForecastTotal=totals_month_forecast,
    )

    page = built[offset: offset + limit]

    rows_payload: list[WeeklyGridRow] = []
    for key, sku_count, style_count, forecast, historical, festival_boosted in page:
        row_fc = fc_cells.get(key, {})
        day_actual = _aggregate_group_day_actual(groups.get(key, []), gross=True)
        # Launch date only means something for a single design (group_by=
        # "style") - a Sub Category row pools many designs, so it stays at
        # its "" / -1 defaults there (2026-09-14, user-requested LAUNCH DATE
        # column). Earliest launch across the row's own SKUs.
        launch_date = ""
        days_since_launch = -1
        if group_by == "style":
            launch_candidates = [
                (plan_row.launchDate, plan_row.daysSinceLaunch)
                for sku in groups.get(key, [])
                if (plan_row := PLAN_BY_SKU.get(sku)) is not None and plan_row.launchDate
            ]
            if launch_candidates:
                launch_date, days_since_launch = min(launch_candidates, key=lambda t: t[0])
        cells: dict[str, WeeklyGridCell] = {}
        month_actual: dict[str, int] = {}
        month_forecast: dict[str, int] = {}
        for c_start, c_end in chunks:
            is_historical = c_start <= today
            key_iso = c_start.isoformat()
            actual_total = 0
            if is_historical:
                d = c_start
                while d <= c_end:
                    actual_total += day_actual.get(d.isoformat(), 0)
                    d += timedelta(days=1)
            forecast_qty = int(round(row_fc.get(key_iso, 0)))
            partial = c_end > SNAPSHOT_DATE
            cells[key_iso] = WeeklyGridCell(
                actual=actual_total if is_historical else None,
                forecast=forecast_qty, partial=partial,
            )
            label = chunk_month_label.get(key_iso, "")
            month_forecast[label] = month_forecast.get(label, 0) + forecast_qty
            if is_historical:
                month_actual[label] = month_actual.get(label, 0) + actual_total
        # Suggested Production Qty (2026-09-12, user-requested): the next
        # `weeks` (=~ 3 months at the default 13) of forward FORECAST demand,
        # minus what's already available (inventory + WIP) - never negative
        # (already-covered demand suggests producing nothing more, not a
        # negative quantity). Deliberately the forward forecast total only,
        # NOT grandForecastTotal (which also includes the historical weeks'
        # backtested-forecast figures) - production only responds to demand
        # that hasn't happened yet.
        future_forecast_total = sum(p.qty for p in forecast)
        available = group_available.get(key, 0)
        rows_payload.append(
            WeeklyGridRow(
                key=key,
                subCategory=group_subcategory.get(key, ""),
                category=group_category.get(key, ""),
                skuCount=sku_count,
                styleCount=style_count,
                launchDate=launch_date,
                daysSinceLaunch=days_since_launch,
                cells=cells,
                monthActualTotal=month_actual,
                monthForecastTotal=month_forecast,
                grandActualTotal=sum(p.actual for p in historical),
                # Sum of the row's own week cells (frozen where completed), so
                # this grand total never drifts for weeks already finished.
                grandForecastTotal=sum(month_forecast.values()),
                futureForecastTotal=future_forecast_total,
                availableQty=available,
                suggestedProduction=max(0, future_forecast_total - available),
                festivalBoosted=festival_boosted,
            )
        )

    return WeeklyGridResponse(
        groupBy=group_by, total=total, totalForecast=total_forecast, totalActual=total_actual,
        currentWeekForecast=current_week_forecast, currentWeekActual=current_week_actual,
        currentWeekLabel=current_week_label,
        totalSuggestedProduction=total_suggested_production,
        subCategoryOptions=subcategory_options,
        categoryOptions=category_options,
        categorySubCategoryMap=category_subcategory_map,
        festivalOutlook=outlook,
        months=months_payload, rows=rows_payload,
        weeklyTotals=weekly_totals,
    )


# Build once at import, then schedule the daily live refresh - moved to the
# very end of the file (2026-09-26, user-requested cache warm-up) so that by
# the time this runs, every function rebuild()'s new _warm_caches() step
# calls (_channel_source_base_df(), get_festival_outlook(), get_weekly_grid())
# is already defined. Previously this sat right after rebuild()'s own def
# (line ~2016 of this file, long before those functions further down), which
# worked fine until _warm_caches() started reaching forward to them - that
# raised a NameError at import (caught by rebuild()'s own try/except, so it
# failed silently as "cache warm-up failed" rather than crashing startup, but
# never actually warmed anything).
#
# With DEFER_INITIAL_LOAD=1 (set by main.py, 2026-10-01) the first load runs
# in a background thread instead, started by start_background_load(), so the
# web server can open its port immediately - Cloud Run fails a revision whose
# container doesn't listen within its startup timeout, and the full BigQuery +
# ERP load takes minutes. Scripts that import this module (backtest_sweep.py
# etc.) still get the old synchronous load.
DATA_READY = False


# Snapshot serving mode (2026-10-02, Phase 3): SERVING_MODE=snapshot makes this
# process a small read-only API - it loads the serving snapshot the daily job
# (job.py) saved to Cloud Storage instead of loading BigQuery and training,
# picks up a newer snapshot when the job publishes one, and never writes to
# the bucket (the job is the only writer).
SERVING_MODE = os.getenv("SERVING_MODE", "full").strip().lower()
_SNAPSHOT_CHECK_SECS = int(os.getenv("SNAPSHOT_CHECK_SECS", "600"))
_SNAPSHOT_GENERATION: int | None = None   # bucket generation of the loaded snapshot
_SNAPSHOT_LAST_CHECK = 0.0
_SNAPSHOT_LOCK = threading.Lock()


def _fetch_and_load_snapshot() -> bool:
    """Download serving_snapshot.pkl if the bucket has a newer one than the
    loaded one (or load the local file when no bucket is configured), then
    load it. True if a snapshot is loaded afterwards."""
    global _SNAPSHOT_GENERATION
    import cache_sync
    local = _serving_cache_path()
    if not cache_sync.BUCKET:
        return DATA_READY or load_serving_snapshot(local)
    try:
        blob = cache_sync._bucket().get_blob(SERVING_SNAPSHOT_FILE)
    except Exception as exc:  # noqa: BLE001
        print(f"[data] snapshot check failed ({exc!r})", file=sys.stderr)
        return DATA_READY
    if blob is None:
        print("[data] no serving snapshot in the bucket yet - run the daily job", file=sys.stderr)
        return DATA_READY
    if blob.generation == _SNAPSHOT_GENERATION:
        return True
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(local.name + ".download")
    blob.download_to_filename(str(tmp))
    tmp.replace(local)
    if load_serving_snapshot(local):
        _SNAPSHOT_GENERATION = blob.generation
        return True
    return DATA_READY


def _snapshot_initial_load() -> None:
    """SERVING_MODE=snapshot start-up: restore api/.cache (production log,
    frozen weeks), then load the snapshot - retrying every minute until the
    daily job has published one."""
    global DATA_READY, _SNAPSHOT_LAST_CHECK
    import cache_sync
    cache_sync.download_recent()
    while True:
        try:
            with _SNAPSHOT_LOCK:
                if _fetch_and_load_snapshot():
                    _warm_caches()
                    DATA_READY = True
                    _SNAPSHOT_LAST_CHECK = time.time()
                    return
        except Exception as exc:  # noqa: BLE001 - keep retrying
            print(f"[data] snapshot load failed ({exc!r})", file=sys.stderr)
        time.sleep(60)


def _snapshot_refresh() -> None:
    with _SNAPSHOT_LOCK:
        try:
            import cache_sync
            before = _SNAPSHOT_GENERATION
            if _fetch_and_load_snapshot() and _SNAPSHOT_GENERATION != before:
                cache_sync.download_recent()   # the job's newer production log / frozen weeks
                _warm_caches()
        except Exception as exc:  # noqa: BLE001 - keep serving the loaded snapshot
            print(f"[data] snapshot refresh failed ({exc!r})", file=sys.stderr)


def maybe_refresh_snapshot() -> None:
    """Called on requests (main.py): at most every SNAPSHOT_CHECK_SECS, check
    in the background whether the daily job published a newer snapshot."""
    global _SNAPSHOT_LAST_CHECK
    if SERVING_MODE != "snapshot" or not DATA_READY:
        return
    now = time.time()
    if now - _SNAPSHOT_LAST_CHECK < _SNAPSHOT_CHECK_SECS or _SNAPSHOT_LOCK.locked():
        return
    _SNAPSHOT_LAST_CHECK = now
    threading.Thread(target=_snapshot_refresh, daemon=True, name="snapshot-refresh").start()


def start_daily_job() -> dict:
    """SERVING_MODE=snapshot's Refresh button: start one execution of the
    Cloud Run Job (DAILY_JOB_NAME) instead of rebuilding in this process."""
    import json as _json
    import urllib.request
    import google.auth
    import google.auth.transport.requests

    job = os.getenv("DAILY_JOB_NAME", "demand-forecasting-job")
    region = os.getenv("DAILY_JOB_REGION", "asia-south1")
    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or project
    req = urllib.request.Request(
        f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run",
        data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        op = _json.loads(resp.read().decode("utf-8"))
    print(f"[data] daily job started ({op.get('name', '')})", file=sys.stderr)
    return {"source": "job started", "rows": len(PLAN_ROWS), "snapshot": SNAPSHOT_DATE.isoformat(),
            **model_status()}


def _initial_load() -> None:
    global DATA_READY
    if SERVING_MODE == "snapshot":
        _snapshot_initial_load()
        return
    try:
        # Restore api/.cache from Cloud Storage first (no-op unless
        # CACHE_BUCKET is set), so a fresh Cloud Run instance starts with the
        # saved models, frozen weeks and production log - see cache_sync.py.
        import cache_sync
        cache_sync.download_recent()
        rebuild()
    finally:
        DATA_READY = True
    start_daily_refresh()


_INITIAL_LOAD_THREAD: threading.Thread | None = None


def start_background_load() -> None:
    """Run the first rebuild() + daily-refresh scheduling in a background
    thread (idempotent). Requests are answered "still loading" until
    DATA_READY - see main.py."""
    global _INITIAL_LOAD_THREAD
    if DATA_READY or (_INITIAL_LOAD_THREAD and _INITIAL_LOAD_THREAD.is_alive()):
        return
    _INITIAL_LOAD_THREAD = threading.Thread(target=_initial_load, daemon=True, name="initial-load")
    _INITIAL_LOAD_THREAD.start()


if os.getenv("DEFER_INITIAL_LOAD", "0") != "1":
    _initial_load()
