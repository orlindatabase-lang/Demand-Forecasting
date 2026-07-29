# Demand Forecasting

FastAPI backend (`api/`) + React/Vite dashboard (`dashboard/`) for SKU/design-level
demand forecasting and production planning on top of merged sales + ERP order history.

## Forecasting

Weekly demand forecasting lives entirely in [`api/lgbm_forecast.py`](api/lgbm_forecast.py):

- **Grain:** weekly (Monday-anchored) buckets, not daily — apparel demand is heavily
  intermittent, so weekly buckets cut noise and a 6-step recursive forecast beats 35
  daily steps.
- **Model:** grid-searches several XGBoost and LightGBM (Tweedie) configs on a
  time-based train/valid/test split, selects the best single family or a 50/50 blend
  by validation WAPE, then refits the winner(s) on all history for the deployed
  forecast (`compute()` for SKU-level, `compute_design()` for design-level).
- **Features:** lags/rolling stats over past weeks, calendar features, product
  attributes, cross-SKU design-level demand, a festival signal (`api/festival.py`),
  and a cold-start blend for young designs (`api/similar_design.py`).
- **Business rules & cleaning** (net-demand sign from order status, column names) are
  inlined at the top of `lgbm_forecast.py` — there is no separate config/pipeline
  package.

Results are cached to disk per snapshot date (`api/.cache/`) and served by the API,
consumed by the dashboard's SKU Production Plan table and drill-downs.

## Run

Backend (from `api/`, using the `venv` at the repo root — not `.venv`):

```bash
..\venv\Scripts\python.exe run.py
```

Dashboard:

```bash
cd dashboard
npm run dev
```

The dashboard fetches all business data from the API at `http://localhost:8000`
(override via `dashboard/.env` `VITE_API_URL`).
