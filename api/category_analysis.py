"""
Category Analysis reports (Sub Category, Category and Tiers Spike Rate tabs):
monthly styles / orders / units / spike per group, the channel drill-down,
the styles behind a STYLES cell, the filtered chart totals, plus the style ->
tier / brand maps they share. Moved out of data.py (2026-10-30) unchanged;
the loaded data (sale rows, snapshot date, data version) is read from the
data module at call time, so reloads are picked up.
"""
from __future__ import annotations

import sys
from datetime import date

import data
import design_attributes


# Category / Sub Category monthly analysis (2026-10-03, user-requested):
# styles sold, orders, units and the month-on-month spike, from April 2025.
# Gross sale - the SAME units as the Weekly Sales Report's Actual Sale (one
# definition everywhere, 2026-10-03, user-requested); the spike is on units too.
_CATEGORY_ANALYSIS_START = date(2025, 4, 1)
_CATEGORY_LEVELS = ("category", "subCategory", "style", "tier")


def _style_tier_map() -> dict[str, str]:
    """{STYLE (upper-cased): current catalog tier} - Cloud SQL CatalogStyle,
    the same tiers as the Weekly Sales Report / Production Log. Empty if the
    catalog is unavailable."""
    try:
        import catalog_style
        return {str(k).strip().upper(): v for k, v in catalog_style.get_current_tier_map().items() if v}
    except Exception as exc:  # noqa: BLE001
        print(f"[data] catalog tiers unavailable ({exc!r})", file=sys.stderr)
        return {}
def _tier_at_sale(styles, dates):
    """Each sale's tier AT THE TIME OF SALE (2026-10-13, user-requested
    "Tier at time of sale" view): the style's latest Cloud SQL tier change on
    or before the sale date (CatalogStyleForecastHistory). Before a style's
    first recorded change - the log only starts 2026-06-01 - its CURRENT tier
    is used, as there is no history to say otherwise."""
    import numpy as np
    import pandas as pd
    key = styles.astype(str).str.strip().str.upper()
    base = key.map(_style_tier_map()).fillna("Unclassified").to_numpy()
    try:
        import catalog_style
        hist = catalog_style.get_tier_history()
    except Exception as exc:  # noqa: BLE001
        print(f"[data] tier history unavailable ({exc!r})", file=sys.stderr)
        hist = []
    if not hist:
        return pd.Series(base, index=styles.index)
    h = pd.DataFrame(hist)
    h["key"] = h["name"].astype(str).str.strip().str.upper()
    # One time resolution on both sides: pandas refuses to match e.g. [us] with [s].
    h["at"] = pd.to_datetime(h["effectiveFrom"]).astype("datetime64[ns]")
    h = h.drop_duplicates(["key", "at"], keep="last").sort_values("at")[["key", "at", "tier"]]
    q = pd.DataFrame({"key": key.to_numpy(), "at": pd.to_datetime(dates).astype("datetime64[ns]").to_numpy(),
                      "i": np.arange(len(key))}).sort_values("at")
    m = pd.merge_asof(q, h, on="at", by="key", direction="backward")
    tier = m.set_index("i")["tier"].reindex(np.arange(len(key))).to_numpy()
    return pd.Series(np.where(pd.notna(tier), tier, base), index=styles.index)


_CATEGORY_ANALYSIS_CACHE: dict[tuple, dict] = {}
_CATEGORY_CHANNELS_CACHE: dict[tuple, dict] = {}  # (data version, level, group) -> channel drill-down
_CATEGORY_CHANNEL_FRAME = None  # (data version, frame) - see _category_channel_frame()


def _category_filter_mask(styles, category: str = "", sub_category: str = "", brand: str = "", tier: str = "",
                          style: str = ""):
    """Boolean mask over a Series of style codes: styles in ``category`` and
    ``sub_category`` (blank = any) - the same names as the Category / Sub
    Category tables (design_attributes, "Unclassified" when unknown). Also
    ``brand``, current catalog ``tier`` and one ``style`` (2026-10-30,
    user-requested: the Brand / Tier / Category / Sub Category / Style filter
    bar applies to all three tables and their charts). None = no filter."""
    if not (category or sub_category or brand or tier or style):
        return None
    brand_of = _style_brand_map() if brand else {}
    tier_of = _style_tier_map() if tier else {}
    keep = {d for d in styles.unique()
            if (not category or (design_attributes.category_of(d) or "Unclassified") == category)
            and (not sub_category or (design_attributes.sub_category_of(d) or "Unclassified") == sub_category)
            and (not brand or brand_of.get(d, "Unclassified") == brand)
            and (not tier or (tier_of.get(d.strip().upper()) or "Unclassified") == tier)
            and (not style or d == style)}
    return styles.isin(keep)


def _category_filter_options(styles) -> dict[str, list[str]]:
    """{category: [its sub categories]} over the given style codes - the
    Tiers Spike Rate table's Category / Sub Category filter menus."""
    out: dict[str, set[str]] = {}
    for d in styles.unique():
        cat = design_attributes.category_of(d) or "Unclassified"
        out.setdefault(cat, set()).add(design_attributes.sub_category_of(d) or "Unclassified")
    return {c: sorted(v) for c, v in sorted(out.items())}


def get_category_analysis(level: str = "subCategory", category: str = "", sub_category: str = "",
                          tier_mode: str = "current", brand: str = "", tier: str = "", style: str = "") -> dict:
    """One row per (month, Category or Sub Category): distinct styles with a
    Gross sale that month, order count (order lines), units, and the units'
    % change vs the previous month for the same group. The current
    month is partial (data through SNAPSHOT_DATE), so it is compared with the
    SAME days of the previous month (1..day) instead of the whole month.
    ``category`` / ``sub_category`` keep only those styles (Tiers Spike Rate
    filters, 2026-10-13, user-requested); blank = all. ``tier_mode`` (tier
    level): "current" = every month under the style's current tier; "sale" =
    each sale under the tier it had at the time (_tier_at_sale)."""
    import pandas as pd

    if level not in _CATEGORY_LEVELS:
        raise ValueError(f"level must be one of {_CATEGORY_LEVELS}, got {level!r}")
    tier_mode = "sale" if level == "tier" and tier_mode == "sale" else "current"
    cache_key = (data._DATA_VERSION, level, category, sub_category, tier_mode, brand, tier, style)
    if cache_key in _CATEGORY_ANALYSIS_CACHE:
        return _CATEGORY_ANALYSIS_CACHE[cache_key]

    df = data._channel_source_base_df()
    out = {"level": level, "dataThrough": data.SNAPSHOT_DATE.isoformat(), "partialMonth": "", "rows": []}
    if df is None or df.empty:
        return out
    df = df[(df["order_date"] >= pd.Timestamp(_CATEGORY_ANALYSIS_START))
            & (df["order_date"] < pd.Timestamp(data.SNAPSHOT_DATE) + pd.Timedelta(days=1))]
    designs = df["DESIGN_NO"].astype(str)
    if level == "tier":
        out["filterOptions"] = _category_filter_options(designs)
    keep = _category_filter_mask(designs, category, sub_category, brand, tier, style)
    if keep is not None:
        df, designs = df[keep.to_numpy()], designs[keep.to_numpy()]
        if df.empty:
            return out
    if level == "style":
        # One row per style (Tiers Spike Rate tab, 2026-10-05, user-requested);
        # its Sub Category is sent alongside for display.
        mapping = {d: d for d in designs.unique()}
        plan_sub = {r.designNo: r.subCategory for r in data.PLAN_ROWS if r.designNo}
        out["subCategoryOf"] = {d: plan_sub.get(d) or design_attributes.sub_category_of(d) or "" for d in mapping}
    elif level == "tier":
        # One row per catalog tier (Tiers Spike Rate tab, 2026-10-09,
        # user-requested: T0 / T1 / T2 / T3 / T11 / T12 rows, each opening
        # into its styles); a style's CURRENT tier, "Unclassified" if none.
        tier_of = _style_tier_map()
        mapping = {d: tier_of.get(d.strip().upper()) or "Unclassified" for d in designs.unique()}
        out["tierOf"] = mapping
        out["tierMode"] = tier_mode
    else:
        group_of = design_attributes.category_of if level == "category" else design_attributes.sub_category_of
        mapping = {d: (group_of(d) or "Unclassified") for d in designs.unique()}
    # Months as "YYYY-MM" strings via the 12 possible period values (fast), and
    # whole-column operations below instead of a per-row loop (2026-10-05:
    # the style level has ~13k month x style rows).
    frame = pd.DataFrame({
        "month": df["_month"].astype(str),
        "group": _tier_at_sale(designs, df["order_date"]) if tier_mode == "sale" else designs.map(mapping),
        "qty": pd.to_numeric(df["qty"], errors="coerce").fillna(0),
        "day": df["order_date"].dt.day,
    })
    if level != "style":
        frame["style"] = designs
    rows, partial_month = _month_group_rows(frame, count_styles=level != "style")
    out.update(partialMonth=partial_month, rows=rows)
    if len(_CATEGORY_ANALYSIS_CACHE) > 64:
        _CATEGORY_ANALYSIS_CACHE.clear()
    _CATEGORY_ANALYSIS_CACHE[cache_key] = out
    return out


def _month_group_rows(frame, count_styles: bool) -> tuple[list[dict], str]:
    """Shared month x group aggregation behind get_category_analysis() and
    its channel drill-down: ``frame`` has month ("YYYY-MM"), group, qty, day
    (and style when ``count_styles``). Returns (rows, partial month or "")."""
    import pandas as pd

    grouped = frame.groupby(["month", "group"], sort=False)
    agg = grouped.agg(orders=("qty", "size"), units=("qty", "sum")).reset_index()
    agg["styles"] = grouped["style"].nunique().to_numpy() if count_styles else 1
    agg["units"] = agg["units"].round().astype(int)

    partial = f"{data.SNAPSHOT_DATE.year:04d}-{data.SNAPSHOT_DATE.month:02d}"
    is_partial = data.SNAPSHOT_DATE != (pd.Timestamp(data.SNAPSHOT_DATE) + pd.offsets.MonthEnd(0)).date()
    prev_partial = str(pd.Period(partial, freq="M") - 1)
    # Previous month's units: the whole previous month, except for the partial
    # current month, which is compared with the same days (1..day) of the previous one.
    agg["prev_month"] = (pd.PeriodIndex(agg["month"], freq="M") - 1).astype(str)
    prev_full = agg[["month", "group", "units"]].rename(columns={"month": "prev_month", "units": "prevUnits"})
    agg = agg.merge(prev_full, on=["prev_month", "group"], how="left")
    agg["prevUnits"] = agg["prevUnits"].astype("float64")
    agg["partial"] = bool(is_partial) & (agg["month"] == partial)
    if is_partial:
        same_days_prev = (frame[(frame["month"] == prev_partial) & (frame["day"] <= data.SNAPSHOT_DATE.day)]
                          .groupby("group")["qty"].sum().round())
        agg.loc[agg["partial"], "prevUnits"] = agg.loc[agg["partial"], "group"].map(same_days_prev).astype("float64").to_numpy()
    agg["prevUnits"] = agg["prevUnits"].fillna(0).astype(int)
    prev = agg["prevUnits"].where(agg["prevUnits"] > 0)
    agg["changePct"] = ((agg["units"] - prev) / prev * 100).round(1)
    same_cols = []
    if count_styles and "style" in frame.columns:
        agg = agg.merge(_same_style_figures(frame, partial, prev_partial, is_partial), on=["month", "group"], how="left")
        same_cols = ["stylesBoth", "sameUnits", "samePrevUnits", "newStyles", "newUnits", "stoppedStyles"]
        agg[same_cols] = agg[same_cols].fillna(0)
        sp = agg["samePrevUnits"].where(agg["samePrevUnits"] > 0)
        agg["sameChangePct"] = ((agg["sameUnits"] - sp) / sp * 100).round(1)
        agg["sameChangePct"] = agg["sameChangePct"].astype(object).where(agg["sameChangePct"].notna(), None)
        same_cols.append("sameChangePct")
    agg = agg.sort_values(["month", "units"], ascending=False)
    agg["changePct"] = agg["changePct"].astype(object).where(agg["changePct"].notna(), None)
    rows = agg[["month", "group", "styles", "orders", "units", "prevUnits", "changePct", "partial", *same_cols]].to_dict("records")
    for r in rows:  # plain Python types for JSON
        r["styles"], r["orders"], r["units"], r["prevUnits"] = int(r["styles"]), int(r["orders"]), int(r["units"]), int(r["prevUnits"])
        r["partial"] = bool(r["partial"])
        for k in ("stylesBoth", "sameUnits", "samePrevUnits", "newStyles", "newUnits", "stoppedStyles"):
            if k in r:
                r[k] = int(round(r[k]))
    return rows, (partial if is_partial else "")


def _same_style_figures(frame, partial: str, prev_partial: str, is_partial: bool):
    """Per (month, group): the SAME-STYLE spike's parts (2026-10-13,
    user-requested: styles are added and dropped every month, so total units
    alone do not compare like with like).

    stylesBoth / sameUnits / samePrevUnits - styles that sold this month AND the
        previous month, and their units now / before (the same-style spike).
        For the incomplete current month both sides are the same days (1..day)
        of the two months, so 12 days are never compared with 30.
    newStyles / newUnits - sold this month, not at all in the previous month
    stoppedStyles        - sold in the previous month, not this month
    New / stopped always compare with the WHOLE previous month (2026-10-13,
    user-requested), so previous styles - stopped + new = this month's styles,
    matching the Styles column next to it."""
    import pandas as pd

    su = frame.groupby(["month", "group", "style"], sort=False)["qty"].sum().reset_index()
    su["prev_month"] = (pd.PeriodIndex(su["month"], freq="M") - 1).astype(str)
    before = su[["month", "group", "style", "qty"]].rename(columns={"month": "prev_month", "qty": "prev"})
    su = su.merge(before, on=["prev_month", "group", "style"], how="left")
    su["prev_full"] = su["prev"].fillna(0.0)          # the whole previous month - new / stopped
    same_days = None
    if is_partial:
        same_days = (frame[(frame["month"] == prev_partial) & (frame["day"] <= data.SNAPSHOT_DATE.day)]
                     .groupby(["group", "style"])["qty"].sum().rename("sd").reset_index())
        su = su.merge(same_days, on=["group", "style"], how="left")
        cur = su["month"] == partial
        su.loc[cur, "prev"] = su.loc[cur, "sd"]
        su = su.drop(columns="sd")
    su["prev"] = su["prev"].fillna(0.0)
    both = (su["qty"] > 0) & (su["prev"] > 0)
    new = (su["qty"] > 0) & (su["prev_full"] <= 0)
    su["both"], su["new"] = both, new
    su["sameNow"] = su["qty"].where(both, 0.0)
    su["samePrev"] = su["prev"].where(both, 0.0)
    su["newQty"] = su["qty"].where(new, 0.0)
    out = su.groupby(["month", "group"]).agg(
        stylesBoth=("both", "sum"), sameUnits=("sameNow", "sum"), samePrevUnits=("samePrev", "sum"),
        newStyles=("new", "sum"), newUnits=("newQty", "sum"),
    ).reset_index()

    # Stopped: sold in the previous period, not this month.
    months = set(su["month"])
    prev_sets = su[["month", "group", "style"]].copy()
    prev_sets["month"] = (pd.PeriodIndex(prev_sets["month"], freq="M") + 1).astype(str)
    prev_sets = prev_sets[prev_sets["month"].isin(months)]
    now_sets = su[["month", "group", "style"]].assign(sold=True)
    stopped = prev_sets.merge(now_sets, on=["month", "group", "style"], how="left")
    stopped = stopped[stopped["sold"].isna()].groupby(["month", "group"]).size().rename("stoppedStyles").reset_index()
    return out.merge(stopped, on=["month", "group"], how="left")


def _category_channel_frame():
    """Every Gross sale row of the Category Analysis window as (month, group =
    marketplace, qty, day, style) - built once per _DATA_VERSION, so opening a
    row's channel drill-down only filters it (~0.2 s instead of ~1.5 s)."""
    global _CATEGORY_CHANNEL_FRAME
    if _CATEGORY_CHANNEL_FRAME is not None and _CATEGORY_CHANNEL_FRAME[0] == data._DATA_VERSION:
        return _CATEGORY_CHANNEL_FRAME[1]
    import pandas as pd

    df = data._channel_source_base_df()
    if df is None:
        return None
    df = df[(df["order_date"] >= pd.Timestamp(_CATEGORY_ANALYSIS_START))
            & (df["order_date"] < pd.Timestamp(data.SNAPSHOT_DATE) + pd.Timedelta(days=1))]
    # Marketplace per row, resolved once per distinct (channel_name, source).
    pair = df["channel_name"].astype(str) + "\x1f" + df["source"].astype(str)
    resolved = {p: data._marketplace_of(*p.split("\x1f", 1)) for p in pair.unique()}
    frame = pd.DataFrame({
        "month": df["_month"].astype(str),
        "group": pair.map(resolved),
        "qty": pd.to_numeric(df["qty"], errors="coerce").fillna(0),
        "day": df["order_date"].dt.day,
        "style": df["DESIGN_NO"].astype(str),
    })
    _CATEGORY_CHANNEL_FRAME = (data._DATA_VERSION, frame)
    return frame


def _category_group_mask(base, level: str, group: str, category: str = "", sub_category: str = "",
                         tier_mode: str = "current", brand: str = "", tier: str = "", style: str = ""):
    """Rows of _category_channel_frame() that belong to one Category / Sub
    Category / Style / Tier row of the Category Analysis tables (blank
    ``group`` = every row, i.e. the TOTAL row), after the Category / Sub
    Category filters."""
    import pandas as pd

    if not group:
        mask = pd.Series(True, index=base.index)
    elif level == "style":
        mask = base["style"] == group
    elif level == "tier" and tier_mode == "sale":
        sale_date = pd.to_datetime(base["month"] + "-" + base["day"].astype(int).astype(str).str.zfill(2))
        mask = pd.Series((_tier_at_sale(base["style"], sale_date) == group).to_numpy(), index=base.index)
    elif level == "tier":
        tier_of = _style_tier_map()
        members = {d for d in base["style"].unique() if (tier_of.get(d.strip().upper()) or "Unclassified") == group}
        mask = base["style"].isin(members)
    else:
        group_of = design_attributes.category_of if level == "category" else design_attributes.sub_category_of
        members = {d for d in base["style"].unique() if (group_of(d) or "Unclassified") == group}
        mask = base["style"].isin(members)
    keep = _category_filter_mask(base["style"], category, sub_category, brand, tier, style)
    return mask & keep if keep is not None else mask


def get_category_analysis_channels(level: str, group: str, category: str = "", sub_category: str = "",
                                   tier_mode: str = "current", brand: str = "", tier: str = "", style: str = "") -> dict:
    """Channel drill-down of one Category / Sub Category / Style row of the
    Category Analysis tables (2026-10-09, user-requested): the same monthly
    styles / orders / units / spike, one row per marketplace (Amazon,
    Flipkart, ... - the same rollup as the Weekly Sales Report's Channel &
    Source drill-down, _marketplace_of)."""
    import pandas as pd

    if level not in _CATEGORY_LEVELS:
        raise ValueError(f"level must be one of {_CATEGORY_LEVELS}, got {level!r}")
    tier_mode = "sale" if level == "tier" and tier_mode == "sale" else "current"
    cache_key = (data._DATA_VERSION, level, group, category, sub_category, tier_mode, brand, tier, style)
    if cache_key in _CATEGORY_CHANNELS_CACHE:
        return _CATEGORY_CHANNELS_CACHE[cache_key]

    out = {"level": level, "group": group, "dataThrough": data.SNAPSHOT_DATE.isoformat(), "partialMonth": "", "rows": []}
    base = _category_channel_frame()
    if base is None or base.empty:
        return out
    frame = base[_category_group_mask(base, level, group, category, sub_category, tier_mode, brand, tier, style)]
    if frame.empty:
        return out
    rows, partial_month = _month_group_rows(frame, count_styles=True)
    out.update(partialMonth=partial_month, rows=rows)
    if len(_CATEGORY_CHANNELS_CACHE) > 256:
        _CATEGORY_CHANNELS_CACHE.clear()
    _CATEGORY_CHANNELS_CACHE[cache_key] = out
    return out


def get_category_analysis_styles(level: str, group: str, month: str, channel: str = "", category: str = "",
                                 sub_category: str = "", tier_mode: str = "current", brand: str = "", tier: str = "", style: str = "") -> dict:
    """The styles behind one STYLES cell of the Category Analysis tables
    (2026-10-29, user-requested: click the style count to see which styles
    sold and how much). ``group`` blank = the TOTAL row; ``channel`` = a
    marketplace sub-row. Per style: units and orders that month, the previous
    month's units and whether it is new (did not sell in the whole previous
    month); plus the stopped styles (sold in the previous month, not this one)
    - the same rules as the +new / -stopped figures next to the count."""
    import pandas as pd

    if level not in _CATEGORY_LEVELS:
        raise ValueError(f"level must be one of {_CATEGORY_LEVELS}, got {level!r}")
    tier_mode = "sale" if level == "tier" and tier_mode == "sale" else "current"
    prev = str(pd.Period(month, freq="M") - 1)
    out = {"level": level, "group": group, "channel": channel, "month": month, "prevMonth": prev,
           "partial": month == f"{data.SNAPSHOT_DATE.year:04d}-{data.SNAPSHOT_DATE.month:02d}",
           "dataThrough": data.SNAPSHOT_DATE.isoformat(), "styles": [], "stopped": []}
    base = _category_channel_frame()
    if base is None or base.empty:
        return out
    base = base[base["month"].isin((month, prev))]
    if channel:
        base = base[base["group"] == channel]
    frame = base[_category_group_mask(base, level, group, category, sub_category, tier_mode, brand, tier, style)]
    if frame.empty:
        return out
    by = frame.groupby(["month", "style"])["qty"].agg(units="sum", orders="size").reset_index()
    now = by[by["month"] == month].set_index("style")
    before = by[by["month"] == prev].set_index("style")
    tier_of = _style_tier_map()

    def info(st: str) -> dict:
        return {"style": st, "subCategory": design_attributes.sub_category_of(st) or "",
                "tier": tier_of.get(st.strip().upper()) or ""}

    styles = []
    for st, r in now.iterrows():
        p = int(round(before["units"].get(st, 0)))
        styles.append({**info(st), "units": int(round(r["units"])), "orders": int(r["orders"]),
                       "prevUnits": p, "new": st not in before.index})
    styles.sort(key=lambda x: (-x["units"], x["style"]))
    stopped = [{**info(st), "prevUnits": int(round(r["units"]))}
               for st, r in before.iterrows() if st not in now.index]
    stopped.sort(key=lambda x: (-x["prevUnits"], x["style"]))
    out.update(styles=styles, stopped=stopped)
    return out


_CATEGORY_FILTERED_CACHE: dict[tuple, dict] = {}  # (data version, filters) -> filtered monthly totals
_STYLE_BRAND_CACHE: tuple | None = None  # (data version, {style: brand})


def _normalize_brand(name) -> str:
    """One spelling per brand for the sales orders' free-text brand_name
    ("MOKOSH" / "mokosh", "coe" / "Colors Of Earth", ...), matching the
    master sheet's names."""
    raw = str(name if name is not None else "").strip()
    key = "".join(ch for ch in raw.lower() if ch.isalnum())
    if not key or key in ("nan", "none", "na"):
        return ""
    if key.startswith("mokosh"):
        return "Mokosh"
    if key.startswith("jturritopsis"):
        return "J.Turritopsis"
    if key.startswith("curlytales"):
        return "Curly Tales"
    if key in ("coe", "colorsofearth", "coloursofearth") or key.startswith("colorsofearth"):
        return "C.O.E"
    return raw.title()


def _style_brand_map() -> dict[str, str]:
    """{style: brand} (2026-10-14, user-requested Brand filter): the master
    sheet's Brand; for a style missing from the sheet, the brand its sales
    orders carry most often (spelling unified). "Unclassified" if neither."""
    global _STYLE_BRAND_CACHE
    if _STYLE_BRAND_CACHE is not None and _STYLE_BRAND_CACHE[0] == data._DATA_VERSION:
        return _STYLE_BRAND_CACHE[1]
    df = data._channel_source_base_df()
    out: dict[str, str] = {}
    if df is not None and not df.empty:
        styles = df["DESIGN_NO"].astype(str)
        fallback: dict[str, str] = {}
        if "brand_name" in df.columns:
            names = df["brand_name"].map(_normalize_brand)
            counts = (names[names != ""].groupby(styles[names != ""]).agg(lambda s: s.value_counts().idxmax())
                      if (names != "").any() else {})
            fallback = dict(counts.items()) if hasattr(counts, "items") else {}
        for st in styles.unique():
            out[st] = design_attributes.brand_of(st) or fallback.get(st) or "Unclassified"
    _STYLE_BRAND_CACHE = (data._DATA_VERSION, out)
    return out


def _category_style_options() -> list[dict]:
    """Every style in the Category Analysis window with its tier, category and
    sub category - the choices behind the Tiers Spike Rate chart's filters."""
    base = _category_channel_frame()
    if base is None or base.empty:
        return []
    # The same tier / category / sub category of a style as the Tiers,
    # Category and Sub Category tables, so the chart's totals match them.
    tier_of = _style_tier_map()
    brand_of = _style_brand_map()
    out = []
    for st in sorted(base["style"].unique()):
        out.append({
            "style": st,
            "brand": brand_of.get(st, "Unclassified"),
            "tier": tier_of.get(st.strip().upper()) or "Unclassified",
            "category": design_attributes.category_of(st) or "Unclassified",
            "subCategory": design_attributes.sub_category_of(st) or "Unclassified",
        })
    return out


def get_category_analysis_filtered(tier: str = "", category: str = "", sub_category: str = "", style: str = "",
                                   brand: str = "") -> dict:
    """Monthly styles / orders / units / spike for every style matching ALL the
    given filters together (blank = any) - the Tiers Spike Rate chart's
    filters (2026-10-13, user-requested: Tier, Category, Sub Category and a
    searchable Style). Summed on the server from the sale rows, so a month's
    spike counts styles that sold the month before even when they did not sell
    this month (the same rule as the tables, see _month_group_rows). Also
    returns every style's tier / category / sub category for the filter lists."""
    cache_key = (data._DATA_VERSION, tier, category, sub_category, style, brand)
    if cache_key in _CATEGORY_FILTERED_CACHE:
        return _CATEGORY_FILTERED_CACHE[cache_key]
    options = _category_style_options()
    out = {"dataThrough": data.SNAPSHOT_DATE.isoformat(), "partialMonth": "", "rows": [], "styleCount": 0, "options": options}
    base = _category_channel_frame()
    if base is None or base.empty:
        return out
    members = {o["style"] for o in options
               if (not brand or o["brand"] == brand)
               and (not tier or o["tier"] == tier) and (not category or o["category"] == category)
               and (not sub_category or o["subCategory"] == sub_category) and (not style or o["style"] == style)}
    out["styleCount"] = len(members)
    frame = base[base["style"].isin(members)]
    if not frame.empty:
        frame = frame.assign(group="selection")
        rows, partial = _month_group_rows(frame, count_styles=True)
        out.update(rows=rows, partialMonth=partial)
    if len(_CATEGORY_FILTERED_CACHE) > 256:
        _CATEGORY_FILTERED_CACHE.clear()
    _CATEGORY_FILTERED_CACHE[cache_key] = out
    return out


def get_style_lifecycle(sub_category: str = "") -> dict:
    """STYLE_LIFECYCLE for one Sub Category, or every Sub Category if
    ``sub_category`` is blank (2026-09-22, user-requested Weekly Sales Report
    feature: styles present last calendar year vs newly launched this one)."""
    if not sub_category:
        return data.STYLE_LIFECYCLE
    entry = data.STYLE_LIFECYCLE.get("bySubCategory", {}).get(sub_category, {
        "presentCount": 0, "presentStyles": [], "addedCount": 0, "addedStyles": [],
    })
    return {
        "lastYear": data.STYLE_LIFECYCLE.get("lastYear"),
        "thisYear": data.STYLE_LIFECYCLE.get("thisYear"),
        "bySubCategory": {sub_category: entry},
    }
