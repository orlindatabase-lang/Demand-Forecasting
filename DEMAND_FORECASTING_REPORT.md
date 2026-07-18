# Demand Forecasting & Production Planning — Detailed Report

**Prepared:** 9 June 2026
**Snapshot date (data as of):** 8 June 2026
**Data source:** `final_merged_data.csv` (real merged sales + ERP order history)

---

## 1. Executive Summary

This report documents a demand-forecasting and production-planning system built on
**~2.0 million real order records** spanning **15 months** (18 Mar 2025 → 8 Jun 2026),
covering **6,660 SKUs** across **1,156 designs**.

Key outcomes:

- **Net demand:** 1.14 million units sold, **₹103.6 Cr gross revenue** over the period.
- **Forecasting model:** seasonal-naive with weekly (day-of-week) seasonality, driven by
  *net* sales (cancellations and returns excluded).
- **Production plan:** for the next 35 days the model recommends producing **82,657 units net
  of WIP**, concentrated in **2,176 SKUs** that are below demand coverage.
- **Forecast quality is strongly volume-dependent:** high-volume SKUs forecast at **80–90 %
  accuracy**, while the long tail of intermittent sellers drags the simple per-SKU mean down to
  ~29 %. The **volume-weighted accuracy is ≈ 52 %**, and improving the tail is the single biggest
  opportunity (see §5 and §9).

> ⚠️ **Two data caveats shape every number below:** (1) there is **no on-hand inventory column**
> in the source, so available stock = WIP only; and (2) the **return rate is 30.7 %**, which is
> high and materially affects true sell-through. Both are discussed in §9.

---

## 2. Data Overview

| Metric | Value |
|---|---|
| Total order rows | 1,994,590 |
| Date range | 18 Mar 2025 → 8 Jun 2026 (~15 months) |
| Distinct SKUs | 6,660 |
| Distinct designs | 1,156 |
| Net units sold | 1,144,235 |
| Gross revenue (sold orders) | ₹103.56 Cr |

### Order-status composition

| Status group | Orders | Share |
|---|---|---|
| **Sold** (Delivered, Shipped, In Transit, Ready to ship, Packed, New, Processing, Pending) | 1,142,766 | 57.3 % |
| **Returns** (Return Received / Init / Rejected, partials) | 612,458 | **30.7 %** |
| **Cancellations** (Cancelled, Cancelled Before Shipping, Cancel Init) | 239,366 | 12.0 % |

Only the **Sold** group is treated as realized demand for forecasting and revenue.

### Known data-quality issues

- **Returns at 30.7 %** are high (fashion/apparel norm is ~20–25 %). Gross revenue overstates
  realised revenue; `settlement_amount` (net) is materially lower.
- **Geography casing** — states arrive as both `Maharashtra` and `MAHARASHTRA`; normalized by
  title-casing (216 → ~120 distinct states).
- **Placeholder values** — `buyer_city` contains `Unknown` (680k rows) and `******`; these are
  dropped from the city ranking.
- **Warehouse** `DEFAULT 34455` carries ~87 % of orders — a catch-all/placeholder, not a real DC.
- **Section** is essentially single-class: 99 % `Top`, with a small uncategorized `0` bucket.

---

## 3. Demand Profile

### 3.1 By sales channel

| Channel | Net units | Share | Gross revenue |
|---|---|---|---|
| **Flipkart** | 522,389 | 45.7 % | ₹43.82 Cr |
| Meesho | 276,623 | 24.2 % | ₹21.75 Cr |
| Myntra | 180,518 | 15.8 % | ₹18.66 Cr |
| Amazon | 153,703 | 13.4 % | ₹17.41 Cr |
| Nykaa | 9,058 | 0.8 % | ₹1.66 Cr |
| Shopify | 1,944 | 0.2 % | ₹0.26 Cr |

**Flipkart + Meesho drive ~70 % of unit volume.** Channel mix should inform safety-stock and
allocation decisions.

### 3.2 Top selling geography (last 30 days, gross revenue)

| Rank | State | Rev (30d) | Qty (30d) | City | Rev (30d) | Qty (30d) |
|---|---|---|---|---|---|---|
| 1 | Karnataka | ₹78.3 L | 7,657 | Bengaluru | ₹37.8 L | 3,616 |
| 2 | Tamil Nadu | ₹83.8 L | 7,650 | Hyderabad | ₹38.1 L | 3,504 |
| 3 | Kerala | ₹65.5 L | 6,688 | Chennai | ₹30.8 L | 2,706 |
| 4 | Maharashtra | ₹61.1 L | 6,501 | New Delhi | ₹16.6 L | 1,697 |
| 5 | Uttar Pradesh | ₹56.9 L | 6,038 | Pune | ₹15.7 L | 1,653 |

Demand skews **South India** (Karnataka, Tamil Nadu, Kerala lead). Over a 90-day window the top
states cross ₹2–2.6 Cr each.

### 3.3 Weekly seasonality (day-of-week index, 1.00 = average day)

| Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|
| 0.97 | 0.97 | 0.94 | 0.96 | 0.98 | 1.04 | **1.14** |

**Sunday is the clear peak (+14 %)**, Saturday second; mid-week is softest. This pattern is learned
per-SKU and applied to the daily forecast (see §4).

### 3.4 Monthly trend (net units)

| Month | Units |
|---|---|
| 2025-10 | 83,955 |
| 2025-11 | 66,557 |
| 2025-12 | 81,590 |
| 2026-01 | 89,761 |
| 2026-02 | 102,432 |
| 2026-03 | **119,208** (peak) |
| 2026-04 | 93,865 |
| 2026-05 | 89,150 |
| 2026-06 | 30,271 (partial — 8 days) |

Demand built steadily through the festive/winter season to a **March 2026 peak**, then softened
into spring. (June is a partial month and not comparable.)

---

## 4. Forecasting Methodology

### 4.1 Demand definition

Demand = units in orders with a **Sold** status. Cancellations and returns are excluded, so the
forecast targets *net realised demand*, not gross orders.

### 4.2 Model: seasonal-naive with weekly seasonality

For each SKU:

```
level   = (units sold in the last 35 days) / 35          # average daily demand
factor[d] = SKU's day-of-week multiplier (Mon..Sun),     # learned from last 90 days,
            normalized so the 7 values average ~1            redistributes the level across the week

forecast(day) = round( level × factor[ weekday(day) ] )
```

This produces a forecast that **varies across the week** (e.g. higher on weekends) rather than a
flat line, while preserving the SKU's overall demand level.

### 4.3 Horizon aggregates shown in the plan

| Field | Definition |
|---|---|
| `forecast7` | level × 7 |
| `forecast10` | level × 10 |
| `forecast35` | units sold in the last 35 days (= level × 35) |
| `historicalLast10d` | actual units sold in the last 10 days |

### 4.4 Why seasonal-naive

With 15 months of history and a 35-day planning horizon, a transparent seasonal-naive baseline is
appropriate, fast (full rebuild in ~5 s), and explainable to planners. It also sets the benchmark
any more complex model must beat. Upgrade paths are in §9.

---

## 5. Forecast Accuracy

Accuracy = `100 − MAPE` over the last 10 days, measured against the shaped daily forecast, for the
**2,385 SKUs that sold in that window**.

| Accuracy band | SKUs |
|---|---|
| 90–100 % | 41 |
| 80–90 % | 73 |
| 70–80 % | 169 |
| 50–70 % | 506 |
| 0–50 % | **1,596** |

- **Simple per-SKU mean accuracy: 28.9 %** (median 24 %).
- **Volume-weighted accuracy: 51.5 %.**

**Interpretation — this is an intermittent-demand problem, not a broken model.** The large gap
between the simple mean (29 %) and the volume-weighted figure (52 %) tells the story clearly:

- High-volume SKUs (e.g. `474-01-M`, ~41 units/day) forecast at **~88 %** — the model works well
  where there is a stable signal.
- The long tail of SKUs selling 0–2 units on most days produces erratic daily series where *any*
  point forecast scores a poor MAPE. These 1,596 low-accuracy SKUs are individually tiny but
  numerous.

The right response is **not** to chase per-SKU accuracy on intermittent items with a daily point
model, but to (a) forecast them with intermittent-demand methods (Croston/SBA) or at a weekly
grain, and (b) judge the system on volume-weighted accuracy and service level. See §9.

---

## 6. Production Planning

### 6.1 Business rules

```
inventoryQty                    = 0        (no on-hand inventory in source — see §9)
wipQty                          = TOTAL_WIP_QTY
availableQty                    = inventoryQty + wipQty
totalSuggestedProduction        = forecast35
calculatedProductionSuggestion  = max(0, forecast35 − availableQty)
stockStatus                     = "In Stock" if availableQty ≥ forecast35 else "Produce"
```

### 6.2 Plan totals (next 35 days)

| Metric | Value |
|---|---|
| SKUs with demand in the 35-day window | 3,009 |
| Total 35-day demand (suggested production, gross) | 107,032 units |
| Work-in-progress (WIP) on hand | 364,899 units |
| **Net production suggestion** (after WIP) | **82,657 units** |
| SKUs flagged **Produce** | 2,176 |

### 6.3 Highest-priority SKUs (by 35-day demand)

| SKU | 35-day forecast | WIP | Net to produce |
|---|---|---|---|
| 474-01-M | 1,421 | 0 | 1,421 |
| 474-01-L | 1,260 | 0 | 1,260 |
| 474-01-XL | 1,215 | 300 | 915 |
| 474-01-XXL | 1,189 | 1 | 1,188 |
| 417-03-M | 1,125 | 198 | 927 |
| 417-03-L | 1,042 | 0 | 1,042 |
| 474-01-S | 923 | 56 | 867 |
| 417-03-XL | 824 | 0 | 824 |
| 417-03-S | 760 | 0 | 760 |
| 474-03-XXL | 746 | 0 | 746 |

Design **474-01** (the kurta/top family across sizes) is the dominant production priority.

---

## 7. System Architecture

**Backend — FastAPI** (`api/`):

| Endpoint | Purpose |
|---|---|
| `GET /api/sku-production-plan` | Paginated plan rows, ordered by 35-day demand |
| `GET /api/sku-production-plan/{sku}/breakdown` | Per-SKU date-wise forecast vs actual (drill-down) |
| `GET /api/sku-production-plan/breakdown` | Bulk day-wise breakdown (paginated) |
| `GET /api/top-states` · `/top-cities` · `/top-warehouses` | Revenue + quantity by region, 10/30/90-day windows |
| `GET /health` | Liveness + row/snapshot info |

Data is aggregated once at startup from the CSV (~5 s) with a mock fallback so the API always boots.

**Frontend — React + MUI + TanStack Table** (`dashboard/`):

- **SKU Production Plan** table with per-row drill-down (date-wise forecast vs actual + accuracy).
- **Top Selling States / Cities / Warehouses** with a **10D / 30D / 90D** toggle showing Revenue and
  Sale Quantity per window.

Run: backend `uvicorn main:app --app-dir api --port 8000`; dashboard `npm run dev` in `dashboard/`.

---

## 8. Key Insights

1. **Channel concentration** — Flipkart + Meesho = ~70 % of volume; channel risk is real.
2. **Geographic concentration** — South India (KA/TN/KL) leads; tailor regional stock accordingly.
3. **Strong weekend seasonality** — Sunday runs +14 %; staffing and dispatch should anticipate it.
4. **Seasonal build to March** — plan capacity for a Q1 (Jan–Mar) demand peak.
5. **A few designs dominate production** — design 474-01 alone tops the 35-day plan across sizes.
6. **The catalog is a classic long tail** — a small set of fast movers + thousands of slow/intermittent SKUs; forecasting strategy must differ between the two.

---

## 9. Limitations & Recommendations

### Limitations

| # | Limitation | Impact |
|---|---|---|
| 1 | **No on-hand inventory** in source | `availableQty` = WIP only; production suggestions are overstated for any SKU that has stock on shelves. **Highest-priority data gap.** |
| 2 | **30.7 % return rate** | Gross revenue and gross demand overstate true sell-through; net (`settlement_amount`) is materially lower. |
| 3 | **Point model on intermittent demand** | Poor per-SKU accuracy on the long tail (1,596 SKUs < 50 %). |
| 4 | **Placeholder warehouse** (`DEFAULT 34455`) | Warehouse ranking is dominated by a catch-all and is not operationally meaningful as-is. |
| 5 | **Single seasonal cycle of history** (~15 months) | Annual seasonality can be estimated but not validated across multiple years. |

### Recommendations

1. **Add an inventory feed** (stock-on-hand per SKU/warehouse). This is the single change that
   would most improve the production plan's correctness.
2. **Segment the forecasting approach:**
   - Fast movers → keep/extend the seasonal model (add trend; consider Holt-Winters / ETS).
   - Intermittent SKUs → **Croston's / SBA** method, forecast at weekly grain.
3. **Forecast net of returns** (or model a return-rate haircut per channel) so the plan reflects
   true demand.
4. **Map warehouse codes** to real DCs and exclude/relabel the `DEFAULT` placeholder.
5. **Track service-level / fill-rate**, not just MAPE — the business goal is meeting demand, and
   volume-weighted accuracy + stockout rate are the metrics that matter.
6. **Add safety stock** = f(lead time, demand variability, target service level) on top of the
   point forecast before issuing production orders.

---

*Generated from `final_merged_data.csv` via the project's forecasting pipeline (`api/data.py`).
All figures reflect the data snapshot of 8 June 2026.*
