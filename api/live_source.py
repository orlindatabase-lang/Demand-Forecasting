"""
Live data assembly for the SKU Production Plan API.

Replicates the data-assembly notebook in code so the API can fetch fresh data
from the source systems instead of reading ``final_merged_data.csv``:

    1. BigQuery  `orlinappareldataset.oms_sale_us.sales_orders`   -> sales orders
    2. ERP view  View_Dboard_Master_Design_Wise_Fabric_Detail...  -> design master
    3. ERP view  View_Program_Planning_For_Rdp                    -> WIP quantities
    4. ERP view  View_Dboard_Trans_Final_Inventory_Data_For_Rdp   -> pending pieces

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
    "http://195.250.31.101/DigiBizzErpApi/api/UnknownCallerApi/GetPowerBiReports",
)
ERP_TOKEN = os.getenv("ERP_TOKEN", "aaaqqqwww111")
ERP_COMPANY_YEAR_ID = os.getenv("ERP_COMPANY_YEAR_ID", "83")
ERP_TIMEOUT = int(os.getenv("ERP_TIMEOUT", "120"))

VIEW_MASTER = "View_Dboard_Master_Design_Wise_Fabric_Detail_For_BI"
VIEW_PLANNING = "View_Program_Planning_For_Rdp"
VIEW_INVENTORY = "View_Dboard_Trans_Final_Inventory_Data_For_Rdp"
# Lot-journey: one row per (lot, process) with entry/exit dates + vendor.
VIEW_PRODUCTION = "View_Dboard_Trans_Production_Analysis_For_BI"

# WIP = in-house WIP + job-work WIP + allocation (2026-09-28, user-specified;
# replaces open production order + in-house + job-work). ALLOCATION_QTY is
# fractional per size (e.g. 20.155); it is summed per design+size and rounded
# to whole pieces per SKU downstream. Not used: PRO_ORD_WIP_QTY, ALLO_CALC,
# PRO_ORD_PCS_WEEK_WISE (a week-wise production-order schedule).
_WIP_COLS = ["IN_HOUSE_QTY_WIP", "JOB_WORK_QTY_WIP", "ALLOCATION_QTY"]

# Final column order, matching final_merged_data.csv. category_name/brand_name/
# promo_discount added for the weekly demand-forecasting model (api/lgbm_forecast.py).
# source (2026-09-16, user-requested Style x Channel x Source x Month report) -
# the raw table's own OMS/WEBSITE discriminator, a coarser grouping on top of
# channel_name (every channel_name maps to exactly one source value - verified
# no overlap: WEBSITE is only "mokosh.in"/"colorsofearth.in", everything else
# is OMS).
# channel_order_id/channel_sub_order_id (2026-09-16, CRITICAL FIX - found
# investigating a user-reported August total discrepancy): every
# .drop_duplicates() call downstream (_load_real_plan, festival outlook,
# spike detection, the Channel & Source drill-down) compares the FULL row
# across every _FINAL_COLS column - and until now that never included a true
# order identifier. The source table has ZERO real duplicate rows (verified:
# 2,503,935 rows = 2,503,935 distinct (channel_order_id, channel_sub_order_id)
# pairs, both columns 100% non-null) - two genuinely different orders (e.g.
# same SKU/qty=1/status/date/channel, different customers) were being
# wrongly collapsed into one, SILENTLY UNDERCOUNTING real sales (August 2026
# alone: 1,851 units / 1,667 orders dropped this way). Including these two
# columns makes every existing drop_duplicates() call correct with no other
# code change needed, since two rows can now only be identical if their real
# order IDs also match (which never happens for genuine orders).
_FINAL_COLS = [
    "product_sku_code", "listing_sku_code", "qty", "order_status", "order_date",
    "total", "settlement_amount", "buyer_city", "buyer_state", "channel_name",
    "warehouse_name", "delivery_date", "DESIGN_NO", "DESIGN_GROUP",
    "CATALOG_NAME", "COLOR", "LAUNCH_DATE", "SECTION",
    "TOTAL_WIP_QTY", "PENDING_QTY_PIECES",
    "category_name", "brand_name", "promo_discount", "source",
    "channel_order_id", "channel_sub_order_id",
]


# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #
ERP_RETRIES = int(os.getenv("ERP_RETRIES", "3"))

# Views that returned HTTP 500 -> time of that failure. Skipped for
# _UNAVAILABLE_TTL_S only (the rest of the same rebuild), then retried: the
# ERP also returns 500 transiently (2026-09-28: master + inventory 500'd at
# one startup and worked minutes later), and a permanent skip left the API
# without any ERP data until the next restart.
_UNAVAILABLE_VIEWS: dict[str, float] = {}
_UNAVAILABLE_TTL_S = 15 * 60


def fetch_erp_view(
    view_name: str,
    url: str | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    company_year_id: str | None = None,
) -> pd.DataFrame:
    """Fetch one ERP PowerBI view as a DataFrame (stdlib urllib, no auth key).

    Retries on empty response or transient errors. HTTP 500 (view missing,
    or a transient ERP error) skips the view for _UNAVAILABLE_TTL_S.

    Args:
        view_name:        The ERP view/report name.
        url:              Override endpoint (default: ERP_URL).
        timeout:          Per-attempt socket timeout in seconds (default: ERP_TIMEOUT).
        retries:          Max attempts (default: ERP_RETRIES).
        company_year_id:  Override CompanyYearId header (default: ERP_COMPANY_YEAR_ID).
    """
    failed_at = _UNAVAILABLE_VIEWS.get(view_name)
    if failed_at is not None and time.time() - failed_at < _UNAVAILABLE_TTL_S:
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
                _UNAVAILABLE_VIEWS[view_name] = time.time()
                print(f"[live_source] {view_name}: HTTP 500 from ERP - skipping for {_UNAVAILABLE_TTL_S // 60} min")
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

    Raw ERP columns: DESIGN_NAME, SIZE, IN_HOUSE_QTY_WIP,
                     JOB_WORK_QTY_WIP, ALLOCATION_QTY, ...
    Returned columns (PLAN_ prefix): PLAN_DESIGN_NAME, PLAN_SIZE,
        PLAN_IN_HOUSE_QTY_WIP, PLAN_JOB_WORK_QTY_WIP, PLAN_ALLOCATION_QTY, ...
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
    where = f"DATE(order_date) >= '{SALES_START_DATE}' AND DATE(order_date) <= CURRENT_DATE()"
    query = f"""
        SELECT
            product_sku_code, listing_sku_code, qty, order_status, order_date,
            total, settlement_amount, buyer_city, buyer_state, channel_name,
            warehouse_name, delivery_date, category_name, brand_name, promo_discount, source,
            channel_order_id, channel_sub_order_id
        FROM `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}`
        WHERE {where}
    """
    df = client.query(query).to_dataframe()

    # Sanity-check against a cheap server-side COUNT(*) - the BQ Storage Read
    # API (used by to_dataframe() by default) has been observed on this
    # network to silently return a truncated dataframe from a failed/short-
    # circuited parallel stream, with no exception raised (found 2026-09-16
    # investigating a Style/Channel/Source report; a standalone diagnostic
    # script lost ~33% of one month's rows this way). One retry via the
    # slower but reliable REST row iterator before giving up and returning
    # whatever was fetched (never block API startup on this).
    try:
        expected_n = list(client.query(
            f"SELECT COUNT(*) AS n FROM `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}` WHERE {where}"
        ).result())[0]["n"]
        if len(df) != expected_n:
            print(f"[live_source] sales fetch returned {len(df):,} rows but {expected_n:,} exist "
                  f"— retrying via REST row iterator", flush=True)
            df = client.query(query).to_dataframe(create_bqstorage_client=False)
            if len(df) != expected_n:
                print(f"[live_source] retry still short: {len(df):,}/{expected_n:,} rows — proceeding anyway",
                      flush=True)
    except Exception as exc:  # noqa: BLE001 — the count check itself must never block startup
        print(f"[live_source] sales row-count sanity check failed ({exc!r}) — skipping", flush=True)

    return df


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
# Standard size tokens that sometimes show up as a SKU's SECOND segment
# instead of a real sub-design number - e.g. "6302-L"/"6302-M"/"6302-S" is a
# flat design series with no sub-design at all, just DESIGN-SIZE. Without
# stripping these, _design_key() below would treat "6302-L" as the design
# key, fragmenting one real design into five that can never match the ERP
# master's own (sizeless) "6302" record (2026-09-15, user-requested design-
# master-join coverage audit found 72 SKUs / 34K+ units of real sales
# silently invisible in the Weekly Sales Report purely because of this).
_SIZE_TOKENS = {"S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL", "4XL", "5XL", "FREE SIZE", "FREESIZE"}


def _design_key(s: pd.Series) -> pd.Series:
    """First two hyphen-separated segments, e.g. '417-03' from '417-03-XL' or
    'K-7504' from 'K-7504-34'. Was digit-only (``^(\\d+-\\d+)``), which returned
    NaN for every letter-prefixed design (the whole K-series, ~726 SKUs) —
    silently dropping their WIP/inventory/master-view join, since the sales
    side's own design key (a plain hyphen-split below) has no such
    restriction and matched fine.

    If that second segment is actually just a size token (see _SIZE_TOKENS),
    drop it and use only the first segment - see _SIZE_TOKENS' comment for
    why (a flat "DESIGN-SIZE" series has no real second design segment)."""
    two_seg = s.astype(str).str.strip().str.upper().str.extract(r"^([A-Za-z0-9]+-[A-Za-z0-9]+)")[0]
    split = two_seg.str.split("-", n=1)
    first = split.str[0]
    second = split.str[1]
    return two_seg.where(~second.isin(_SIZE_TOKENS), first)


# Letter-series designs carry a colour segment: SKU "K-108-01-32" is design
# "K-108-01" (colour 01), size "32", and the ERP stock/WIP views list it
# under DESIGN_NO "K-108-01" (109 such designs in the master, 2026-09-28).
_COLOUR_DESIGN_RE = r"^([A-Z]+-\d+-\d{2})"


def _stock_design(s: pd.Series) -> pd.Series:
    """ERP design code -> stock/WIP join key: the full colour-level design for
    letter-series codes (K-108-01), else _design_key() as before."""
    up = s.astype(str).str.strip().str.upper()
    colour = up.str.extract(_COLOUR_DESIGN_RE + "$")[0]
    return colour.fillna(_design_key(s))


def assemble() -> pd.DataFrame:
    """Fetch all four sources and merge into the final sales+ERP panel.

    Returns a DataFrame with the same columns as final_merged_data.csv.
    """
    # 1) Sales (BigQuery) ----------------------------------------------------- #
    sales = fetch_sales_bigquery()
    sales["order_date"] = pd.to_datetime(sales["order_date"], errors="coerce")

    # Exclude combo-SKU order lines (2026-09-16, user-requested) - a bundle of
    # two physical designs sold as one order line, e.g. "001-03-XL & K-9301-34"
    # or "412-03--423-03-S" (the same combo, just without a literal "&" in
    # product_sku_code - listing_sku_code always keeps the "&"). _design_key()
    # below only captures the FIRST design in the pair, so a combo's qty was
    # being credited entirely to that one style while its partner design got
    # none of the credit - a real (if small, ~0.06% of Gross Sale) attribution
    # error. Dropped here, before any design-key/master join, so every
    # downstream consumer (Weekly Sales Report, festival outlook, channel
    # breakdown, model training) is consistent automatically.
    is_combo = (
        sales["product_sku_code"].astype(str).str.contains("&", regex=False)
        | sales["product_sku_code"].astype(str).str.contains("--", regex=False)
        | sales["listing_sku_code"].astype(str).str.contains("&", regex=False)
    )
    sales = sales.loc[~is_combo].copy()

    # Exclude "INACTIVE*"-coded order lines (2026-09-17, user-requested,
    # re-applied after a brief revert-and-reinstate cycle) - a placeholder
    # product_sku_code (e.g. "INACTIVE-04", "Inactive -02") used when the
    # OMS/channel integration couldn't resolve a real SKU. Has a hyphen, so
    # _design_key() below was resolving it to a literal "INACTIVE-04" style
    # - pooling together whatever real designs (up to hundreds, per
    # listing_sku_code) happened to share that placeholder code, both
    # fabricating a fake style AND crediting it instead of the real ones.
    # Dropped here rather than reattributed via listing_sku_code -
    # user-specified simple exclusion, same treatment as the combo-SKU rows
    # above.
    is_inactive_placeholder = sales["product_sku_code"].astype(str).str.strip().str.upper().str.startswith("INACTIVE")
    sales = sales.loc[~is_inactive_placeholder].copy()

    # Exclude "Freebie-Pouch-1" (2026-09-17, user-requested) - a promotional
    # giveaway pouch, not a garment design. Has a hyphen, so _design_key()
    # below was resolving it to its own fake "FREEBIE-POUCH" style (532
    # Gross units, one uniform SKU - unlike INACTIVE* above, not pooling any
    # real designs' sales, just a single non-garment item miscounted as one).
    is_freebie = sales["product_sku_code"].astype(str).str.strip().str.upper().str.startswith("FREEBIE-POUCH")
    sales = sales.loc[~is_freebie].copy()

    # Exclude "CTA-K*"-coded order lines (2026-09-17, user-requested) - e.g.
    # "CTA-K-8002-11-12-30". "CTA"/"CT" are channel/marketplace listing-name
    # prefixes (see the design-master join comment below), not part of the
    # real design number - _design_key() below was capturing "CTA-K" (the
    # prefix + the generic "K" series marker) as the design key instead of
    # the real design that follows ("K-8002"), fabricating a fake "CTA-K"
    # style (5 Gross units, one SKU).
    is_cta_k = sales["product_sku_code"].astype(str).str.strip().str.upper().str.startswith("CTA-K")
    sales = sales.loc[~is_cta_k].copy()

    # Exclude the Shopify direct-to-consumer channel entirely (2026-09-18,
    # user-requested) - channel_name "ORLIN APPAREL PRIVATE LIMITED -
    # Shopify" (2,199 Gross units, 750 distinct real SKUs). Unlike the
    # combo/INACTIVE/Freebie-Pouch/CTA-K filters above, this isn't a junk-SKU
    # data-quality fix - these are real designs' real orders, dropped only
    # because the user doesn't want this channel counted in Actual Sale
    # totals or forecasts at all.
    is_shopify = sales["channel_name"].astype(str).str.contains("shopify", case=False, regex=False)
    sales = sales.loc[~is_shopify].copy()

    # 2) Design master (ERP) -> merge on design key --------------------------- #
    # fetch_master() returns MASTER_-prefixed columns (e.g. MASTER_DESIGN_NO).
    # Keyed off product_sku_code, NOT listing_sku_code: listing_sku_code is
    # the channel/marketplace listing name (varies per platform — channel
    # prefixes like "CT-"/"CTA-", kids' "9-10 Years" sizing instead of a
    # plain size, underscores instead of hyphens), so only ~55.4% of rows
    # split cleanly into the assumed DESIGN-DESIGN-SIZE shape. product_sku_code
    # is the internal canonical code and splits cleanly 97.8% of the time
    # (verified 2026-08-17). 2026-08-22: briefly switched the whole SKU
    # identity (this join included) to listing_sku_code, user-requested —
    # reverted the same day after a live retrain showed real damage (WAPE
    # ~45-50 -> ~65, cold-start blending 495 -> 1,209 SKUs, since one
    # physical SKU splits across several per-channel listing codes). See
    # lgbm_forecast.py's COL_SKU comment for the full numbers.
    master = fetch_master()
    master_cols = ["MASTER_DESIGN_NO", "MASTER_DESIGN_GROUP", "MASTER_CATALOG_NAME",
                   "MASTER_COLOR", "MASTER_LAUNCH_DATE", "MASTER_SECTION"]
    merged = sales.assign(design_key=_design_key(sales["product_sku_code"]))
    if "MASTER_DESIGN_NO" in master.columns:
        master = master[[c for c in master_cols if c in master.columns]].copy()
        master["design_key"] = _design_key(master["MASTER_DESIGN_NO"])
        master = master.drop_duplicates(subset="design_key")
        # Strip MASTER_ prefix before merging so the final output uses the
        # canonical column names (DESIGN_NO, DESIGN_GROUP, …).
        master = strip_prefix(master, "MASTER_")
        merged = merged.merge(master, on="design_key", how="left")
    # Fall back to the sales-side design_key itself wherever the ERP master
    # join found no match (or the master fetch failed/returned no
    # MASTER_DESIGN_NO column at all) - 2026-09-15, user-requested fix for a
    # design-master-join coverage audit: 72 SKUs / 34K+ units of real sales
    # had a null DESIGN_NO and were being silently excluded from the Weekly
    # Sales Report's style grouping (get_weekly_grid() skips any row with no
    # design key). A design with no master-catalog metadata (sub-category,
    # color, etc.) is still a real design that sold real units - it should
    # show up under its own derived key, not disappear entirely. Genuinely
    # missing ERP master records (not every gap is fixable here - see
    # design_key's own comment) will still show up with blank/derived
    # DESIGN_GROUP/CATALOG_NAME/COLOR/SECTION rather than none at all.
    if "DESIGN_NO" not in merged.columns:
        merged["DESIGN_NO"] = pd.NA
    merged["DESIGN_NO"] = merged["DESIGN_NO"].fillna(merged["design_key"])
    merged["LAUNCH_DATE"] = pd.to_datetime(merged.get("LAUNCH_DATE"), errors="coerce")

    # design + size keys from product_sku_code (not listing_sku_code — see
    # the design-master join above for why), e.g. '417-03-XL' -> ('417-03','XL'),
    # and for letter-series colour designs 'K-108-01-32' -> ('K-108-01','32')
    # (was ('K-108','01'), which matched no ERP row, so none of those
    # styles' stock or WIP was counted - 2026-09-28 Available Qty audit).
    code = merged["product_sku_code"].astype(str).str.strip().str.upper()
    parts = code.str.split("-")
    colour = code.str.match(_COLOUR_DESIGN_RE + "-")
    merged["_design"] = parts.str[:3].str.join("-").where(colour, parts.str[:2].str.join("-"))
    merged["_size"] = parts.str[3].where(colour, parts.str[2]).fillna("").str.strip()

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
                _design=_stock_design(plan["PLAN_DESIGN_NAME"]),
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
                _design=_stock_design(inv["INV_DESIGN_NO"]),
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
    # One design+size's stock/WIP belongs to ONE SKU: when several SKU codes
    # share the key (case variants like '057-03-S' / '057-03-s', suffixes
    # like '474-05-XS-A'), each used to carry the full quantity, so a Style
    # total counted it more than once. Keep it on the SKU with the most
    # order rows and zero it on the others.
    sku_rows = final.groupby(["_design", "_size", "product_sku_code"]).size().rename("_n").reset_index()
    primary = (sku_rows.sort_values(["_n", "product_sku_code"], ascending=[False, True])
               .drop_duplicates(["_design", "_size"])[["_design", "_size", "product_sku_code"]]
               .rename(columns={"product_sku_code": "_primary_sku"}))
    final = final.merge(primary, on=["_design", "_size"], how="left")
    not_primary = final["product_sku_code"] != final["_primary_sku"]
    final.loc[not_primary, ["TOTAL_WIP_QTY", "PENDING_QTY_PIECES"]] = 0
    final = final.drop(columns=["_primary_sku"])

    # tidy: drop helpers, guarantee the expected columns/order
    final = final.drop(columns=["design_key", "_design", "_size"], errors="ignore")
    for col in _FINAL_COLS:
        if col not in final.columns:
            final[col] = pd.NA
    return final[_FINAL_COLS]
