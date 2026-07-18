"""
Data processing: load, clean, validate, and aggregate the merged sales data
into daily demand panels.

Pipeline
--------
load_raw            -> read the merged CSV
clean_data          -> typed, de-duplicated frame with a row-level net_demand sign
data_quality_report -> human-readable QA summary (also flags unknown statuses)
aggregate_daily     -> (date, id) net_demand table
complete_timeline   -> dense daily grid per id with missing days = 0 demand
build_static_attrs  -> one static attribute row per id (for product features)
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import config


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_raw(path=None) -> pd.DataFrame:
    """Load the merged sales/ERP CSV. Raises a clear error if it is missing."""
    path = path or config.DATA_PATH
    try:
        df = pd.read_csv(path, low_memory=False)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Input data not found at {path}. Run the data-assembly notebook "
            f"first (it writes final_merged_data.csv)."
        ) from exc
    if df.empty:
        raise ValueError(f"Input data at {path} is empty.")
    return df


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #
def _normalize_status(s: pd.Series) -> pd.Series:
    """Lower-case, strip, and collapse internal whitespace for status matching."""
    return (
        s.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"\s+", " ", regex=True)
    )


def add_net_demand(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a row-level ``net_demand`` column = sign(status) * qty.

    Delivered -> +qty, Return Received -> -qty, Cancelled / Cancelled Return
    Received -> 0 (see :func:`config.demand_sign`).
    """
    df = df.copy()
    status_norm = _normalize_status(df[config.COL_STATUS])
    df["status_norm"] = status_norm
    sign = status_norm.map(config.demand_sign).astype("int8")
    df["demand_sign"] = sign
    qty = pd.to_numeric(df[config.COL_QTY], errors="coerce").fillna(0)
    df[config.TARGET] = sign * qty
    return df


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Type-cast dates and numerics, drop duplicates, attach the row-level
    ``net_demand`` signal, and remove rows that cannot be placed on the
    forecasting timeline (missing/future order_date).
    """
    df = df.copy()

    # --- dates ---
    # Only order_date (timeline) and LAUNCH_DATE (product age) are needed.
    # delivery_date is deliberately NOT parsed: it is unused for forecasting and
    # its free-text format would force a slow element-wise parse on millions of
    # rows.
    for col in (config.COL_ORDER_DATE, config.COL_LAUNCH_DATE):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    # order_date drives the timeline -> normalise to midnight
    df[config.COL_ORDER_DATE] = df[config.COL_ORDER_DATE].dt.normalize()

    # --- numerics ---
    for col in config.NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- categorical / product attributes: blank -> NaN, strip ---
    for col in config.PRODUCT_ATTR_COLS + [config.COL_SKU, config.COL_DESIGN]:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()
            df[col] = df[col].replace({"": pd.NA, "0": pd.NA, "nan": pd.NA})

    # --- demand sign / net_demand ---
    df = add_net_demand(df)

    # --- drop exact-duplicate order lines (no order_id in source) ---
    df = df.drop_duplicates(ignore_index=True)

    # --- drop rows that cannot be timestamped or have no qty ---
    today = pd.Timestamp.today().normalize()
    valid = (
        df[config.COL_ORDER_DATE].notna()
        & (df[config.COL_ORDER_DATE] <= today)
        & df[config.COL_QTY].notna()
    )
    df = df.loc[valid].reset_index(drop=True)

    return df


# --------------------------------------------------------------------------- #
# Data-quality report
# --------------------------------------------------------------------------- #
def data_quality_report(raw: pd.DataFrame, clean: pd.DataFrame) -> pd.DataFrame:
    """
    Build a tidy data-quality summary comparing the raw and cleaned frames.

    Returns a DataFrame with one ``metric``/``value`` row per check so it can be
    written straight to CSV.
    """
    rows: List[Tuple[str, object]] = []

    rows.append(("raw_rows", len(raw)))
    rows.append(("clean_rows", len(clean)))
    rows.append(("rows_removed", len(raw) - len(clean)))
    rows.append(("duplicate_rows_in_raw", int(raw.duplicated().sum())))

    # null counts per important column (on raw)
    for col in [config.COL_ORDER_DATE, config.COL_QTY, config.COL_STATUS,
                config.COL_SKU, config.COL_DESIGN, config.COL_LAUNCH_DATE]:
        if col in raw.columns:
            rows.append((f"nulls_raw__{col}", int(raw[col].isna().sum())))

    # date span
    if config.COL_ORDER_DATE in clean.columns and not clean.empty:
        rows.append(("order_date_min", clean[config.COL_ORDER_DATE].min()))
        rows.append(("order_date_max", clean[config.COL_ORDER_DATE].max()))

    # status breakdown -> demand sign
    if "status_norm" in clean.columns:
        sign_counts = clean.groupby("demand_sign").size()
        rows.append(("rows_positive_demand", int(sign_counts.get(1, 0))))
        rows.append(("rows_zero_demand", int(sign_counts.get(0, 0))))
        rows.append(("rows_negative_demand", int(sign_counts.get(-1, 0))))

        # statuses that did not match any known rule (sign 0 but not 'cancel')
        unknown = (
            clean.loc[clean["demand_sign"].eq(0) & ~clean["status_norm"].str.contains("cancel"),
                      "status_norm"]
            .value_counts()
        )
        rows.append(("unknown_status_values", "; ".join(
            f"{k}({v})" for k, v in unknown.items()) or "none"))

    # net demand totals
    if config.TARGET in clean.columns:
        rows.append(("total_net_demand", float(clean[config.TARGET].sum())))
        rows.append(("unique_skus", int(clean[config.COL_SKU].nunique())))
        rows.append(("unique_designs", int(clean[config.COL_DESIGN].nunique())))

    return pd.DataFrame(rows, columns=["metric", "value"])


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def aggregate_daily(clean: pd.DataFrame, id_col: str) -> pd.DataFrame:
    """
    Aggregate row-level net_demand to one row per (week, id).

    Each order is bucketed into the Monday of its week (weekly forecasting grain).
    Rows with a missing id are dropped (cannot be attributed to an entity).
    Output columns: [date, <id_col>, net_demand, order_lines] where ``date`` is
    the week-start Monday.
    """
    sub = clean.loc[clean[id_col].notna(),
                    [config.COL_ORDER_DATE, id_col, config.TARGET]].copy()
    sub["_w"] = sub[config.COL_ORDER_DATE] - pd.to_timedelta(
        sub[config.COL_ORDER_DATE].dt.weekday, unit="D")
    weekly = (
        sub.groupby([id_col, "_w"], as_index=False)
        .agg(net_demand=(config.TARGET, "sum"),
             order_lines=(config.TARGET, "size"))
        .rename(columns={"_w": config.DATE_COL})
    )
    return weekly


def complete_timeline(daily: pd.DataFrame, id_col: str,
                      global_max: pd.Timestamp | None = None) -> pd.DataFrame:
    """
    Expand each id to a dense daily grid from its first observed date to the
    global maximum date, filling missing days with zero demand.

    A continuous timeline is required for correct lag / rolling features.
    """
    if daily.empty:
        return daily.assign(**{config.DATE_COL: pd.NaT})

    global_max = global_max or daily[config.DATE_COL].max()
    starts = daily.groupby(id_col)[config.DATE_COL].min().reset_index(name="_start")

    # build (id, date) grid via per-id date ranges, then explode
    starts["_dates"] = starts["_start"].apply(
        lambda s: pd.date_range(s, global_max, freq="7D"))  # weekly grid (Mondays)
    grid = starts.explode("_dates")[[id_col, "_dates"]].rename(
        columns={"_dates": config.DATE_COL})
    grid[config.DATE_COL] = pd.to_datetime(grid[config.DATE_COL])

    panel = grid.merge(daily, on=[id_col, config.DATE_COL], how="left")
    panel["net_demand"] = panel["net_demand"].fillna(0.0)
    panel["order_lines"] = panel["order_lines"].fillna(0).astype(int)
    panel = panel.sort_values([id_col, config.DATE_COL]).reset_index(drop=True)
    return panel


def build_static_attrs(clean: pd.DataFrame, id_col: str) -> pd.DataFrame:
    """
    One static attribute row per id: modal product attributes plus the earliest
    launch date (used for product_age_days).

    For DESIGN_NO-level forecasting, COLOR varies within a design, so the modal
    colour is taken; downstream code treats it as just another category.
    """
    attr_cols = [c for c in config.PRODUCT_ATTR_COLS if c in clean.columns]

    def _mode(s: pd.Series):
        m = s.dropna()
        return m.mode().iloc[0] if not m.mode().empty else pd.NA

    agg: Dict[str, object] = {c: _mode for c in attr_cols}
    if config.COL_LAUNCH_DATE in clean.columns:
        agg[config.COL_LAUNCH_DATE] = "min"

    static = (
        clean.loc[clean[id_col].notna()]
        .groupby(id_col)
        .agg(agg)
        .reset_index()
    )
    if config.COL_LAUNCH_DATE in static.columns:
        static[config.COL_LAUNCH_DATE] = pd.to_datetime(
            static[config.COL_LAUNCH_DATE], errors="coerce")
    return static


def build_panel(clean: pd.DataFrame, id_col: str
                ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convenience wrapper: daily aggregation -> dense timeline, plus the static
    attribute table for the same id. Returns ``(panel, static_attrs)``.
    """
    daily = aggregate_daily(clean, id_col)
    panel = complete_timeline(daily, id_col)
    static = build_static_attrs(clean, id_col)
    return panel, static
