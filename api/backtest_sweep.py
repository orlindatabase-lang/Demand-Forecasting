"""Read-only diagnostic: sweep FORECAST_BLEND and the post-hoc festival
multiplier against the EXISTING cached model snapshots, to answer two open
questions from the forecasting lifecycle audit without touching production
code or retraining anything:

  1. Is FORECAST_BLEND=0.5 actually the best blend weight, or just an
     unvalidated default? (every other tunable constant in data.py cites an
     empirical verification — this one didn't.)
  2. Is the post-hoc festival multiplier (data.py's _adjusted_weekly) double-
     counting uplift the model already partly learned from its own
     festival_mult/is_festival/days_to_festival_peak training features?

Reuses data.py's own _backtest_model_accuracy — the real walk-forward scorer
against already-cached lgbm_forecasts_v*_*.json snapshots + actual sales —
by monkeypatching FORECAST_BLEND and _week_festival for each combination,
then restoring them. No cache files are written, no model is retrained, no
production code is modified: this only READS already-cached forecasts and
real sales history.

Run: python api/backtest_sweep.py
"""
from __future__ import annotations

import os

# Avoid side effects from the module-level `rebuild()` / `start_daily_refresh()`
# calls at the bottom of data.py: "naive" skips spawning a real LGBM retrain
# thread, and disabling the daily-refresh timer avoids a lingering background
# thread — this is a one-shot read-only diagnostic, not a long-running process.
os.environ.setdefault("FORECAST_MODEL", "naive")
os.environ.setdefault("DAILY_REFRESH", "off")

import pandas as pd

import data

_BLEND_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)


def main() -> None:
    df = data._SOURCE_DF
    if df is None:
        raise SystemExit(
            "data._SOURCE_DF is empty — the one-time rebuild() at import may have "
            "failed; check stderr above for a '[data] real load failed' message."
        )
    df = df.dropna(subset=["product_sku_code"]).drop_duplicates()
    df = df[df["order_date"] <= pd.Timestamp.today().normalize()]
    snap = pd.Timestamp(df["order_date"].max()).normalize()
    sales = df[df["order_status"].astype(str).str.strip().str.lower().isin(data._SOLD_STATUSES)]

    orig_blend = data.FORECAST_BLEND
    orig_week_festival = data._week_festival
    no_festival = lambda week_start: (1.0, None)  # noqa: E731 — post-hoc multiplier "off"

    rows: list[dict] = []
    try:
        for festival_on in (True, False):
            data._week_festival = orig_week_festival if festival_on else no_festival
            for blend in _BLEND_GRID:
                data.FORECAST_BLEND = blend
                model_pct, naive_pct, n_snapshots, sku_acc, _, _, _ = data._backtest_model_accuracy(sales, snap)
                rows.append({
                    "festival_multiplier": "on" if festival_on else "off",
                    "FORECAST_BLEND": blend,
                    "model_accuracy_pct": model_pct,
                    "naive_baseline_pct": naive_pct,
                    "snapshots_used": n_snapshots,
                    "skus_scored": len(sku_acc),
                })
    finally:
        data.FORECAST_BLEND = orig_blend
        data._week_festival = orig_week_festival

    print("\n=== FORECAST_BLEND x festival-multiplier sweep ===")
    print(f"{'festival':<10}{'blend':<8}{'model_acc%':<12}{'naive_acc%':<12}{'snapshots':<11}{'skus':<8}")
    for r in rows:
        print(f"{r['festival_multiplier']:<10}{r['FORECAST_BLEND']:<8}{r['model_accuracy_pct']:<12}"
              f"{r['naive_baseline_pct']:<12}{r['snapshots_used']:<11}{r['skus_scored']:<8}")

    if rows and rows[0]["snapshots_used"] == 0:
        print(
            "\nWARNING: 0 snapshots were scoreable — not enough matured cached forecasts "
            "exist yet (see _BACKTEST_MAX_SNAPSHOTS / MATURATION_DAYS in data.py). "
            "Re-run this after the API has been running daily for a couple of weeks."
        )
        return

    best = max(rows, key=lambda r: (r["model_accuracy_pct"], r["snapshots_used"]))
    print(f"\nBest combination: festival_multiplier={best['festival_multiplier']}, "
          f"FORECAST_BLEND={best['FORECAST_BLEND']} -> model_accuracy={best['model_accuracy_pct']}%")

    on_rows = {r["FORECAST_BLEND"]: r["model_accuracy_pct"] for r in rows if r["festival_multiplier"] == "on"}
    off_rows = {r["FORECAST_BLEND"]: r["model_accuracy_pct"] for r in rows if r["festival_multiplier"] == "off"}
    deltas = [on_rows[b] - off_rows[b] for b in _BLEND_GRID]
    print(f"festival-on minus festival-off, per blend value: "
          f"{dict(zip(_BLEND_GRID, deltas))}")
    print(
        "If festival-on is consistently WORSE than festival-off across most blend "
        "values, that's evidence of double-counting (the post-hoc multiplier is "
        "compounding an uplift the model's own festival features already learned). "
        "If festival-on is consistently BETTER, the post-hoc multiplier is pulling "
        "its weight and should stay."
    )


if __name__ == "__main__":
    main()
