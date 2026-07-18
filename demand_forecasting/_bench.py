"""Throwaway: parity + speed check for the vectorised forecaster."""
import time
import numpy as np
import pandas as pd
import config, data_processing as dp, feature_engineering as fe, train, forecast

# --- synthetic data: many SKUs ---
rng = np.random.default_rng(1)
dates = pd.date_range("2025-03-18", "2026-06-07", freq="D")
n_sku = 4000
skus = [f"S{idx:05d}" for idx in range(n_sku)]
rows = []
# sparse: each SKU sells on a random subset of days
for s in skus:
    ndays = rng.integers(20, 120)
    sdays = rng.choice(dates, size=ndays, replace=False)
    for d in sdays:
        rows.append((s, s, 1, "Delivered", pd.Timestamp(d), s[:3]))
raw = pd.DataFrame(rows, columns=["product_sku_code", "listing_sku_code", "qty",
                                  "order_status", "order_date", "DESIGN_NO"])
raw["DESIGN_GROUP"] = "G"; raw["COLOR"] = "Red"; raw["SECTION"] = "Top"
raw["CATALOG_NAME"] = "C"; raw["LAUNCH_DATE"] = pd.Timestamp("2025-02-01")

clean = dp.clean_data(raw)
panel, static = dp.build_panel(clean, "listing_sku_code")
feats, fcols, ccols = fe.make_features(panel, static, "listing_sku_code")
split = train.time_based_split(feats)
model = train.train_lightgbm(feats[split.train], feats[split.val], fcols, ccols)
cat_dtypes = {c: feats[c].dtype for c in ccols}
print(f"SKUs={panel['listing_sku_code'].nunique()}  panel_rows={len(panel):,}")

# --- vectorised forecast timing ---
t0 = time.time()
fc = forecast.RecursiveForecaster(model, fcols, static, "listing_sku_code",
                                  cat_dtypes=cat_dtypes, verbose=False)
out_vec = fc.forecast(panel, 90)
t_vec = time.time() - t0
print(f"vectorised 90-day forecast: {t_vec:.1f}s  rows={len(out_vec):,}  "
      f"total_qty={out_vec['forecast_qty'].sum():.1f}")

# --- parity check on day 1 vs a manual make_features step ---
ids = np.sort(panel["listing_sku_code"].dropna().unique())
last_date = panel["date"].max()
new = pd.DataFrame({"listing_sku_code": ids, "date": last_date + pd.Timedelta(days=1),
                    "net_demand": np.nan})
work = pd.concat([panel[["listing_sku_code", "date", "net_demand"]], new], ignore_index=True)
f2, _, _ = fe.make_features(work, static, "listing_sku_code")
today = f2[f2["date"] == last_date + pd.Timedelta(days=1)].sort_values("listing_sku_code")
ref = np.clip(model.predict(today[fcols]), 0, None)
day1 = out_vec[out_vec["forecast_date"] == last_date + pd.Timedelta(days=1)] \
    .sort_values("sku")["forecast_qty"].to_numpy()
maxdiff = np.max(np.abs(np.round(ref, 2) - day1))
print(f"day-1 parity max abs diff vs make_features path: {maxdiff:.4f}")
assert maxdiff < 1e-9, "PARITY FAILED"
print("PARITY OK")
