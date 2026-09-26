"""Read-only diagnostic: total sale (units) for one calendar month, queried
directly from BigQuery — no FastAPI app/`data` module import needed, so this
runs standalone in a notebook or a plain script. Mirrors the same Gross Sale
definition the dashboard uses (`api/data.py`'s `_GROSS_SALE_STATUSES` /
`_SOLD_STATUSES`) and the same de-dup (channel_order_id + channel_sub_order_id
identify a real order — the table has ZERO true duplicate rows, verified
2026-09-16 across all 2.5M+ rows; an earlier version of this script/pipeline
excluded those two columns and wrongly collapsed distinct orders that just
happened to share SKU/qty/status/date/channel — see live_source.py's
_FINAL_COLS comment for the full story) — keep the two status sets below in
sync with api/data.py if that file changes.

Usage (script):
    python check_month_total_bq.py           # defaults to LAST calendar month
    python check_month_total_bq.py 2026 8     # a specific year/month

Usage (notebook):
    from check_month_total_bq import month_total, last_month
    month_total(2026, 8)
    month_total(*last_month())
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd
from google.cloud import bigquery

BQ_PROJECT = "orlinappareldataset"
BQ_DATASET = "oms_sale_us"
BQ_TABLE = "sales_orders"

# Keep these two sets in sync with api/data.py's _SOLD_STATUSES /
# _GROSS_SALE_STATUSES (see that file's comments for the full reasoning).
_SOLD_STATUSES = {
    "delivered", "shipped", "in transit", "ready to ship", "ready for pickup",
    "packed", "new", "processing", "pending",
    "manifested", "out for delivery", "picked up", "reached at destination", "delayed",
}
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


def last_month(today: date | None = None) -> tuple[int, int]:
    """(year, month) for the calendar month before ``today`` (default: real today) —
    the current month is still in progress, so "last month" is the most recent
    one with a complete, stable total."""
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    last_day_prev_month = first_of_this_month - pd.Timedelta(days=1)
    return last_day_prev_month.year, last_day_prev_month.month


def fetch_month(year: int, month: int) -> pd.DataFrame:
    start = pd.Timestamp(date(year, month, 1))
    end = start + pd.offsets.MonthEnd(0)
    client = bigquery.Client(project=BQ_PROJECT)
    where = f"DATE(order_date) >= '{start.date()}' AND DATE(order_date) <= '{end.date()}'"

    # Cheap server-side count first — the BQ Storage API client
    # (create_bqstorage_client=True) has been observed to silently return a
    # truncated dataframe (partial read stream) on this network with no
    # error raised, so fetch WITHOUT it and verify the row count matches.
    check_query = f"""
        SELECT COUNT(*) AS n, SUM(qty) AS total_qty
        FROM `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}`
        WHERE {where}
    """
    expected = list(client.query(check_query).result())[0]

    print(f"Fetching {start.strftime('%B %Y')} from BigQuery …")
    # Select the SAME full column set api/live_source.py does (not just the
    # handful this script actually reports on) - CRITICALLY including
    # channel_order_id/channel_sub_order_id, the table's real order
    # identifiers. Without them, drop_duplicates() below compares only
    # SKU/qty/status/date/channel/etc and wrongly collapses two genuinely
    # different orders that happen to share all of those (very common for
    # qty=1 orders of a popular SKU) - verified 2026-09-16 this cost August
    # 2026 alone 1,851 real units / 1,667 orders. With the true IDs included,
    # two rows can only look identical if they really are the same order.
    query = f"""
        SELECT product_sku_code, listing_sku_code, qty, order_status, order_date,
               total, settlement_amount, buyer_city, buyer_state, channel_name,
               warehouse_name, delivery_date, category_name, brand_name,
               promo_discount, source, channel_order_id, channel_sub_order_id
        FROM `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}`
        WHERE {where}
    """
    # create_bqstorage_client defaults to True in this client library version
    # and reads via the BigQuery Storage Read API's parallel streams — on
    # this network a stream has been observed to fail/short-circuit without
    # raising, silently returning a truncated dataframe. Force the slower but
    # reliable REST (tabledata.list) row iterator instead.
    df = client.query(query).to_dataframe(create_bqstorage_client=False, progress_bar_type=None)

    if len(df) != expected["n"]:
        raise RuntimeError(
            f"Fetched {len(df):,} rows but BigQuery reports {expected['n']:,} rows "
            f"exist for this month — the dataframe read was truncated. Re-run, or "
            f"page the query manually (e.g. via client.query(...).result(page_size=...))."
        )
    return df


def month_total(year: int, month: int) -> None:
    df = fetch_month(year, month)
    df = df.drop_duplicates()
    status_norm = df["order_status"].astype(str).str.strip().str.lower()

    gross_mask = status_norm.isin(_GROSS_SALE_STATUSES)
    net_mask = status_norm.isin(_SOLD_STATUSES)

    label = date(year, month, 1).strftime("%B %Y")
    gross_qty = df.loc[gross_mask, "qty"].sum()
    print(f"=== {label}: GROSS SALE = {gross_qty:,} units ({gross_mask.sum():,} rows) ===")
    print(f"  (dashboard definition — every status except Cancelled / Cancelled Before Shipping / Cancel Request Approved)")
    print()
    print(f"All statuses, no exclusion : {df['qty'].sum():>10,} units  ({len(df):,} rows)")
    print(f"GROSS (dashboard definition): {gross_qty:>10,} units  ({gross_mask.sum():,} rows)")
    print(f"NET (_SOLD_STATUSES)        : {df.loc[net_mask, 'qty'].sum():>10,} units  ({net_mask.sum():,} rows)")

    print()
    print("By source:")
    for src, g in df.groupby("source"):
        g_status = g["order_status"].astype(str).str.strip().str.lower()
        g_gross = g.loc[g_status.isin(_GROSS_SALE_STATUSES), "qty"].sum()
        print(f"  {src:<10s} GROSS qty={g_gross:>10,}  ({len(g):,} rows)")

    print()
    print("Status breakdown (qty desc):")
    breakdown = df.groupby(status_norm)["qty"].agg(["count", "sum"]).sort_values("sum", ascending=False)
    for status, row in breakdown.iterrows():
        flag = "GROSS" if status in _GROSS_SALE_STATUSES else "excluded"
        print(f"  {status:<28s} rows={int(row['count']):>7,}  qty={int(row['sum']):>7,}  [{flag}]")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        year, month = int(sys.argv[1]), int(sys.argv[2])
    else:
        year, month = last_month()
    month_total(year, month)
