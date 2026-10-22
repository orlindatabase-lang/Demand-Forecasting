"""
Season-wise design suggestions (2026-10-08, user-requested).

Seasons:  S1 Nov-Jan (Winter + Wedding) · S2 Feb-Apr (Wedding + Pre-Summer + Eid)
          S3 May-Jul (Summer + Spring)  · S4 Aug-Oct (Festival + Wedding)

For the RUNNING and the UPCOMING season this answers: which Category, Sub
Category, Colour and Fabric (plus Embroidery, Neck, Sleeve, Top type) to
prefer - as one card per product (Sub Category) holding its colours,
fabrics, details, best mixes (incl. untried ones), make more and cut back.

Read-only add-on: it only READS data the API already holds (the de-duplicated
Gross sales rows via data._channel_source_base_df(), PLAN_ROWS for prices,
SNAPSHOT_DATE) and the garment-attribute sheet. It writes nothing and changes
no existing calculation. Results are cached per data version.

Method (all on Gross units, the same measure as the rest of the dashboard):
  1. Season lift      = attribute's share of sales in the season / its share
                         over the whole history - "especially strong this season".
  2. Units per style  = Empirical-Bayes (Gamma-Poisson) shrunk: values backed by
                         few styles are pulled toward the season average.
  3. Momentum         = last 8 weeks vs the same 8 weeks a year earlier.
  4. Preference score = weighted percentile ranks of 1-3 + volume share,
                         damped by a confidence factor (styles / units behind it).
  5. CatBoost (Poisson) on style x season rows explains each attribute value's
     own effect (SHAP, as a % uplift) and scores every candidate combination for
     the target season, incl. ones never produced.
  6. Gaps / saturated / declining from units-per-style vs number of styles.
  7. Product cards: 1-6 per Sub Category, from its own ranking.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

SEASONS = {
    "S1": {"months": (11, 12, 1), "label": "Nov · Dec · Jan", "occasion": "Winter + Wedding"},
    "S2": {"months": (2, 3, 4), "label": "Feb · Mar · Apr", "occasion": "Wedding + Pre-Summer + Eid"},
    "S3": {"months": (5, 6, 7), "label": "May · Jun · Jul", "occasion": "Summer + Spring"},
    "S4": {"months": (8, 9, 10), "label": "Aug · Sep · Oct", "occasion": "Festival + Wedding"},
}
_SEASON_OF_MONTH = {m: s for s, v in SEASONS.items() for m in v["months"]}
_ORDER = ["S1", "S2", "S3", "S4"]

_CSV_PATH = Path(__file__).resolve().parent.parent / "SKU_Master_Data_Final_Cleaned.csv"
# Attribute -> sheet column. Print_Type is left out (only ~13% filled);
# "Top type" (Dyed / Print) covers that instead.
ATTRIBUTES = {
    "category": "CATEGORY",
    "subCategory": "SUB CATEGORY",
    "colour": "Top_Color",
    "fabric": "TOP_Fabric",
    "embroidery": "Top Emboidery_Type",
    "neck": "NECK STYLE",
    "sleeve": "SLEEVE TYPE",
    "topType": "Top type",
}
RANKED = ("category", "subCategory", "colour", "fabric")          # the four main cards
DIRECTION = ("colour", "fabric", "embroidery", "neck", "sleeve", "topType")
_NULLS = {"", "UNKNOWN", "NOT APPLIED", "NOT_APPLIED", "-", "N/A", "NA", "NONE", "NAN"}

# Optional catalog-tier filters (Cloud SQL CatalogStyle.forecastStatus).
# TIERS limits the styles the suggestions are about (rankings, lift baseline,
# momentum, opportunities, drivers, combination candidates); TRAIN_TIERS the
# styles the CatBoost model learns from. None = every style with sales,
# tiered or not - the current setting (2026-10-08, user-requested: "consider
# overall style data for design suggestion"; was T1-T3 / all six tiers).
# Tier itself is never a model input (it is assigned from sales, so it would
# leak the answer).
TIERS: tuple[str, ...] | None = None
TRAIN_TIERS: tuple[str, ...] | None = None
PRIOR_STYLES = 5.0            # Empirical-Bayes prior strength, in styles
MIN_STYLES = 2                # an attribute value needs this many selling styles to be ranked
# Report weeks (2026-10-10, user-requested: "for the week (W1, W2, W3, W4) use
# the logic from the weekly forecast"): every "now" window is built from the
# Weekly Sales Report's own calendar weeks - W1 1-7, W2 8-15, W3 16-23, W4
# 24-end of month (data._month_chunks) - and the RUNNING week is completed the
# way that report does it: actual sales so far + its weekly forecast for the
# days still to come (the report's latest forecast for the week, saved in
# forecast_freeze, else the live naive x festival rate). A month always has exactly 4 report weeks, so a year has 48.
CHUNK_START_DAYS = (1, 8, 16, 24)
CHUNKS_PER_YEAR = 48
MOMENTUM_CHUNKS = 8           # ~2 months, vs the same 8 report weeks a year earlier
# Current trend (2026-10-09, user-requested: "also consider the current running
# and trending design status"): each value's share of the last TREND_CHUNKS
# report weeks' sales vs its share of the TREND_CHUNKS before. Share, not units,
# so a festival week lifting everything is not mistaken for a trend; over EVERY
# currently selling style, so new launches (no sales last season) count too.
# Late marketplace orders (2026-10-10, user-requested: real-time, but the
# not-yet-complete recent days must not look like a drop): shares are taken
# with every channel at its usual weight (see _chunk_frame), so a channel whose
# latest orders are still arriving cannot drag its products down.
TREND_CHUNKS = 4
TREND_MIN_UNITS = 30          # earlier-window units needed before a trend % is shown
NEW_LAUNCH_DAYS = 56          # a style first sold within this many days = "new launch"
ON_SALE_MIN_UNITS = 20        # last-8-week units for a value to count as still on sale
# ONE rule set for every section (2026-10-09, user-requested: "all parameters
# and all features must be in sync"): the same "on sale now" test, the same
# rising / falling threshold and the same scoring (WEIGHTS) drive the ranked
# cards, Design direction, Make more / less, Best combinations, What drives
# sales and Trending now - and are sent to the page as "rules".
TREND_STRONG = 30             # % change in share (4 weeks) that counts as rising / falling
GAP_RATIO = 1.3               # "design more": sells >= this x the sub category's average per design
SATURATED_RATIO = 0.7         # "design less": sells <= this x the average ...
SATURATED_MIN_STYLES = 4      # ... with at least this many designs
# Trend status (2026-10-09, user-requested: which factor is trending, which is
# starting to go out of trend and which is already out): each value's share of
# sales in LIFE_BLOCKS consecutive blocks of TREND_CHUNKS report weeks (~6
# months). Same windows and TREND_STRONG as everything else - the latest two
# blocks ARE the trend.
LIFE_BLOCKS = 6
# Sudden spike (2026-10-10, user-requested; shown in Trend status): a value's share
# of one report week's sales vs its share of the SPIKE_BASE_CHUNKS weeks
# before - the running week (completed by its forecast) once it has
# SPIKE_MIN_ACTUAL_DAYS of real sales, else the last complete week.
SPIKE_BASE_CHUNKS = 4
SPIKE_MIN_ACTUAL_DAYS = 4
SPIKE_RATIO = 1.5             # share now >= this x its usual share = a spike
SPIKE_MIN_GAIN = 0.1          # ... and up by >= this many share points (~80 pieces a week) - not 0.02% -> 0.03%
OUT_OF_PEAK = 0.5             # "out of trend": current share <= this x its own peak share in those months
# Score weights (percentile ranks), 2026-10-09: the RUNNING season is ranked
# mainly on what sells now (last-4-weeks units per selling style, its 4-week
# share trend and share of current sales); the UPCOMING season blends that
# with last year's same season (units per style, season lift), which says more
# about a season that has not started yet. "stage" = the value's trend stage
# (trending > in trend > fading > out of trend, see _lifecycle) - it adds the
# 6-month view (how far below its best) to the 4-week trend.
WEIGHTS = {"nowPerStyle": 0.30, "trend": 0.20, "nowShare": 0.10, "lift": 0.15, "perStyle": 0.05, "momentum": 0.10,
           "stage": 0.10}
WEIGHTS_UPCOMING = {"perStyle": 0.25, "lift": 0.20, "nowPerStyle": 0.20, "trend": 0.10, "nowShare": 0.05, "momentum": 0.10,
                    "stage": 0.10}
STAGE_SCORE = {"trending": 1.0, "steady": 2 / 3, "fading": 1 / 3, "out": 0.0}
# Suggestions (2026-10-10, user-requested): only values that are Trending or
# In trend are suggested - cards, combinations, design direction, Design more.
# Fading / out of trend values still show in the analysis (trend status,
# drivers) and in Losing steam / Design less.
GOOD_STAGES = ("trending", "steady")


def _suggestable(row: dict) -> bool:
    """Trending or in trend. ``stage`` None = no trend stage for this attribute
    (neck, sleeve, ...) - allowed; "none" = a stage-tracked value that sold
    too little in the last 6 months to have one - not suggested."""
    stage = row.get("stage")
    return stage is None or stage in GOOD_STAGES

def rules() -> dict:
    """The shared thresholds, for the page (badges, texts) to use the same ones."""
    return {"trendWeeks": TREND_CHUNKS, "momentumWeeks": MOMENTUM_CHUNKS, "trendStrong": TREND_STRONG,
            "onSaleMinUnits": ON_SALE_MIN_UNITS, "minStyles": MIN_STYLES, "gapRatio": GAP_RATIO,
            "saturatedRatio": SATURATED_RATIO, "saturatedMinStyles": SATURATED_MIN_STYLES,
            "lifeBlocks": LIFE_BLOCKS, "outOfPeak": OUT_OF_PEAK,
            "spikeBaseWeeks": SPIKE_BASE_CHUNKS, "spikeRatio": SPIKE_RATIO, "spikeMinGain": SPIKE_MIN_GAIN}


def _direction(value) -> int:
    """+1 rising, -1 falling, 0 steady / unknown - by the shared TREND_STRONG."""
    if value is None:
        return 0
    return 1 if value >= TREND_STRONG else -1 if value <= -TREND_STRONG else 0


_CACHE: dict = {}
_LOCK = threading.Lock()
_LAST_ROWS: dict = {}   # training rows of the last build, handed to the background tuner


# --------------------------------------------------------------------------- #
# Seasons
# --------------------------------------------------------------------------- #
def season_of(d: date) -> tuple[str, int]:
    """(season code, season year). S1 spans two calendar years and is
    labelled by its November year (Jan 2026 -> ("S1", 2025))."""
    s = _SEASON_OF_MONTH[d.month]
    return s, (d.year - 1 if (s == "S1" and d.month == 1) else d.year)


def season_bounds(code: str, year: int) -> tuple[date, date]:
    first = {"S1": date(year, 11, 1), "S2": date(year, 2, 1), "S3": date(year, 5, 1), "S4": date(year, 8, 1)}[code]
    nxt = {"S1": date(year + 1, 2, 1), "S2": date(year, 5, 1), "S3": date(year, 8, 1), "S4": date(year, 11, 1)}[code]
    return first, nxt - timedelta(days=1)


def season_label(code: str, year: int) -> str:
    return f"{code} {year}-{str(year + 1)[2:]}" if code == "S1" else f"{code} {year}"


def next_season(code: str, year: int) -> tuple[str, int]:
    i = _ORDER.index(code)
    if code == "S4":
        return "S1", year
    if code == "S1":
        return "S2", year + 1
    return _ORDER[i + 1], year


# --------------------------------------------------------------------------- #
# Garment attributes (read-only copy of the sheet)
# --------------------------------------------------------------------------- #
def _clean(v) -> str:
    s = " ".join(str(v).strip().split())
    if s.upper() in _NULLS:
        return ""
    return s.title()


def _clean_neck(v) -> str:
    """"Round" / "V" / "Collar" ... -> "Round Neck" etc., so one neck isn't split in two."""
    s = _clean(v)
    return f"{s} Neck" if s and not s.endswith("Neck") and len(s.split()) <= 2 else s


def _load_attributes():
    import pandas as pd
    if not _CSV_PATH.exists():
        return pd.DataFrame(columns=["style", *ATTRIBUTES])
    raw = pd.read_csv(_CSV_PATH, encoding="utf-8-sig").drop_duplicates(subset=["STYLE NO"], keep="first")
    out = pd.DataFrame({"style": raw["STYLE NO"].astype(str).str.strip().str.upper()})
    for key, col in ATTRIBUTES.items():
        cleaner = _clean_neck if key == "neck" else _clean
        out[key] = raw[col].map(cleaner) if col in raw.columns else ""
    # Category / Sub Category exactly as the rest of the dashboard names them
    # (design_attributes - e.g. "Anarkali KPD Set", not the title-cased
    # "Anarkali Kpd Set"), 2026-10-09 data diagnosis.
    try:
        import design_attributes
        for key, lookup in (("category", design_attributes.category_of), ("subCategory", design_attributes.sub_category_of)):
            named = out["style"].map(lambda st: lookup(st))
            named = named.where(named != "Unclassified", "")
            out[key] = named.where(named != "", out[key])
    except Exception as exc:  # noqa: BLE001 - fall back to the cleaned CSV names
        print(f"[design_suggest] dashboard names unavailable ({exc!r})", file=sys.stderr)
    return out[out["style"] != ""].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #
def get_design_suggestions(wait: bool = False) -> dict:
    """Running + upcoming season suggestions, cached per data version.

    The build takes ~20-60 s (CatBoost), so by default it runs in a
    background thread: the first call starts it and returns
    {"available": False, "building": True}; later calls return the result
    once ready. ``wait=True`` builds synchronously (tests / scripts)."""
    import data
    key = (data._DATA_VERSION, data.SNAPSHOT_DATE)
    with _LOCK:
        if _CACHE.get("key") == key:
            return _CACHE["value"]
        thread = _CACHE.get("thread")
        if not (thread and thread.is_alive() and _CACHE.get("building") == key):
            thread = threading.Thread(target=_build_and_store, args=(data, key), daemon=True, name="design-suggest")
            _CACHE.update(thread=thread, building=key)
            thread.start()
    if wait:
        thread.join()
        return _CACHE.get("value", {"available": False, "seasons": []})
    return {"available": False, "building": True, "seasons": []}


def _build_and_store(data, key) -> None:
    t0 = time.time()
    try:
        value = _build(data)
    except Exception as exc:  # noqa: BLE001 - an add-on must never break the API
        import traceback
        print(f"[design_suggest] failed ({exc!r})\n{traceback.format_exc()}", file=sys.stderr)
        value = {"available": False, "error": str(exc), "seasons": []}
    with _LOCK:
        _CACHE.update(key=key, value=value)
    print(f"[design_suggest] built in {time.time() - t0:.1f}s", file=sys.stderr)

    # No cross-validated settings yet (or older than TUNE_MAX_AGE_DAYS): tune
    # now, in this same background thread, then rebuild with the winner. The
    # quick-fit result above keeps being served meanwhile.
    rows = _LAST_ROWS.pop("rows", None)
    if rows is not None and _LAST_ROWS.pop("needsTuning", False):
        try:
            tuned = _tune(rows)
        except Exception as exc:  # noqa: BLE001
            print(f"[design_suggest] cross-validation failed ({exc!r})", file=sys.stderr)
            tuned = None
        if tuned:
            _save_tuning(tuned)
            # Rebuild only if the saved file reads back - otherwise the rebuild
            # would see "not tuned" again and loop.
            if _load_tuning() and (data._DATA_VERSION, data.SNAPSHOT_DATE) == key:
                _build_and_store(data, key)


def _build(data) -> dict:
    import numpy as np
    import pandas as pd

    base = data._channel_source_base_df()
    if base is None or base.empty:
        return {"available": False, "error": "no sales data loaded", "seasons": []}
    snap: date = data.SNAPSHOT_DATE
    sales = pd.DataFrame({
        "style": base["DESIGN_NO"].astype(str).str.strip().str.upper(),
        "date": base["order_date"].dt.normalize(),
        "qty": pd.to_numeric(base["qty"], errors="coerce").fillna(0.0).astype("float64"),   # plain float: nullable Int64 breaks reshapes
        "channel": _channels(base, data),
    })
    sales = sales[sales["date"] <= pd.Timestamp(snap)]
    sales["ord"] = _add_chunk_ord(sales)
    months = sales["date"].dt.month
    sales["season"] = months.map(_SEASON_OF_MONTH)
    sales["seasonYear"] = np.where((sales["season"] == "S1") & (months == 1), sales["date"].dt.year - 1, sales["date"].dt.year)
    tier_of: dict[str, str] = {}
    if TIERS or TRAIN_TIERS:
        import catalog_style
        tier_of = {str(k).strip().upper(): v for k, v in catalog_style.get_current_tier_map().items()}

    def _in_tiers(df, tiers):
        return df if not tiers else df[df["style"].isin({st for st, t in tier_of.items() if t in tiers})]

    train_sales = _in_tiers(sales, TRAIN_TIERS)
    sales = _in_tiers(sales, TIERS)
    if sales.empty:
        scope = f"styles in tiers {', '.join(TIERS)}" if TIERS else "styles"
        return {"available": False, "error": f"no sales for {scope} (catalog tiers unavailable?)",
                "tiers": list(TIERS or ()), "seasons": []}
    allowed = set(sales["style"].unique())

    attrs = _load_attributes()
    styles = attrs.set_index("style")
    price = _style_prices(data)

    run_code, run_year = season_of(snap)
    up_code, up_year = next_season(run_code, run_year)
    # One frame of report weeks (running week completed by its forecast,
    # channels at their usual weight) behind every "now" signal on the page.
    projection, window = _running_projection(data, sales, snap, styles)
    frame = _chunk_frame(sales, snap, projection)
    lifecycle = _lifecycle(frame, styles, snap)
    spikes, window["spike"] = _spikes(frame, styles, snap)
    for attr in TREND_STATUS_ATTRS:               # ⚡ on each Trend status row
        for r in lifecycle.get(attr, []):
            r["spike"] = spikes.get(attr, {}).get(r["value"])
    trend, mom = _trend(frame), _momentum(frame)
    model = _train_model(train_sales, styles, price, snap, focus=allowed)
    if model:
        _LAST_ROWS.update(rows=model["rows"], needsTuning=model["needsTuning"])

    seasons = []
    for role, code, year in (("running", run_code, run_year), ("upcoming", up_code, up_year)):
        seasons.append(_season_block(role, code, year, sales, styles, price, snap, model, trend, lifecycle, spikes, mom))
    return {
        "available": True,
        "dataThrough": snap.isoformat(),
        "historyFrom": sales["date"].min().date().isoformat(),
        "measure": "Gross units",
        "tiers": list(TIERS or ()),
        "tierStyles": int(sales["style"].nunique()),
        "model": model["info"] if model else None,
        "trendingNow": _trending_now(sales, styles, snap, trend, mom),
        "window": window,
        "rules": rules(),
        "lifecycle": lifecycle,
        "seasons": seasons,
    }


def _style_prices(data) -> dict[str, float]:
    """Average realised selling price per style (from the plan's per-SKU prices)."""
    acc: dict[str, list[float]] = {}
    for r in data.PLAN_ROWS:
        if r.designNo and r.price:
            acc.setdefault(str(r.designNo).strip().upper(), []).append(float(r.price))
    return {k: float(sum(v) / len(v)) for k, v in acc.items()}


# --------------------------------------------------------------------------- #
# Per-season block
# --------------------------------------------------------------------------- #
def _reference_occurrence(code: str, year: int, sales, snap: date) -> tuple[int | None, str]:
    """The most recent COMPLETE occurrence of this season before (code, year) -
    the evidence base. None if that season has never been completed."""
    for y in range(year - 1, year - 6, -1):
        start, end = season_bounds(code, y)
        if end < snap and ((sales["season"] == code) & (sales["seasonYear"] == y)).any():
            first = sales["date"].min().date()
            coverage = "complete" if start >= first else "partial (data starts mid-season)"
            return y, coverage
    return None, ""


def _season_block(role, code, year, sales, styles, price, snap, model, trend=None, lifecycle=None, spikes=None,
                  mom=None) -> dict:  # spikes: kept for callers; shown in Trend status
    import pandas as pd

    start, end = season_bounds(code, year)
    ref_year, coverage = _reference_occurrence(code, year, sales, snap)
    block = {
        "role": role,
        "season": code,
        "label": season_label(code, year),
        "months": SEASONS[code]["label"],
        "occasion": SEASONS[code]["occasion"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "reference": season_label(code, ref_year) if ref_year is not None else "",
        "referenceCoverage": coverage,
        "rankings": {},
        "products": [],
        "overview": {},
        "opportunities": [],
        "drivers": [],
    }
    if ref_year is None:
        block["note"] = "No completed occurrence of this season in the history yet."
        return block

    ref = sales[(sales["season"] == code) & (sales["seasonYear"] == ref_year)]
    per_style = ref.groupby("style")["qty"].sum()
    per_style = per_style[per_style > 0]
    all_per_style = sales.groupby("style")["qty"].sum()
    mom = mom or {}

    joined = pd.DataFrame({"units": per_style}).join(styles, how="left").fillna("")
    joined_all = pd.DataFrame({"units": all_per_style}).join(styles, how="left").fillna("")
    season_total = float(joined["units"].sum())
    all_total = float(joined_all["units"].sum())
    mu = season_total / max(1, len(joined))                      # avg units per selling style

    weights = WEIGHTS_UPCOMING if role == "upcoming" else WEIGHTS
    stages = _stage_maps(lifecycle)
    ranked_all = {}
    for attr in (*RANKED, *DIRECTION[2:]):
        ranked_all[attr] = _rank_attribute(attr, joined, joined_all, season_total, all_total, mu, mom,
                                           trend=trend, weights=weights, stage_of=stages.get(attr))
        block["rankings"][attr] = [r for r in ranked_all[attr] if _suggestable(r)]
    # The same scoring inside every sub category - the base of every product card.
    sc_rank = _subcategory_rankings(joined, joined_all, mom, trend, weights, stages)
    block["opportunities"] = _opportunities(joined, mu, mom, trend=trend, joined_all=joined_all,
                                            weights=weights, role=role, sc_rank=sc_rank)
    if model:
        block["drivers"] = _drivers(model, code, ranked_all)   # analysis: every value on sale
    active_now = [v[0] for v in trend.values() if _active(v)] if trend else []
    avg_now = sum(active_now) / max(1, len(active_now))         # last-4-weeks units of an average selling design
    block["products"] = _products(block, sc_rank, model, code, joined_all, stages, avg_now)
    block["overview"] = _overview(block["products"])
    return block


def _channels(base, data):
    """Marketplace of every sales row (Myntra, Ajio, Website, ...), the same
    names as the channel drill-downs (data._marketplace_of)."""
    import pandas as pd
    if "channel_name" not in base.columns:
        return pd.Series("All", index=base.index)
    src = base["source"].astype(str) if "source" in base.columns else pd.Series("OMS", index=base.index)
    key = base["channel_name"].astype(str) + "\x1f" + src
    names = {k: data._marketplace_of(*k.split("\x1f", 1)) for k in key.unique()}
    return key.map(names)


def _chunk_ord(d: date) -> int:
    """Running number of the report week (W1-W4 of its month) a date falls in."""
    idx = 0 if d.day <= 7 else 1 if d.day <= 15 else 2 if d.day <= 23 else 3
    return (d.year * 12 + d.month - 1) * 4 + idx


def _chunk_bounds(o: int) -> tuple[date, date]:
    import calendar
    ym, idx = divmod(o, 4)
    y, m = divmod(ym, 12)
    m += 1
    start = date(y, m, CHUNK_START_DAYS[idx])
    end = date(y, m, CHUNK_START_DAYS[idx + 1] - 1) if idx < 3 else date(y, m, calendar.monthrange(y, m)[1])
    return start, end


def _chunk_label(o: int) -> str:
    return f"{_chunk_bounds(o)[0]:%b} W{o % 4 + 1}"


def _add_chunk_ord(sales):
    import numpy as np
    day = sales["date"].dt.day
    idx = np.select([day <= 7, day <= 15, day <= 23], [0, 1, 2], 3)
    return (sales["date"].dt.year * 12 + sales["date"].dt.month - 1) * 4 + idx


PACE_PRIOR_DAYS = 3.0          # pace credibility: real days / (real days + this)
PACE_LIMITS = (0.5, 2.0)       # the pace factor never goes beyond these


def _running_projection(data, sales, snap: date, styles=None) -> tuple[dict, dict]:
    """The running report week's sales still to come ({style: {channel:
    units}}) - the Weekly Sales Report's logic: the week's forecast as the
    report last showed it (forecast_freeze.stored "grid:style"; the running
    week is not frozen) prorated to the days after ``snap``, else the live
    naive weekly rate x festival factor per day. Then corrected by this week's real PACE (2026-10-10,
    user-requested): per Sub Category, real units so far / the forecast for
    those same days, pulled toward 1 while there are few real days
    (PACE_PRIOR_DAYS) and kept within PACE_LIMITS - so a product clearly
    selling faster (or slower) than forecast this week is not flattened back
    to the forecast. Split over channels by the style's own channel mix in
    the last 4 report weeks."""
    import festival
    o = _chunk_ord(snap)
    start, end = _chunk_bounds(o)
    remaining = (end - snap).days
    info = {"runningWeek": _chunk_label(o), "from": start.isoformat(), "to": end.isoformat(),
            "actualThrough": snap.isoformat(), "forecastDays": max(0, remaining), "frozenStyles": 0, "estimatedStyles": 0,
            "realDays": (snap - start).days + 1, "pace": {}}
    if remaining <= 0:
        return {}, info
    try:
        import forecast_freeze
        frozen = {str(k).strip().upper(): v for k, v in forecast_freeze.stored("grid:style").items()}
    except Exception:  # noqa: BLE001 - fall back to the live rate
        frozen = {}
    naive: dict[str, float] = {}
    for r in data.PLAN_ROWS:
        if r.designNo:
            st = str(r.designNo).strip().upper()
            naive[st] = naive.get(st, 0.0) + float(data._NAIVE_WEEK.get(r.skuCode, 0.0))
    fest = sum(festival.festival_factor(snap + timedelta(days=i))[0] for i in range(1, remaining + 1))
    fest_done = sum(festival.festival_factor(start + timedelta(days=i))[0] for i in range((snap - start).days + 1))
    week_len = (end - start).days + 1
    real_days = (snap - start).days + 1
    recent = sales[(sales["ord"] >= o - 4) & (sales["ord"] < o)]
    by_style = recent.groupby(["style", "channel"])["qty"].sum()
    overall = recent.groupby("channel")["qty"].sum()
    overall = (overall / overall.sum()).to_dict() if overall.sum() > 0 else {"All": 1.0}
    mix: dict[str, dict[str, float]] = {}
    for (st, ch), q in by_style.items():
        mix.setdefault(st, {})[ch] = float(q)
    rest_of, done_fc = {}, {}
    for st in set(naive) | set(frozen):
        fc = frozen.get(st, {}).get(start.isoformat())
        if fc is not None:
            rest_of[st] = float(fc) * remaining / week_len
            done_fc[st] = float(fc) * real_days / week_len
            info["frozenStyles"] += 1
        else:
            rest_of[st] = naive.get(st, 0.0) / 7.0 * fest
            done_fc[st] = naive.get(st, 0.0) / 7.0 * fest_done
            info["estimatedStyles"] += 1
    # This week's pace per Sub Category (more volume than one design).
    sc_of = styles["subCategory"] if styles is not None and "subCategory" in styles.columns else None
    so_far = sales[sales["ord"] == o].groupby("style")["qty"].sum()
    pace_real, pace_fc = {}, {}
    for st, f_done in done_fc.items():
        sc = (sc_of.get(st, "") if sc_of is not None else "") or "_"
        pace_real[sc] = pace_real.get(sc, 0.0) + float(so_far.get(st, 0.0))
        pace_fc[sc] = pace_fc.get(sc, 0.0) + f_done
    credibility = real_days / (real_days + PACE_PRIOR_DAYS)
    pace = {}
    for sc, f in pace_fc.items():
        raw = pace_real[sc] / f if f > 0 else 1.0
        pace[sc] = min(PACE_LIMITS[1], max(PACE_LIMITS[0], 1.0 + (raw - 1.0) * credibility))
    info["pace"] = {sc: round(v, 2) for sc, v in sorted(pace.items(), key=lambda kv: -pace_fc[kv[0]])[:12] if sc != "_"}
    out = {}
    for st, rest in rest_of.items():
        sc = (sc_of.get(st, "") if sc_of is not None else "") or "_"
        rest *= pace.get(sc, 1.0)
        if rest <= 0:
            continue
        m = mix.get(st)
        tot = sum(m.values()) if m else 0.0
        shares = {c: q / tot for c, q in m.items()} if tot > 0 else overall
        out[st] = {c: rest * w for c, w in shares.items() if w > 0}
    return out, info


CHUNK_SPAN = CHUNKS_PER_YEAR + MOMENTUM_CHUNKS


def _chunk_frame(sales, snap: date, projection=None):
    """Report-week sales up to ``snap``: one row per style x report week x
    channel with ``offset`` (0 = the week containing snap, -1 the week
    before, ...), ``qty`` (real units; + the running week's ``projection``,
    flagged ``proj``) and ``adj`` = qty with every channel at its usual
    weight: within each week a channel's units are scaled to its share of the
    last LIFE_BLOCKS x TREND_CHUNKS weeks. A channel whose latest orders are
    still arriving (marketplaces report late) is scaled back up, so its
    products' share - every trend signal here - is not dragged down. Totals
    per week are unchanged."""
    import numpy as np
    import pandas as pd
    o_snap = _chunk_ord(snap)
    ords = sales["ord"] if "ord" in sales.columns else _add_chunk_ord(sales)
    keep = ((sales["date"] <= pd.Timestamp(snap)) & (ords > o_snap - CHUNK_SPAN)).to_numpy()
    f = pd.DataFrame({
        "style": sales["style"].to_numpy()[keep],
        "offset": (ords.to_numpy()[keep] - o_snap).astype(int),
        "channel": sales["channel"].to_numpy()[keep] if "channel" in sales.columns else "All",
        "qty": sales["qty"].to_numpy(dtype="float64")[keep],
    })
    f = f.groupby(["style", "offset", "channel"], as_index=False)["qty"].sum()
    f["proj"] = False
    if projection:
        extra = pd.DataFrame([{"style": st, "offset": 0, "channel": c, "qty": float(q), "proj": True}
                              for st, by in projection.items() for c, q in by.items() if q > 0])
        if not extra.empty:
            f = pd.concat([f, extra], ignore_index=True)
    recent = f[f["offset"] > -LIFE_BLOCKS * TREND_CHUNKS]
    w = recent.groupby("channel")["qty"].sum()
    w = (w / w.sum()) if w.sum() > 0 else w
    tot = f.groupby("offset")["qty"].transform("sum").to_numpy()
    tot_c = f.groupby(["offset", "channel"])["qty"].transform("sum").to_numpy()
    weight = f["channel"].map(w).fillna(0.0).to_numpy()
    f["adj"] = np.where(tot_c > 0, f["qty"].to_numpy() * weight * tot / np.where(tot_c > 0, tot_c, 1.0), 0.0)
    return f


def _active(v) -> bool:
    """Selling now - real units in the trend window (the running week's
    forecast part does not count as selling)."""
    return (v[2] if len(v) > 2 else v[0]) > 0


def _momentum(frame) -> dict[str, tuple[float, float]]:
    """style -> (units in the last MOMENTUM_CHUNKS report weeks, units in the
    same report weeks a year earlier) - channel-weighted (see _chunk_frame)."""
    now = frame[frame["offset"] > -MOMENTUM_CHUNKS].groupby("style")["adj"].sum()
    ly = frame[(frame["offset"] <= -CHUNKS_PER_YEAR) & (frame["offset"] > -CHUNKS_PER_YEAR - MOMENTUM_CHUNKS)]
    ly = ly.groupby("style")["adj"].sum()
    return {s: (float(now.get(s, 0.0)), float(ly.get(s, 0.0))) for s in set(now.index) | set(ly.index)}


def _trend(frame) -> dict[str, tuple[float, float, float, float, float]]:
    """style -> (units in the last TREND_CHUNKS report weeks, units in the
    TREND_CHUNKS before - both channel-weighted, the running week completed
    by its forecast - then REAL units sold in those two windows, and the
    running week's expected rest (its forecast part)). Only the first two
    drive trends; the real / expected ones are what the page shows as pieces."""
    cur = frame[frame["offset"] > -TREND_CHUNKS]
    prev = frame[(frame["offset"] <= -TREND_CHUNKS) & (frame["offset"] > -2 * TREND_CHUNKS)]
    now = cur.groupby("style")["adj"].sum()
    before = prev.groupby("style")["adj"].sum()
    real = cur[~cur["proj"]].groupby("style")["qty"].sum()
    real_before = prev[~prev["proj"]].groupby("style")["qty"].sum()
    expected = cur[cur["proj"]].groupby("style")["qty"].sum()
    return {s: (float(now.get(s, 0.0)), float(before.get(s, 0.0)), float(real.get(s, 0.0)),
                float(real_before.get(s, 0.0)), float(expected.get(s, 0.0)))
            for s in set(now.index) | set(before.index)}


def _share_trend(members, trend, total_now: float, total_before: float) -> tuple[float, float, float | None, int]:
    """(units now, share now %, share change % vs the window before or None, styles selling now)
    for a set of styles - see TREND_CHUNKS."""
    now = sum(trend.get(s, (0.0, 0.0))[0] for s in members)
    before = sum(trend.get(s, (0.0, 0.0))[1] for s in members)
    active = sum(1 for s in members if _active(trend.get(s, (0.0, 0.0))))
    share_now = now / total_now if total_now else 0.0
    share_before = before / total_before if total_before else 0.0
    change = (share_now / share_before - 1) * 100 if before >= TREND_MIN_UNITS and share_before > 0 else None
    return now, share_now * 100, change, active


def _trend_totals(trend) -> tuple[float, float]:
    return sum(v[0] for v in trend.values()), sum(v[1] for v in trend.values())


def _pct_rank(values: list[float]) -> list[float]:
    import pandas as pd
    if not values:
        return []
    return pd.Series(values, dtype="float64").rank(pct=True, method="average").fillna(0.5).tolist()


def _rank_attribute(attr, joined, joined_all, season_total, all_total, mu, mom, trend=None, weights=None,
                    scope=None, stage_of=None) -> list[dict]:
    """Rank one attribute's values for a season.

    With current sales (``trend``, 2026-10-09, user-requested: "the design
    suggestion must be as per the current running and trending design"), the
    ranking is led by what is selling NOW - the last TREND_CHUNKS weeks' units per
    selling style, share of current sales and its 4-week trend - with last
    year's season (lift, units per style) and year-on-year momentum as
    supporting evidence; see WEIGHTS / WEIGHTS_UPCOMING. Only values on sale
    now (2+ styles selling, ON_SALE_MIN_UNITS) are ranked, including ones that
    did not exist last season. Without ``trend`` it falls back to last
    season's evidence only. ``stage_of`` ({value: trend stage}, _lifecycle)
    adds the trend stage as a signal; values without one score neutral."""
    weights = weights or WEIGHTS
    trend = trend or {}
    current = bool(trend)
    # ``scope`` (styles): measure shares / averages within it - a sub category.
    scoped = trend if scope is None else {st: trend[st] for st in scope if st in trend}
    t_now, t_before = _trend_totals(scoped)
    g = joined[joined[attr] != ""].groupby(attr)["units"].agg(units="sum", styles="count")
    g_all = joined_all[joined_all[attr] != ""].groupby(attr)["units"].sum()
    # Average last-4-weeks units per selling style - the prior the current
    # units per style are shrunk toward (same Gamma-Poisson idea as perStyle).
    active_all = [s for s, v in scoped.items() if _active(v)]
    mu_now = t_now / max(1, len(active_all))

    values = set(g.index[g["styles"] >= MIN_STYLES])
    if current:
        values |= set(joined_all.loc[joined_all[attr] != "", attr].unique())
    rows = []
    for value in values:
        members = joined_all.index[joined_all[attr] == value]
        now = sum(mom.get(s, (0.0, 0.0))[0] for s in members)
        ly = sum(mom.get(s, (0.0, 0.0))[1] for s in members)
        momentum = (now - ly) / ly * 100 if ly >= 20 else None
        now_units, now_share, trend_pct, active = _share_trend(members, trend, t_now, t_before)
        if current and (active < MIN_STYLES or max(now, now_units) < ON_SALE_MIN_UNITS):
            continue   # not on sale now - nothing to suggest
        has_ref = value in g.index and int(g.loc[value, "styles"]) >= MIN_STYLES
        units, n = (float(g.loc[value, "units"]), int(g.loc[value, "styles"])) if has_ref else (0.0, 0)
        share = units / season_total if season_total else 0.0
        share_all = float(g_all.get(value, 0.0)) / all_total if all_total else 0.0
        lift = share / share_all if has_ref and share_all > 0 else 1.0
        per_style_raw = units / n if n else 0.0
        per_style = (units + PRIOR_STYLES * mu) / (n + PRIOR_STYLES) if has_ref else 0.0   # Gamma-Poisson shrinkage
        now_per_style = (now_units + PRIOR_STYLES * mu_now) / (active + PRIOR_STYLES) if current else 0.0
        ref_conf = min(1.0, n / 5) * min(1.0, units / 200)
        now_conf = min(1.0, active / 5) * min(1.0, now_units / 100)
        confidence = (now_conf if weights is WEIGHTS else (ref_conf + now_conf) / 2) if current else ref_conf
        rows.append({
            "value": str(value), "units": int(round(units)), "styles": n, "share": round(float(share) * 100, 1),
            "lift": round(float(lift), 2), "perStyle": round(float(per_style), 1), "perStyleRaw": round(float(per_style_raw), 1),
            "momentum": None if momentum is None else round(float(momentum), 1), "confidence": round(float(confidence), 2),
            "trend": None if trend_pct is None else round(float(trend_pct), 1),
            "nowShare": round(float(now_share), 1), "activeStyles": int(active),
            "nowUnits": int(round(now_units)), "nowPerStyle": round(float(now_per_style), 1),
            "lastSeason": bool(has_ref),
            "stage": None if stage_of is None else stage_of.get(str(value), "none"),
        })
    if not rows:
        return []

    def rank(key: str, present=lambda r: True) -> list[float]:
        """Percentile rank of ``key``; rows without that signal get a neutral 0.5."""
        vals = [r[key] if present(r) and r[key] is not None else None for r in rows]
        pr = _pct_rank([v for v in vals if v is not None])
        it = iter(pr)
        return [next(it) if v is not None else 0.5 for v in vals]

    ranks = {
        "lift": rank("lift", lambda r: r["lastSeason"]),
        "perStyle": rank("perStyle", lambda r: r["lastSeason"]),
        "share": rank("share", lambda r: r["lastSeason"]),
        "momentum": rank("momentum"),
        "trend": rank("trend"),
        "nowPerStyle": rank("nowPerStyle"),
        "nowShare": rank("nowShare"),
        # Absolute, not a percentile: "trending" is good even if everything is trending.
        "stage": [STAGE_SCORE.get(r["stage"], 0.5) for r in rows],
    }
    now_keys = ("trend", "nowPerStyle", "nowShare", "stage")
    for i, r in enumerate(rows):
        raw = sum(w * ranks[k][i] for k, w in weights.items() if current or k not in now_keys)
        if not current:   # last-season-only fallback: renormalise the weights it can use
            raw /= max(1e-9, sum(w for k, w in weights.items() if k not in now_keys))
        r["score"] = int(round(100 * raw * (0.5 + 0.5 * r["confidence"])))
        r["reasons"] = _reasons(r)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def _reasons(r: dict) -> str:
    parts = []
    if r.get("nowUnits"):
        parts.append(f"now {r['nowPerStyle']:,.0f} units per selling style, {r['nowShare']:.1f}% of the last 4 weeks' sales "
                     f"({r['activeStyles']} styles selling)")
    if not r.get("lastSeason", True):
        parts.append("not sold in last year's season")
    if r["lift"] >= 1.15:
        parts.append(f"strong this season ({r['lift']:.2f}× its usual share)")
    elif r["lift"] <= 0.85:
        parts.append(f"weaker this season ({r['lift']:.2f}× its usual share)")
    if r.get("lastSeason", True):
        parts.append(f"{r['perStyle']:,.0f} units per style last season")
    if r["momentum"] is not None:
        parts.append(f"{'+' if r['momentum'] >= 0 else ''}{r['momentum']:.0f}% vs same 8 weeks last year")
    if r.get("trend") is not None and abs(r["trend"]) >= 15:
        word = "gaining" if r["trend"] > 0 else "losing"
        parts.append(f"{word} share now ({'+' if r['trend'] > 0 else ''}{r['trend']:.0f}% vs the 4 weeks before; "
                     f"{r['nowShare']:.1f}% of last 4 weeks' sales)")
    stage = {"trending": "trending", "steady": "in trend", "fading": "starting to fade",
             "out": "out of trend (far below its best in the last 6 months)"}.get(r.get("stage") or "")
    if stage:
        parts.append(stage)
    if r["confidence"] < 0.6:
        parts.append("limited evidence")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Opportunities: Sub Category x Colour and Sub Category x Fabric
# --------------------------------------------------------------------------- #
def _subcategory_rankings(joined, joined_all, mom, trend, weights, stages=None) -> dict[str, dict]:
    """{sub category: {"rankings": {attr: rows}, "avgNow", "avgLast"}} - every
    colour / fabric / detail scored with the SAME method as the ranked cards,
    but within the sub category (its share, its averages). The trend stage is
    the value's business-wide one (``stages``, _stage_maps)."""
    stages = stages or {}
    out = {}
    trend = trend or {}
    for sc in sorted(v for v in joined_all["subCategory"].unique() if v):
        j_sc = joined[joined["subCategory"] == sc]
        a_sc = joined_all[joined_all["subCategory"] == sc]
        season_total, all_total = float(j_sc["units"].sum()), float(a_sc["units"].sum())
        mu = season_total / max(1, len(j_sc))
        rankings = {attr: _rank_attribute(attr, j_sc, a_sc, season_total, all_total, mu, mom,
                                          trend=trend, weights=weights, scope=list(a_sc.index),
                                          stage_of=stages.get(attr))
                    for attr in DIRECTION if attr in a_sc.columns}
        now_units = sum(trend.get(st, (0.0, 0.0))[0] for st in a_sc.index)
        active = sum(1 for st in a_sc.index if _active(trend.get(st, (0.0, 0.0))))
        sc_stage = stages["subCategory"].get(sc, "none") if "subCategory" in stages else None
        out[sc] = {"rankings": rankings, "avgNow": now_units / max(1, active), "avgLast": mu, "stage": sc_stage}
    return out


def _opportunities(joined, mu, mom, trend=None, joined_all=None, weights=None, role="running", sc_rank=None) -> list[dict]:
    """Make more / Picking up / Losing steam / Make less, per Sub Category x
    Colour / Fabric, from the SAME within-sub-category evaluation as the cards
    (``_subcategory_rankings``) and the shared thresholds:

    - basis: the running season compares designs on what they sell NOW (last
      4 weeks per selling design); the upcoming one on last year's same season.
    - Design more: sells >= GAP_RATIO x the sub category's average per design,
      with few designs, and not falling (TREND_STRONG).
    - Losing steam: was at / above average last season, falling now.
    - Design less: <= SATURATED_RATIO x average with many designs, not rising.
    - Picking up: rising now (TREND_STRONG).
    Only values on sale now are judged (same rule as the cards)."""
    trend = trend or {}
    pool = joined if joined_all is None else joined_all
    if sc_rank is None:
        sc_rank = _subcategory_rankings(joined, pool, mom, trend, weights or WEIGHTS)
    current = bool(trend)
    use_now = current and role == "running"
    out = []
    for sc, info in sc_rank.items():
        for second in ("colour", "fabric"):
            rows = info["rankings"].get(second, [])
            if len(rows) < 3:
                continue
            members_of = {r["value"]: pool.index[(pool["subCategory"] == sc) & (pool[second] == r["value"])] for r in rows}
            basis = []
            for r in rows:
                now_basis = use_now or not r["lastSeason"]
                per, n = (r["nowPerStyle"], r["activeStyles"]) if now_basis else (r["perStyle"], r["styles"])
                basis.append((per, n, now_basis))
            med = float(sorted(n for _, n, _ in basis)[len(basis) // 2])
            for r, (per, n, now_basis) in zip(rows, basis):
                avg = info["avgNow"] if now_basis else info["avgLast"]
                if avg <= 0 or n < MIN_STYLES:
                    continue
                members = members_of[r["value"]]
                recent = max(sum(mom.get(st, (0.0, 0.0))[0] for st in members),
                             sum(trend.get(st, (0.0, 0.0))[0] for st in members))
                on_sale = recent >= ON_SALE_MIN_UNITS
                if not on_sale:
                    continue   # not in the range now - the cards skip it too
                move = _direction(r["trend"] if r["trend"] is not None else r["momentum"])
                if not _suggestable(r) or not _suggestable(info):
                    move = min(move, -1)   # it or its sub category fading / out of trend: never "design more" / "picking up"
                strong_before = r["lastSeason"] and r["perStyle"] >= info["avgLast"]
                kind = None
                if per >= GAP_RATIO * avg and n <= max(2.0, med) and move >= 0:
                    kind = "gap"
                elif move < 0 and strong_before:
                    kind = "declining"
                elif per <= SATURATED_RATIO * avg and n >= max(float(SATURATED_MIN_STYLES), 1.5 * med) and move <= 0:
                    kind = "saturated"
                elif move > 0:
                    kind = "rising"
                if kind:
                    out.append({
                        "type": kind, "subCategory": sc, "attribute": second, "value": r["value"],
                        "styles": int(n), "units": r["units"], "perStyle": round(float(per), 1),
                        "subCategoryPerStyle": round(float(avg), 1), "basis": "now" if now_basis else "lastSeason",
                        "momentum": r["momentum"], "trend": r["trend"], "score": r["score"],
                    })
    order = {"gap": 0, "rising": 1, "declining": 2, "saturated": 3}
    out.sort(key=lambda o: (order[o["type"]],
                            -(o["trend"] or 0) if o["type"] == "rising" else -o["perStyle"] / max(o["subCategoryPerStyle"], 1e-9)))
    return out


# --------------------------------------------------------------------------- #
# Trend status: trending / in trend / starting to fade / out of trend
# --------------------------------------------------------------------------- #
LIFECYCLE_ATTRS = ("category", "subCategory", "colour", "fabric")
# Shown in Trend status (2026-10-10, user-requested: Trend status is the one
# place for factor trends - Neck moved here from "Trending now"). Neck is
# display-only: the scores, suggestions and model use LIFECYCLE_ATTRS.
TREND_STATUS_ATTRS = (*LIFECYCLE_ATTRS, "neck")


def _lifecycle(frame, styles, snap: date) -> dict:
    """Per Category / Sub Category / Colour / Fabric value: its share of sales
    in each of the last LIFE_BLOCKS blocks of TREND_CHUNKS report weeks
    (oldest -> newest; ``frame`` from _chunk_frame - channel-weighted, the
    running week completed by its forecast) and its stage, by the shared rules:

    - trending: share up >= TREND_STRONG vs the block before (= the trend)
    - out:      not on sale now, or share <= OUT_OF_PEAK x its own peak and not recovering
    - fading:   share down >= TREND_STRONG vs the block before (still above OUT_OF_PEAK)
    - steady:   in trend - none of the above
    Values that never reached ON_SALE_MIN_UNITS in a block are left out."""
    o_snap = _chunk_ord(snap)
    span = LIFE_BLOCKS * TREND_CHUNKS
    recent = frame[frame["offset"] > -span].copy()
    recent["block"] = (recent["offset"] + span - 1) // TREND_CHUNKS
    totals = recent.groupby("block")["adj"].sum().reindex(range(LIFE_BLOCKS), fill_value=0.0)
    attrs = styles.reindex(recent["style"].unique()).fillna("")
    blocks = []
    for k in range(LIFE_BLOCKS):
        first_o = o_snap - (LIFE_BLOCKS - k) * TREND_CHUNKS + 1
        last_o = o_snap - (LIFE_BLOCKS - 1 - k) * TREND_CHUNKS
        blocks.append({"from": _chunk_bounds(first_o)[0].isoformat(), "to": _chunk_bounds(last_o)[1].isoformat(),
                       "weeks": f"{_chunk_label(first_o)} – {_chunk_label(last_o)}"})
    out = {"blocks": blocks}
    for attr in TREND_STATUS_ATTRS:
        if attr not in attrs.columns:
            out[attr] = []
            continue
        fv = recent.assign(value=recent["style"].map(attrs[attr])).dropna(subset=["value"])
        fv = fv[fv["value"] != ""]
        units = fv.groupby(["value", "block"])["qty"].sum().unstack(fill_value=0.0).reindex(columns=range(LIFE_BLOCKS), fill_value=0.0)
        adj = (fv.groupby(["value", "block"])["adj"].sum().unstack(fill_value=0.0)
               .reindex(index=units.index, columns=range(LIFE_BLOCKS), fill_value=0.0))
        real_now = fv[(fv["block"] == LIFE_BLOCKS - 1) & ~fv["proj"] & (fv["qty"] > 0)]
        active_now = real_now.groupby("value")["style"].nunique()
        real_units_now = real_now.groupby("value")["qty"].sum()
        rows = []
        for value, u in units.iterrows():
            if u.max() < ON_SALE_MIN_UNITS:
                continue
            a = adj.loc[value]
            share = [float(a[k] / totals[k] * 100) if totals[k] else 0.0 for k in range(LIFE_BLOCKS)]
            now, before, peak = share[-1], share[-2], max(share)
            change = (now / before - 1) * 100 if u[LIFE_BLOCKS - 2] >= TREND_MIN_UNITS and before > 0 else None
            on_sale = u[LIFE_BLOCKS - 1] >= ON_SALE_MIN_UNITS and int(active_now.get(value, 0)) >= MIN_STYLES
            move = _direction(change)
            if not on_sale:
                stage = "out"
            elif move > 0:
                stage = "trending"
            elif peak > 0 and now <= OUT_OF_PEAK * peak and (change is None or change <= 0):
                stage = "out"                                   # far below its best and not recovering
            elif move < 0:
                stage = "fading"
            else:
                stage = "steady"
            rows.append({
                "value": str(value), "stage": stage, "share": [round(x, 2) for x in share],
                "nowShare": round(now, 2), "change": None if change is None else round(change, 1),
                "fromPeakPct": round((now / peak - 1) * 100, 1) if peak else None,
                "peakBlock": share.index(peak), "nowUnits": int(round(u[LIFE_BLOCKS - 1])),
                # real pieces sold so far vs the running week's expected rest (its forecast)
                "realNowUnits": int(round(real_units_now.get(value, 0.0))),
                "expectedUnits": int(round(u[LIFE_BLOCKS - 1] - real_units_now.get(value, 0.0))),
                "activeStyles": int(active_now.get(value, 0)),
                "onSale": bool(on_sale), "gain": round(now - before, 2),
            })
        # Trending: biggest gain in share first, so a tiny value jumping from
        # 0.02% to 0.2% does not outrank a real mover.
        order = {"trending": 0, "steady": 1, "fading": 2, "out": 3}
        rows.sort(key=lambda r: (order[r["stage"]], -r["gain"] if r["stage"] == "trending" else -r["nowShare"]))
        out[attr] = rows
    return out


def _spikes(frame, styles, snap: date) -> tuple[dict, dict]:
    """({attr: {value: {"ratio", "lastWeekUnits", "lastWeekShare", "usualShare",
    "activeStyles", "new"}}}, window) for values whose share of one report
    week's sales is >= SPIKE_RATIO x (and >= SPIKE_MIN_GAIN points above)
    their share of the SPIKE_BASE_CHUNKS weeks before. The week: the running
    one (completed by its forecast) once it has SPIKE_MIN_ACTUAL_DAYS of real
    sales, else the last complete one. Channel-weighted shares (_chunk_frame);
    on-sale floor as everywhere: ON_SALE_MIN_UNITS and MIN_STYLES designs
    really selling. "new" = (almost) nothing in the weeks before."""
    o_snap = _chunk_ord(snap)
    start, end = _chunk_bounds(o_snap)
    real_days = (snap - start).days + 1
    k = 0 if real_days >= SPIKE_MIN_ACTUAL_DAYS else -1
    now = frame[frame["offset"] == k]
    base = frame[(frame["offset"] < k) & (frame["offset"] >= k - SPIKE_BASE_CHUNKS)]
    t_now, t_base = float(now["adj"].sum()), float(base["adj"].sum())
    w_start, w_end = _chunk_bounds(o_snap + k)
    window = {"week": _chunk_label(o_snap + k), "from": w_start.isoformat(), "to": w_end.isoformat(),
              "baseWeeks": f"{_chunk_label(o_snap + k - SPIKE_BASE_CHUNKS)} – {_chunk_label(o_snap + k - 1)}",
              "includesForecast": bool(k == 0 and snap < end)}
    out = {}
    if t_now <= 0 or t_base <= 0:
        return {a: {} for a in TREND_STATUS_ATTRS}, window
    for attr in TREND_STATUS_ATTRS:
        value_of = styles[attr] if attr in styles.columns else None
        if value_of is None:
            out[attr] = {}
            continue
        n = now.assign(value=now["style"].map(value_of)).dropna(subset=["value"])
        b = base.assign(value=base["style"].map(value_of)).dropna(subset=["value"])
        n, b = n[n["value"] != ""], b[b["value"] != ""]
        units_now = n.groupby("value")["qty"].sum()
        adj_now = n.groupby("value")["adj"].sum()
        active = n[~n["proj"] & (n["qty"] > 0)].groupby("value")["style"].nunique()
        units_base = b.groupby("value")["qty"].sum()
        adj_base = b.groupby("value")["adj"].sum()
        found = {}
        for value, u in units_now.items():
            if u < ON_SALE_MIN_UNITS or int(active.get(value, 0)) < MIN_STYLES:
                continue
            share_now = float(adj_now.get(value, 0.0)) / t_now * 100
            share_base = float(adj_base.get(value, 0.0)) / t_base * 100
            new = float(units_base.get(value, 0.0)) < ON_SALE_MIN_UNITS
            ratio = share_now / share_base if share_base > 0 else None
            if share_now - share_base < SPIKE_MIN_GAIN:
                continue
            if new or (ratio is not None and ratio >= SPIKE_RATIO):
                found[str(value)] = {"ratio": None if new or ratio is None else round(ratio, 2),
                                     "lastWeekUnits": int(round(u)), "lastWeekShare": round(share_now, 2),
                                     "usualShare": round(share_base, 2), "activeStyles": int(active.get(value, 0)),
                                     "new": bool(new)}
        out[attr] = found
    return out, window


def _stage_maps(lifecycle: dict | None) -> dict[str, dict[str, str]]:
    """{attr: {value: stage}} from _lifecycle."""
    return {a: {r["value"]: r["stage"] for r in (lifecycle or {}).get(a, [])} for a in LIFECYCLE_ATTRS}


# --------------------------------------------------------------------------- #
# Trending now: styles and attribute values gaining in the last TREND_CHUNKS report weeks
# --------------------------------------------------------------------------- #
def _trending_now(sales, styles, snap: date, trend, mom=None) -> dict:
    """Trending designs: the styles gaining the most in the last TREND_CHUNKS
    report weeks vs the TREND_CHUNKS before (new launches included) - same
    trend numbers and TREND_STRONG rule as everything else. Factor trends
    live in Trend status only (2026-10-10, user-requested: no duplicate
    lists). Pieces shown are REAL sales, with the running week's expected
    rest (its forecast) apart."""
    import pandas as pd
    first_sale = sales.groupby("style")["date"].min()
    launch_cut = pd.Timestamp(snap) - pd.Timedelta(days=NEW_LAUNCH_DAYS)
    rows = []
    for st, v in trend.items():
        # The pieces the table shows - real sold so far + the running week's
        # expected rest vs real pieces before - so the growth, the order and
        # the TREND_STRONG cut can all be checked from the row itself.
        real, real_before, expected = (v[2], v[3], v[4]) if len(v) >= 5 else (v[0], v[1], 0.0)
        now, before = real + expected, real_before
        if real < ON_SALE_MIN_UNITS or now < (1 + TREND_STRONG / 100) * before:
            continue
        a = styles.loc[st] if st in styles.index else None
        rows.append({
            "style": st, "nowUnits": int(round(real)), "expectedUnits": int(round(expected)),
            "beforeUnits": int(round(real_before)),
            "growthPct": round((now / before - 1) * 100, 1) if before >= 10 else None,
            "gain": now - before,
            "newLaunch": bool(st in first_sale.index and first_sale[st] >= launch_cut),
            **{k: (str(a[k]) if a is not None and a[k] else "") for k in ("subCategory", "colour", "fabric", "neck", "sleeve")},
        })
    rows.sort(key=lambda r: r["gain"], reverse=True)
    for r in rows:
        r.pop("gain")
    o = _chunk_ord(snap)
    return {
        "windowWeeks": TREND_CHUNKS,
        "nowFrom": _chunk_bounds(o - TREND_CHUNKS + 1)[0].isoformat(),
        "nowTo": _chunk_bounds(o)[1].isoformat(),
        "beforeFrom": _chunk_bounds(o - 2 * TREND_CHUNKS + 1)[0].isoformat(),
        "beforeTo": _chunk_bounds(o - TREND_CHUNKS)[1].isoformat(),
        "nowWeeks": f"{_chunk_label(o - TREND_CHUNKS + 1)} – {_chunk_label(o)}",
        "beforeWeeks": f"{_chunk_label(o - 2 * TREND_CHUNKS + 1)} – {_chunk_label(o - TREND_CHUNKS)}",
        "runningWeek": _chunk_label(o),
        "styles": rows[:20],
    }


# --------------------------------------------------------------------------- #
# CatBoost: attribute effects + combination scoring
# --------------------------------------------------------------------------- #
_ATTR_FEATURES = ["season", "category", "subCategory", "colour", "fabric", "embroidery", "neck", "sleeve", "topType"]
# Trend stage of the style's category / sub category / colour / fabric when the
# season STARTED (from sales before it only - no peeking): lets the model learn
# how much "trending" vs "out of trend" changes a season's sales. Predictions
# use the stage as of today. "none" = too small / not sold in those months.
_STAGE_FEATURES = [f"{a}Stage" for a in LIFECYCLE_ATTRS]
_FEATURES = _ATTR_FEATURES + _STAGE_FEATURES
_NUMERIC = ["priceBand", "ageQuarters"]

# Cross-validated tuning (see _tune). The page is first served from a quick
# default fit; the CV search runs afterwards in the background, its winning
# settings are saved and reused for TUNE_MAX_AGE_DAYS before re-tuning.
CV_FOLDS = 5
CV_MAX_ITER = 1500
TUNE_MAX_AGE_DAYS = 7
# one_hot_max_size=255 one-hot encodes every attribute (colour has ~150
# values) instead of CatBoost's target statistics - measured ~25-40x faster
# per fit here, which is what makes a 60-fit cross-validation affordable.
_BASE_PARAMS = dict(learning_rate=0.03, one_hot_max_size=255, random_seed=42, verbose=False, allow_writing_files=False)
_DEFAULT_PARAMS = {"loss_function": "Tweedie:variance_power=1.5", "depth": 6, "l2_leaf_reg": 10}
CV_GRID = [
    {"loss_function": f"Tweedie:variance_power={vp}", "depth": d, "l2_leaf_reg": l2}
    for vp in (1.3, 1.5, 1.7) for d in (4, 6) for l2 in (3, 10)
]
_TUNING_FILE = Path(__file__).resolve().parent / ".cache" / "design_model_tuning.json"


def _train_model(sales, styles, price, snap: date, focus: set | None = None):
    """CatBoost (Poisson) on one row per style x COMPLETED season occurrence
    since the style's first sale (0 units included). ``sales`` covers every
    TRAIN_TIERS style; ``focus`` (the TIERS styles the page suggests for) is
    marked per row - accuracy is reported and tuned on those rows. Returns
    None if CatBoost is unavailable or there is too little data."""
    try:
        from catboost import CatBoostRegressor, Pool
    except Exception as exc:  # noqa: BLE001
        print(f"[design_suggest] CatBoost unavailable ({exc!r}) - rankings only", file=sys.stderr)
        return None
    import numpy as np
    import pandas as pd

    # Completed seasons with FULL data only: a season the history starts in
    # the middle of (S2 2025 - sales data begins 18 Mar 2025) would teach the
    # model that its styles sold about half of what they really did
    # (2026-10-09 data diagnosis).
    history_start = sales["date"].min().date()
    occ = sales[["season", "seasonYear"]].drop_duplicates()
    occ = [(s, int(y)) for s, y in occ.itertuples(index=False)
           if season_bounds(s, int(y))[1] < snap and season_bounds(s, int(y))[0] >= history_start - timedelta(days=3)]
    if len(occ) < 2:
        return None
    first_sale = sales.groupby("style")["date"].min()
    units = sales.groupby(["style", "season", "seasonYear"])["qty"].sum()
    prices = pd.Series(price)
    bands = pd.qcut(prices, 5, labels=False, duplicates="drop") if len(prices) >= 10 else pd.Series(dtype=float)

    rows = []
    for s, y in occ:
        start, end = season_bounds(s, y)
        day_before = start - timedelta(days=1)            # a month end: its report week is complete
        stage_at_start = _stage_maps(_lifecycle(_chunk_frame(sales, day_before), styles, day_before))
        eligible = first_sale[first_sale <= pd.Timestamp(end)].index
        for st in eligible:
            if st not in styles.index:
                continue
            a = styles.loc[st]
            age_q = max(0.0, (pd.Timestamp(start) - first_sale[st]).days / 91.0)
            rows.append({
                "season": s, **{k: (a[k] or "Unknown") for k in _ATTR_FEATURES[1:]},
                **{f"{k}Stage": stage_at_start[k].get(a[k], "none") for k in LIFECYCLE_ATTRS},
                "priceBand": float(bands.get(st, np.nan)) if len(bands) else np.nan,
                "ageQuarters": round(age_q, 1),
                "y": float(units.get((st, s, y), 0.0)), "style": st, "occ": f"{s} {y}",
            })
    df = pd.DataFrame(rows)
    if len(df) < 200 or df["y"].sum() <= 0:
        return None
    df["focus"] = df["style"].isin(focus) if focus is not None else True

    # Tweedie (more stable than Poisson on a few very large sellers) on units
    # capped at the 99th percentile - an uncapped Poisson fit on all rows was
    # seen to diverge (predictions ~1e185) on this data.
    cap = float(df["y"].quantile(0.99))
    df["y"] = df["y"].clip(upper=cap)
    X, y = df[_FEATURES + _NUMERIC], df["y"]
    seasons = [season_label(s, yr) for s, yr in sorted(occ, key=lambda t: (t[1], _ORDER.index(t[0])))]
    base_info = {"type": "CatBoost (Tweedie)", "rows": int(len(df)), "styles": int(df["style"].nunique()), "seasons": seasons,
                 "trainingTiers": list(TRAIN_TIERS or ()), "focusTiers": list(TIERS or ()),
                 "focusRows": int(df["focus"].sum()), "focusStyles": int(df.loc[df["focus"], "style"].nunique())}

    tuned = _load_tuning()
    final, info = None, None
    if tuned:
        # Settings chosen by the background cross-validation (_tune) - one fit.
        final = CatBoostRegressor(**{**_BASE_PARAMS, **tuned["params"]})
        final.fit(Pool(X, y, cat_features=_FEATURES))
        if _unstable(final.predict(X), cap):
            print("[design_suggest] tuned refit unstable - falling back to the quick fit", file=sys.stderr)
            final = None
        else:
            info = {**base_info, **tuned["stats"], "params": tuned["params"]}
    if final is None:
        final, info = _quick_fit(X, y, df["style"], df["focus"].to_numpy(), cap, CatBoostRegressor, Pool,
                                 df["occ"].to_numpy())
        info = {**base_info, **info}
    shap = final.get_feature_importance(Pool(X, y, cat_features=_FEATURES), type="ShapValues")
    print(f"[design_suggest] CatBoost on {len(df):,} style-season rows - {info['validation']}: "
          f"WAPE {info['holdoutWape']:.1f}% vs {info['baselineWape']:.1f}% for a flat average", file=sys.stderr)
    return {
        "model": final, "rows": df, "shap": shap[:, :-1], "bands": bands,
        "cap": cap, "needsTuning": tuned is None,
        "info": info,
    }


def _top_pick_hit(y, pred, occ) -> float | None:
    """Of the designs the model ranked in the top 20% of a season, the % that
    really were in that season's top 20% (random picking ~20%). Pooled over
    the season occurrences in the test rows."""
    import numpy as np
    import pandas as pd
    frame = pd.DataFrame({"y": np.asarray(y, dtype=float), "p": np.asarray(pred, dtype=float), "occ": np.asarray(occ)})
    hits = picks = 0
    for _, g in frame.groupby("occ"):
        n = int(len(g) * 0.2)
        if n < 5:
            continue
        top_p = set(g.nlargest(n, "p").index)
        top_y = set(g.nlargest(n, "y").index)
        hits += len(top_p & top_y)
        picks += n
    return round(hits / picks * 100, 1) if picks else None


def _unstable(pred, cap: float) -> bool:
    import numpy as np
    return not np.isfinite(pred).all() or float(pred.max()) > 20 * max(cap, 1.0)


def _quick_fit(X, y, style, focus, cap, CatBoostRegressor, Pool, occ):
    """Fast default fit used until cross-validated settings exist: one
    style-grouped 80/20 holdout for early stopping + an honest error figure
    (on the held-out FOCUS-tier styles, plus all tiers)."""
    import numpy as np
    test = _style_folds(style, 5)[0]
    params = {**_BASE_PARAMS, **_DEFAULT_PARAMS, "iterations": CV_MAX_ITER}
    m = CatBoostRegressor(**params)
    m.fit(Pool(X[~test], y[~test], cat_features=_FEATURES), eval_set=Pool(X[test], y[test], cat_features=_FEATURES),
          early_stopping_rounds=60)
    yv, pred, train_mean = y.to_numpy(), m.predict(X[test]), float(y[~test].mean())
    ft = focus[test]
    yt, yf = yv[test], yv[test][ft]
    wape = float(np.abs(yf - pred[ft]).sum() / max(1e-9, yf.sum()) * 100)
    base = float(np.abs(yf - train_mean).sum() / max(1e-9, yf.sum()) * 100)
    wape_all = float(np.abs(yt - pred).sum() / max(1e-9, yt.sum()) * 100)
    hit = _top_pick_hit(yt[ft], pred[ft], occ[test][ft])
    best_iter = max(50, int(m.get_best_iteration() or CV_MAX_ITER))
    final = CatBoostRegressor(**{**params, "iterations": best_iter})
    final.fit(Pool(X, y, cat_features=_FEATURES))
    if _unstable(final.predict(X), cap):
        print("[design_suggest] refit unstable - using the validated holdout model", file=sys.stderr)
        final = m
    return final, {"holdoutWape": round(wape, 1), "baselineWape": round(base, 1), "allTiersWape": round(wape_all, 1),
                   "topPickHitPct": hit,
                   "validation": "single 80/20 holdout (cross-validation pending)"}


def _tune(df) -> dict | None:
    """Style-grouped K-fold cross-validation over CV_GRID (2026-10-08,
    user-requested). Each style is held out exactly once and never sits in
    train and test together. Every parameter set is trained once per fold
    for CV_MAX_ITER trees and scored on its out-of-fold predictions at several
    tree counts; the set + tree count with the lowest pooled WAPE on the
    FOCUS-tier rows (the styles the page suggests for) wins - models train on
    every tier. Runs in the background (see _build_and_store) - it is ~60
    fits, too slow to make the page wait for."""
    from catboost import CatBoostRegressor, Pool
    import numpy as np
    t0 = time.time()
    X, y = df[_FEATURES + _NUMERIC], df["y"]
    yv, focus = y.to_numpy(), df["focus"].to_numpy(dtype=bool)
    folds = _style_folds(df["style"], CV_FOLDS)
    checkpoints = list(range(100, CV_MAX_ITER + 1, 100))
    abs_err: dict[tuple, float] = {}       # focus rows - the selection metric
    abs_err_all: dict[tuple, float] = {}   # every tier - reported alongside
    fold_wapes: dict[tuple, list[float]] = {}
    base_err = sum(float(np.abs(yv[t & focus] - yv[~t].mean()).sum()) for t in folds)
    for gi, grid in enumerate(CV_GRID):
        for t in folds:
            m = CatBoostRegressor(**{**_BASE_PARAMS, **grid, "iterations": CV_MAX_ITER})
            m.fit(Pool(X[~t], y[~t], cat_features=_FEATURES))
            yt, ft = yv[t], focus[t]
            for k in checkpoints:
                diff = np.abs(yt - m.predict(X[t], ntree_end=k))
                err = float(diff[ft].sum())
                abs_err[(gi, k)] = abs_err.get((gi, k), 0.0) + err
                abs_err_all[(gi, k)] = abs_err_all.get((gi, k), 0.0) + float(diff.sum())
                fold_wapes.setdefault((gi, k), []).append(err / max(1e-9, yt[ft].sum()) * 100)
    if not abs_err:
        return None
    total = max(1e-9, float(yv[focus].sum()))
    gi, k = min(abs_err, key=abs_err.get)
    params = {**CV_GRID[gi], "iterations": int(k)}
    # Ranking check for the winner: out-of-fold predictions, top-20% hit rate.
    oof = np.zeros(len(yv))
    for t in folds:
        m = CatBoostRegressor(**{**_BASE_PARAMS, **params})
        m.fit(Pool(X[~t], y[~t], cat_features=_FEATURES))
        oof[t] = m.predict(X[t])
    hit = _top_pick_hit(yv[focus], oof[focus], df["occ"].to_numpy()[focus])
    stats = {"holdoutWape": round(abs_err[(gi, k)] / total * 100, 1), "baselineWape": round(base_err / total * 100, 1),
             "allTiersWape": round(abs_err_all[(gi, k)] / max(1e-9, float(yv.sum())) * 100, 1),
             "validation": f"{CV_FOLDS}-fold cross-validation, grouped by style",
             "cvFolds": CV_FOLDS, "cvFoldWape": [round(float(w), 1) for w in fold_wapes[(gi, k)]],
             "paramSetsTried": len(CV_GRID), "tunedOn": date.today().isoformat(), "topPickHitPct": hit}
    print(f"[design_suggest] cross-validation done in {time.time() - t0:.0f}s: best {params}, "
          f"WAPE {stats['holdoutWape']}% (folds {stats['cvFoldWape']}) vs {stats['baselineWape']}% flat average",
          file=sys.stderr)
    return {"params": params, "stats": stats}


def _load_tuning() -> dict | None:
    """Saved cross-validated settings, if tuned within TUNE_MAX_AGE_DAYS."""
    try:
        saved = json.loads(_TUNING_FILE.read_text(encoding="utf-8"))
        age = (date.today() - date.fromisoformat(saved["stats"]["tunedOn"])).days
        return saved if 0 <= age <= TUNE_MAX_AGE_DAYS and saved.get("grid") == _grid_id() else None
    except Exception:  # noqa: BLE001 - missing / old / unreadable file = not tuned
        return None


def _save_tuning(tuned: dict) -> None:
    try:
        _TUNING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TUNING_FILE.write_text(json.dumps({**tuned, "grid": _grid_id()}, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[design_suggest] could not save tuning ({exc!r})", file=sys.stderr)


def _grid_id() -> str:
    """Changes when the search space changes, so old settings get re-tuned."""
    return json.dumps([CV_FOLDS, CV_MAX_ITER, _BASE_PARAMS["learning_rate"], CV_GRID, TRAIN_TIERS, TIERS,
                       _TRAINING_ROWS_VERSION], sort_keys=True)


# Bump when the training rows change, so saved settings get re-tuned.
_TRAINING_ROWS_VERSION = "2026-10-10 full seasons only, dashboard sub category names, report-week trend stage features, top-pick check"


def _style_folds(style, n_folds: int) -> list:
    """Boolean test masks for K folds that split by STYLE (all of a style's
    season rows land in the same fold), shuffled with a fixed seed."""
    import numpy as np
    uniq = np.array(sorted(style.astype(str).unique()), dtype=object)
    uniq = uniq[np.random.default_rng(42).permutation(len(uniq))]
    fold_of = {s: i % n_folds for i, s in enumerate(uniq)}
    ids = style.astype(str).map(fold_of).to_numpy()
    return [ids == i for i in range(n_folds)]


def _drivers(model, code: str, rankings: dict | None = None) -> list[dict]:
    """Average SHAP effect of each attribute value in rows of this season,
    as a multiplicative % (Poisson log link): +20 = sells 20% more. Only
    values on sale now (present in the season's rankings) are listed."""
    import numpy as np
    df, shap = model["rows"], model["shap"]
    mask = ((df["season"] == code) & df["focus"]).to_numpy()   # effects on the suggested-tier styles
    if mask.sum() < 30:
        return []
    out = []
    for j, feat in enumerate(_FEATURES + _NUMERIC):
        if feat in ("season", "ageQuarters", "priceBand") or feat in _STAGE_FEATURES:
            continue
        col = df.loc[mask, feat].astype(str)
        vals = shap[mask, j]
        for value, idx in col.groupby(col).groups.items():
            if value == "Unknown" or len(idx) < 5:
                continue
            if rankings is not None and feat in rankings and value not in {r["value"] for r in rankings[feat]}:
                continue   # not on sale now
            eff = float(np.mean(vals[col.index.get_indexer(idx)]))
            out.append({"attribute": feat, "value": str(value), "effectPct": round(float((np.exp(eff) - 1) * 100), 1), "rows": int(len(idx))})
    out.sort(key=lambda d: d["effectPct"], reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Product cards: one per Sub Category (2026-10-10, user-requested: one
# connected suggestion per product instead of separate sections that did not
# agree with each other)
# --------------------------------------------------------------------------- #
CARD_COLOURS = 4
CARD_FABRICS = 2
CARD_MIXES = 3
CARD_DETAILS = ("embroidery", "neck", "sleeve", "topType")
# Strength labels instead of unit numbers: the model ranks well (backtest)
# but its unit numbers are rough, so only relative levels are shown.
STRONG_SCORE, GOOD_SCORE = 70, 50          # product score (0-100)
STRONG_MIX, GOOD_MIX = 15.0, -10.0          # mix vs this product's average mix, %


def _level(value: float, strong: float, good: float) -> str:
    return "strong" if value >= strong else "good" if value >= good else "trySmall"


def _products(block: dict, sc_rank: dict, model, code: str, joined_all, stages: dict, avg_now: float) -> list[dict]:
    """One card per suggested Sub Category (the Sub Category ranking: trending
    / in trend only), every part from that product's OWN ranking (_subcategory_
    rankings), so the parts always agree:

    - use: its best colours / fabrics (trending / in trend, not falling) and
      details; Make more items are always among them
    - mixes: the model's best colour x fabric pairs made ONLY from those
      colours / fabrics, as a level vs this product's average mix
    - makeMore: Design more items (sells >= GAP_RATIO x average, few designs)
    - cutBack: its colours / fabrics that are fading / out of trend, losing
      steam or saturated - never one of the "use" values."""
    import pandas as pd
    opps = block.get("opportunities", [])
    existing = set(zip(joined_all["subCategory"], joined_all["colour"], joined_all["fabric"]))
    category_of = joined_all.groupby("subCategory")["category"].agg(lambda c: c.mode().iloc[0] if not c.mode().empty else "")

    def usable(x) -> bool:
        return _direction(x["trend"]) >= 0 and _suggestable(x)

    cards = []
    for sc_row in block["rankings"].get("subCategory", []):
        sc = sc_row["value"]
        info = sc_rank.get(sc)
        if not info:
            continue
        r = info["rankings"]
        mine = [o for o in opps if o["subCategory"] == sc]
        make_more = [o for o in mine if o["type"] == "gap"][:2]
        use = {}
        for attr, k in (("colour", CARD_COLOURS), ("fabric", CARD_FABRICS)):
            rows = [x for x in r.get(attr, []) if usable(x)]
            chosen = rows[:k]
            for o in make_more:   # a Make more value is always one to use
                if o["attribute"] == attr and o["value"] not in {x["value"] for x in chosen}:
                    chosen += [x for x in rows if x["value"] == o["value"]]
            use[attr] = [{"value": x["value"], "stage": x.get("stage"), "trend": x["trend"]} for x in chosen]
        details = {}
        for attr in CARD_DETAILS:
            best = next((x for x in r.get(attr, []) if usable(x)), None)
            if best:
                details[attr] = best["value"]

        # Mixes: only the colours / fabrics above, ranked by the model against
        # this product's average over a wider set of its usable colours / fabrics.
        mixes = []
        if model and use["colour"] and use["fabric"]:
            cat = category_of.get(sc, "") or "Unknown"
            wide_c = [x for x in r.get("colour", []) if usable(x)][:12]
            wide_f = [x for x in r.get("fabric", []) if usable(x)][:6]
            listed = {(c["value"], f["value"]) for c in use["colour"] for f in use["fabric"]}
            cands = [{"season": code, "category": cat, "subCategory": sc, "colour": c["value"], "fabric": f["value"],
                      **{k: details.get(k, "Unknown") for k in CARD_DETAILS}, "priceBand": 2.0, "ageQuarters": 2.0,
                      "categoryStage": stages.get("category", {}).get(cat, "none"),
                      "subCategoryStage": stages.get("subCategory", {}).get(sc, "none"),
                      "colourStage": c.get("stage") or "none", "fabricStage": f.get("stage") or "none"}
                     for c in wide_c for f in wide_f]
            cdf = pd.DataFrame(cands)
            cdf["predicted"] = model["model"].predict(cdf[_FEATURES + _NUMERIC]).clip(0, model["cap"])
            avg = float(cdf["predicted"].mean())
            cdf = cdf[[(x.colour, x.fabric) in listed for x in cdf.itertuples()]].sort_values("predicted", ascending=False)
            for x in cdf.head(CARD_MIXES).itertuples():
                vs = (float(x.predicted) / avg - 1) * 100 if avg > 0 else 0.0
                mixes.append({"colour": x.colour, "fabric": x.fabric, "tried": (sc, x.colour, x.fabric) in existing,
                              "level": _level(vs, STRONG_MIX, GOOD_MIX), "vsAveragePct": round(vs, 1)})

        used = {(a, u["value"]) for a in use for u in use[a]}
        cut = []
        for attr in ("colour", "fabric"):   # fading / out of trend, still being made here
            for x in r.get(attr, []):
                if x.get("stage") in ("fading", "out") and x["activeStyles"] >= MIN_STYLES and (attr, x["value"]) not in used:
                    cut.append({"attribute": attr, "value": x["value"], "reason": x["stage"], "styles": x["activeStyles"]})
        for o in mine:
            if o["type"] in ("declining", "saturated") and (o["attribute"], o["value"]) not in used:
                cut.append({"attribute": o["attribute"], "value": o["value"], "reason": o["type"], "styles": o["styles"]})
        seen, cut_back = set(), []
        for c in sorted(cut, key=lambda c: -c["styles"]):
            if (c["attribute"], c["value"]) not in seen:
                seen.add((c["attribute"], c["value"]))
                cut_back.append(c)

        per_design = sc_row["nowUnits"] / max(1, sc_row["activeStyles"])
        cards.append({
            "subCategory": sc, "category": category_of.get(sc, "") or "", "stage": sc_row.get("stage"), "score": sc_row["score"],
            "level": _level(sc_row["score"], STRONG_SCORE, GOOD_SCORE),
            "nowShare": sc_row["nowShare"], "activeStyles": sc_row["activeStyles"],
            "vsAverage": round(per_design / avg_now, 2) if avg_now > 0 else None,
            "lastSeason": sc_row["lastSeason"], "lift": sc_row["lift"], "trend": sc_row["trend"],
            "use": {**use, "details": details}, "mixes": mixes,
            "makeMore": [{"attribute": o["attribute"], "value": o["value"], "styles": o["styles"],
                          "times": round(o["perStyle"] / max(o["subCategoryPerStyle"], 1e-9), 1)} for o in make_more],
            "cutBack": cut_back[:4],
        })
    return cards


def _overview(cards: list[dict]) -> dict:
    """The tab's top line - built only from the cards: the first 3 products,
    and the colours / fabrics listed on the top cards, weighted by each
    product's share of sales (so a 1%-of-sales product does not outvote a
    56% one)."""
    top = cards[:8]

    def most(attr: str, k: int) -> list[str]:
        weight, first = {}, {}
        for i, c in enumerate(top):
            for j, u in enumerate(c["use"][attr]):
                weight[u["value"]] = weight.get(u["value"], 0.0) + c["nowShare"]
                first.setdefault(u["value"], (i, j))
        return sorted(weight, key=lambda v: (-weight[v], first[v]))[:k]

    return {"focus": [c["subCategory"] for c in cards[:3]], "colours": most("colour", 3), "fabrics": most("fabric", 2)}
