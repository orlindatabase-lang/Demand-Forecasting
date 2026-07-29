"""
Live data assembly for the SKU Production Plan API.

Replicates the data-assembly notebook in code so the API can fetch fresh data
from the source systems instead of reading ``final_merged_data.csv``:

    1. BigQuery  `orlinappareldataset.oms_sale_us.sales_orders`   -> sales orders
    2. ERP view  View_Dboard_Master_Design_Wise_Fabric_Detail...  -> design master
    3. ERP view  View_Program_Planning_For_BI                     -> WIP quantities
    4. ERP view  View_Dboard_Trans_Final_Inventory_Data_For_BI    -> pending pieces

``assemble()`` merges these into a single DataFrame with the same columns as
``final_merged_data.csv``, which the rest of the pipeline consumes unchanged.

Auth: BigQuery uses Application Default Credentials (gcloud ADC) — i.e.
``bigquery.Client(project=...)`` with no explicit key. Configure with
``gcloud auth application-default login`` (or set GOOGLE_APPLICATION_CREDENTIALS
to a service-account JSON). Every setting below can be overridden via env vars.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration (env-overridable)
# --------------------------------------------------------------------------- #
BQ_PROJECT = os.getenv("BQ_PROJECT", "orlinappareldataset")
BQ_DATASET = os.getenv("BQ_DATASET", "oms_sale_us")
BQ_TABLE = os.getenv("BQ_TABLE", "sales_orders")
SALES_START_DATE = os.getenv("SALES_START_DATE", "2025-03-18")

ERP_URL = os.getenv(
    "ERP_URL",
    "http://190.92.175.131:8080/DigiBizzErpApi/api/UnknownCallerApi/GetPowerBiReports",
)
ERP_TOKEN = os.getenv("ERP_TOKEN", "aaaqqqwww111")
ERP_COMPANY_YEAR_ID = os.getenv("ERP_COMPANY_YEAR_ID", "83")
ERP_TIMEOUT = int(os.getenv("ERP_TIMEOUT", "120"))

VIEW_MASTER = "View_Dboard_Master_Design_Wise_Fabric_Detail_For_BI"
VIEW_PLANNING = "View_Program_Planning_For_BI"
VIEW_INVENTORY = "View_Dboard_Trans_Final_Inventory_Data_For_BI"
# Lot-journey: one row per (lot, process) with entry/exit dates + vendor.
VIEW_PRODUCTION = "View_Dboard_Trans_Production_Analysis_For_BI"

# WIP = the genuine work-in-progress columns only. The planning view also has
# ALLO_CALC / ALLOCATION_QTY (allocations, and fractional — not physical pieces)
# and PRO_ORD_PCS_WEEK_WISE (a week-wise production-order schedule); summing all
# of those (as the original notebook did) inflated WIP ~3-4x. Real WIP is the
# three *_WIP columns: open production order + in-house + job-work.
_WIP_COLS = ["PRO_ORD_WIP_QTY", "IN_HOUSE_QTY_WIP", "JOB_WORK_QTY_WIP"]

# Final column order, matching final_merged_data.csv. category_name/brand_name/
# promo_discount added for the weekly demand-forecasting model (api/lgbm_forecast.py).
_FINAL_COLS = [
    "product_sku_code", "listing_sku_code", "qty", "order_status", "order_date",
    "total", "settlement_amount", "buyer_city", "buyer_state", "channel_name",
    "warehouse_name", "delivery_date", "DESIGN_NO", "DESIGN_GROUP",
    "CATALOG_NAME", "COLOR", "LAUNCH_DATE", "SECTION",
    "TOTAL_WIP_QTY", "PENDING_QTY_PIECES",
    "category_name", "brand_name", "promo_discount",
]


# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #
ERP_RETRIES = int(os.getenv("ERP_RETRIES", "3"))

# Views that returned HTTP 500 — skipped silently on subsequent refresh cycles.
_UNAVAILABLE_VIEWS: set[str] = set()


def fetch_erp_view(
    view_name: str,
    url: str | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    company_year_id: str | None = None,
) -> pd.DataFrame:
    """Fetch one ERP PowerBI view as a DataFrame (stdlib urllib, no auth key).

    Retries on empty response or transient errors. HTTP 500 = view not found,
    logged once then permanently skipped.

    Args:
        view_name:        The ERP view/report name.
        url:              Override endpoint (default: ERP_URL).
        timeout:          Per-attempt socket timeout in seconds (default: ERP_TIMEOUT).
        retries:          Max attempts (default: ERP_RETRIES).
        company_year_id:  Override CompanyYearId header (default: ERP_COMPANY_YEAR_ID).
    """
    if view_name in _UNAVAILABLE_VIEWS:
        return pd.DataFrame()

    _url     = url             or ERP_URL
    _timeout = timeout         or ERP_TIMEOUT
    _retries = retries         or ERP_RETRIES
    _cy_id   = company_year_id or ERP_COMPANY_YEAR_ID

    req = urllib.request.Request(
        _url,
        headers={
            "Report-Api-Token": ERP_TOKEN,
            "ViewName": view_name,
            "CompanyYearId": _cy_id,
            "Accept": "application/json",
        },
    )
    last = pd.DataFrame()
    for attempt in range(1, _retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            df = pd.DataFrame(data)
            if not df.empty:
                return df
            last = df
        except urllib.error.HTTPError as exc:
            if exc.code == 500:
                _UNAVAILABLE_VIEWS.add(view_name)
                print(f"[live_source] {view_name}: not found in ERP (HTTP 500) — skipping")
                return pd.DataFrame()
            print(f"[live_source] {view_name} attempt {attempt}/{_retries} failed: {exc!r}")
        except Exception as exc:  # noqa: BLE001 — retry on transient errors
            print(f"[live_source] {view_name} attempt {attempt}/{_retries} failed: {exc!r}")
        if attempt < _retries:
            time.sleep(min(2.0 * attempt, 10.0))
    print(f"[live_source] {view_name}: empty after {_retries} attempts")
    return last


def prefix_cols(df: pd.DataFrame, prefix: str, keep: frozenset = frozenset({"LOT_NO"})) -> pd.DataFrame:
    """Rename all columns with a process-specific prefix except join-key columns.

    Call this in every process-specific fetch function so columns are
    unambiguous when multiple process DataFrames are in memory at the same time.

    Args:
        df:     Raw DataFrame from the ERP view.
        prefix: E.g. "JW_", "EMB_", "MASTER_". Applied to every column NOT in `keep`.
        keep:   Join-key columns kept under their original name. Default: {"LOT_NO"}.

    Example:
        PARTY_NAME  →  JW_PARTY_NAME    (prefix="JW_")
        GRN_QTY     →  EMB_GRN_GRN_QTY (prefix="EMB_GRN_")
        LOT_NO      →  LOT_NO           (always kept as-is)
    """
    return df.rename(columns={c: f"{prefix}{c}" for c in df.columns if c not in keep})


def strip_prefix(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Reverse prefix_cols — remove a known prefix from column names.

    Use before passing prefixed data to internal processing functions or
    external models that expect the original ERP column names.

    Example:
        JW_PARTY_NAME   →  PARTY_NAME   (strip_prefix(df, "JW_"))
        LOT_NO          →  LOT_NO       (unprefixed columns are unchanged)
    """
    return df.rename(columns={c: c[len(prefix):] for c in df.columns if c.startswith(prefix)})




def fetch_production_analysis() -> pd.DataFrame:
    """Fetch the lot-journey view (one row per lot×process, with FIRST/LAST dates).

    Raw ERP columns: LOT_NO, MAIN_DESIGN_NAME, PROCESS_NAME, PARTY_NAME,
                     FIRST_DATE, LAST_DATE, BAL_QTY, PRO_ORD_STATUS, ...
    Returned columns (PA_ prefix): LOT_NO (kept), PA_MAIN_DESIGN_NAME,
        PA_PROCESS_NAME, PA_PARTY_NAME, PA_FIRST_DATE, PA_LAST_DATE, ...
    """
    df = fetch_erp_view(VIEW_PRODUCTION)
    return prefix_cols(df, "PA_") if not df.empty else df


def fetch_master() -> pd.DataFrame:
    """Fetch the design master view (design attributes: group, color, section…).

    Raw ERP columns: DESIGN_NO, DESIGN_GROUP, CATALOG_NAME, COLOR,
                     LAUNCH_DATE, SECTION, DESIGNER, IS_ACTIVE
    Returned columns (MASTER_ prefix): MASTER_DESIGN_NO, MASTER_DESIGN_GROUP,
        MASTER_CATALOG_NAME, MASTER_COLOR, MASTER_LAUNCH_DATE, MASTER_SECTION,
        MASTER_DESIGNER, MASTER_IS_ACTIVE
    Note: no LOT_NO join key — all columns are prefixed.
    """
    df = fetch_erp_view(VIEW_MASTER)
    return prefix_cols(df, "MASTER_", keep=frozenset()) if not df.empty else df


def fetch_planning() -> pd.DataFrame:
    """Fetch the WIP / program-planning view (open production orders by design+size).

    Raw ERP columns: DESIGN_NAME, SIZE, PRO_ORD_WIP_QTY,
                     IN_HOUSE_QTY_WIP, JOB_WORK_QTY_WIP, ...
    Returned columns (PLAN_ prefix): PLAN_DESIGN_NAME, PLAN_SIZE,
        PLAN_PRO_ORD_WIP_QTY, PLAN_IN_HOUSE_QTY_WIP, PLAN_JOB_WORK_QTY_WIP, ...
    """
    df = fetch_erp_view(VIEW_PLANNING)
    return prefix_cols(df, "PLAN_", keep=frozenset()) if not df.empty else df


def fetch_inventory() -> pd.DataFrame:
    """Fetch the finished-goods inventory view (pending pieces by design+size).

    Raw ERP columns: DESIGN_NO, SIZE, PENDING_QTY_PIECES,
                     TOTAL_COST, TOTAL_COST_AMOUNT
    Returned columns (INV_ prefix): INV_DESIGN_NO, INV_SIZE,
        INV_PENDING_QTY_PIECES, INV_TOTAL_COST, INV_TOTAL_COST_AMOUNT
    """
    df = fetch_erp_view(VIEW_INVENTORY)
    return prefix_cols(df, "INV_", keep=frozenset()) if not df.empty else df




def fetch_sales_bigquery() -> pd.DataFrame:
    """Fetch sales orders from BigQuery (Application Default Credentials)."""
    from google.cloud import bigquery  # imported lazily so the API starts without the lib

    client = bigquery.Client(project=BQ_PROJECT)
    query = f"""
        SELECT
            product_sku_code, listing_sku_code, qty, order_status, order_date,
            total, settlement_amount, buyer_city, buyer_state, channel_name,
            warehouse_name, delivery_date, category_name, brand_name, promo_discount
        FROM `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}`
        WHERE DATE(order_date) >= '{SALES_START_DATE}'
        AND   DATE(order_date) <= CURRENT_DATE()
    """
    return client.query(query).to_dataframe()


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def _design_key(s: pd.Series) -> pd.Series:
    """First two numeric segments, e.g. '417-03' from '417-03-XL'."""
    return s.astype(str).str.strip().str.upper().str.extract(r"^(\d+-\d+)")[0]


def assemble() -> pd.DataFrame:
    """Fetch all four sources and merge into the final sales+ERP panel.

    Returns a DataFrame with the same columns as final_merged_data.csv.
    """
    # 1) Sales (BigQuery) ----------------------------------------------------- #
    sales = fetch_sales_bigquery()
    sales["order_date"] = pd.to_datetime(sales["order_date"], errors="coerce")

    # 2) Design master (ERP) -> merge on design key --------------------------- #
    # fetch_master() returns MASTER_-prefixed columns (e.g. MASTER_DESIGN_NO).
    master = fetch_master()
    master_cols = ["MASTER_DESIGN_NO", "MASTER_DESIGN_GROUP", "MASTER_CATALOG_NAME",
                   "MASTER_COLOR", "MASTER_LAUNCH_DATE", "MASTER_SECTION"]
    merged = sales.assign(design_key=_design_key(sales["listing_sku_code"]))
    if "MASTER_DESIGN_NO" in master.columns:
        master = master[[c for c in master_cols if c in master.columns]].copy()
        master["design_key"] = _design_key(master["MASTER_DESIGN_NO"])
        master = master.drop_duplicates(subset="design_key")
        # Strip MASTER_ prefix before merging so the final output uses the
        # canonical column names (DESIGN_NO, DESIGN_GROUP, …).
        master = strip_prefix(master, "MASTER_")
        merged = merged.merge(master, on="design_key", how="left")
    merged["LAUNCH_DATE"] = pd.to_datetime(merged.get("LAUNCH_DATE"), errors="coerce")

    # design + size keys from the listing SKU, e.g. '417-03-XL' -> ('417-03','XL')
    parts = merged["listing_sku_code"].astype(str).str.strip().str.upper().str.split("-")
    merged["_design"] = parts.str[:2].str.join("-")
    merged["_size"] = parts.str[2].fillna("").str.strip()

    # 3) WIP (planning view), aggregated by design+size ----------------------- #
    # fetch_planning() returns PLAN_-prefixed columns (e.g. PLAN_DESIGN_NAME).
    plan = fetch_planning()
    _WIP_COLS_PLAN = [f"PLAN_{c}" for c in _WIP_COLS]
    if {"PLAN_DESIGN_NAME", "PLAN_SIZE"}.issubset(plan.columns):
        plan[_WIP_COLS_PLAN] = (
            plan.reindex(columns=_WIP_COLS_PLAN).apply(pd.to_numeric, errors="coerce").fillna(0)
        )
        plan["PLAN_TOTAL_WIP_QTY"] = plan[_WIP_COLS_PLAN].sum(axis=1)
        wip = (
            plan.assign(
                _design=_design_key(plan["PLAN_DESIGN_NAME"]),
                _size=plan["PLAN_SIZE"].astype(str).str.strip().str.upper(),
            )
            .groupby(["_design", "_size"], as_index=False)["PLAN_TOTAL_WIP_QTY"]
            .sum()
            .rename(columns={"PLAN_TOTAL_WIP_QTY": "TOTAL_WIP_QTY"})
        )
    else:
        print("[live_source] planning view unavailable; WIP defaults to 0")
        wip = pd.DataFrame(columns=["_design", "_size", "TOTAL_WIP_QTY"])

    # 4) Pending pieces (inventory view), aggregated by design+size ----------- #
    # fetch_inventory() returns INV_-prefixed columns (e.g. INV_DESIGN_NO).
    inv = fetch_inventory()
    if {"INV_DESIGN_NO", "INV_SIZE", "INV_PENDING_QTY_PIECES"}.issubset(inv.columns):
        inv["INV_PENDING_QTY_PIECES"] = pd.to_numeric(
            inv["INV_PENDING_QTY_PIECES"], errors="coerce"
        ).fillna(0)
        pend = (
            inv.assign(
                _design=_design_key(inv["INV_DESIGN_NO"]),
                _size=inv["INV_SIZE"].astype(str).str.strip().str.upper(),
            )
            .groupby(["_design", "_size"], as_index=False)["INV_PENDING_QTY_PIECES"]
            .sum()
            .rename(columns={"INV_PENDING_QTY_PIECES": "PENDING_QTY_PIECES"})
        )
    else:
        print("[live_source] inventory view unavailable; PENDING defaults to 0")
        pend = pd.DataFrame(columns=["_design", "_size", "PENDING_QTY_PIECES"])

    # 5) Join WIP + pending onto the sales panel ------------------------------ #
    final = merged.merge(wip, on=["_design", "_size"], how="left").merge(
        pend, on=["_design", "_size"], how="left"
    )
    final[["TOTAL_WIP_QTY", "PENDING_QTY_PIECES"]] = final[
        ["TOTAL_WIP_QTY", "PENDING_QTY_PIECES"]
    ].fillna(0)

    # tidy: drop helpers, guarantee the expected columns/order
    final = final.drop(columns=["design_key", "_design", "_size"], errors="ignore")
    for col in _FINAL_COLS:
        if col not in final.columns:
            final[col] = pd.NA
    return final[_FINAL_COLS]
