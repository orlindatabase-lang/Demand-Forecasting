# Apparel Demand Forecasting

Production-quality, modular forecasting system that predicts **net demand** for
apparel products at two levels — **SKU** (`listing_sku_code`) and **DESIGN_NO** —
and produces 7 / 30 / 90-day forecasts driven by a LightGBM model.

> Scope: forecasting only. No deployment, API, MLOps, RL, dashboards, or
> inventory optimization (those come later).

## Input

A single CSV, `final_merged_data.csv` (written by the data-assembly notebook),
with these columns:

```
product_sku_code, listing_sku_code, qty, order_status, order_date, total,
fee_discounts, settlement_amount, buyer_city, buyer_state, channel_name,
warehouse_name, delivery_date, DESIGN_NO, DESIGN_GROUP, CATALOG_NAME, COLOR,
LAUNCH_DATE, SECTION, TOTAL_WIP_QTY, PENDING_QTY_PIECES
```

## Business rules baked in

- **Timeline** = `order_date` (never `delivery_date`).
- **net_demand** per order line:
  | order_status | demand |
  |---|---|
  | Delivered | `+qty` |
  | Return Received | `-qty` |
  | Cancelled | `0` |
  | Cancelled Return Received | `0` |

  (matched case-insensitively; `cancel` is tested before `return` so
  "Cancelled Return Received" resolves to 0). Any unrecognised status is treated
  as 0 demand and reported in the data-quality summary.

## Modules

| File | Responsibility |
|---|---|
| `config.py` | Paths, column names, status rules, feature/split/LGBM settings |
| `data_processing.py` | Clean, validate, QA report, daily aggregation, dense timeline |
| `feature_engineering.py` | Lag, rolling, calendar, and product features (leak-free) |
| `train.py` | Time-based split, walk-forward CV, LightGBM, feature importance |
| `evaluate.py` | MAE/RMSE/MAPE/SMAPE/R², naive baselines, comparison tables |
| `forecast.py` | Recursive 7/30/90-day forecasts per SKU and DESIGN_NO |
| `run_pipeline.py` | Orchestrator that runs everything and writes artefacts |

## Features

- **Lags:** 1, 7, 14, 28, 56
- **Rolling:** mean 7/14/28, std 7/28 (computed on `shift(1)` — no leakage)
- **Calendar:** day_of_week, week_of_year, month, quarter, year, is_weekend,
  is_month_start, is_month_end
- **Product:** DESIGN_GROUP, COLOR, SECTION, CATALOG_NAME, product_age_days

Missing sales days are filled with **0 demand** on a per-SKU complete daily
timeline before features are built.

## Validation

- Strict **time-based** train / validation / test split (no random shuffling).
- **Walk-forward** validation (expanding window, 3 folds × 30 days) as a
  stability check.
- Baselines: **Last Day**, **Last Week Same Day**, **Moving Average (7d)**.
- LightGBM uses a `tweedie` objective (well suited to zero-inflated demand).

## Run

```bash
pip install -r requirements.txt

cd demand_forecasting
python run_pipeline.py              # both levels
python run_pipeline.py --level sku  # SKU only
python run_pipeline.py --data ../final_merged_data.csv
```

## Outputs (`demand_forecasting/outputs/`)

```
reports/
  data_quality_report.csv
  {level}_metrics_comparison.csv
  {level}_feature_importance.csv
  {level}_feature_importance_top20.csv      # with business interpretation
  {level}_walk_forward.csv
models/
  {level}_lgbm.joblib                        # model + feature spec
forecasts/
  {level}_forecast_7d.csv
  {level}_forecast_30d.csv
  {level}_forecast_90d.csv                   # forecast_date, sku, forecast_qty
```

`level` is `sku` or `design`.
