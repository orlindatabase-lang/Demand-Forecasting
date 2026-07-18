"""
End-to-end orchestrator for the demand-forecasting system.

Run from inside the ``demand_forecasting`` folder::

    python run_pipeline.py                 # both levels, default data path
    python run_pipeline.py --level sku      # SKU level only
    python run_pipeline.py --data ../final_merged_data.csv

For each aggregation level (SKU and DESIGN_NO) it:
  1. cleans + validates the data and writes a QA report
  2. builds a dense daily demand panel and the feature matrix
  3. trains LightGBM with a time-based split + walk-forward validation
  4. compares LightGBM against three naive baselines on the test horizon
  5. writes feature importance with business interpretation
  6. trains a production model on all data and writes 7/30/90-day forecasts

All artefacts land under ``demand_forecasting/outputs/``.
"""
from __future__ import annotations

import argparse
import time
from typing import Dict

import joblib
import numpy as np
import pandas as pd

import config
import data_processing as dp
import feature_engineering as fe
import evaluate
import train
import forecast


def _log(msg: str) -> None:
    print(f"[pipeline] {msg}", flush=True)


def run_level(clean: pd.DataFrame, level_key: str,
              run_walk_forward: bool = config.RUN_WALK_FORWARD) -> Dict[str, object]:
    """Run the full modelling pipeline for one aggregation level."""
    id_col = config.LEVELS[level_key]["id_col"]
    label = config.LEVELS[level_key]["label"]
    _log(f"=== Level '{level_key}' ({label}) ===")

    # --- panel + features -------------------------------------------------- #
    panel, static = dp.build_panel(clean, id_col)
    if panel.empty:
        _log(f"no data for level '{level_key}', skipping.")
        return {}
    _log(f"panel rows={len(panel):,}  ids={panel[id_col].nunique():,}  "
         f"dates {panel[config.DATE_COL].min().date()}..{panel[config.DATE_COL].max().date()}")

    feats, feature_cols, cat_cols = fe.make_features(panel, static, id_col)

    # --- chronological split ---------------------------------------------- #
    split = train.time_based_split(feats)
    train_df, val_df, test_df = feats[split.train], feats[split.val], feats[split.test]
    _log(f"split -> train={len(train_df):,}  val={len(val_df):,}  test={len(test_df):,} "
         f"(val>={split.val_start.date()}, test>={split.test_start.date()})")

    if test_df.empty or train_df.empty:
        _log("not enough history for a time split; skipping model eval.")
        return {}

    # --- baselines on the test horizon ------------------------------------ #
    results: Dict[str, Dict[str, float]] = {}
    y_test = test_df[config.TARGET].to_numpy()
    for name, preds in evaluate.baseline_predictions(test_df).items():
        results[name] = evaluate.compute_metrics(y_test, preds)

    # --- LightGBM --------------------------------------------------------- #
    _log("training LightGBM (early stopping on validation)...")
    t = time.perf_counter()
    model = train.train_lightgbm(train_df, val_df, feature_cols, cat_cols)
    best_iter = getattr(model, "best_iteration_", None)
    test_preds = np.clip(model.predict(test_df[feature_cols]), 0, None)
    results["LightGBM"] = evaluate.compute_metrics(y_test, test_preds)
    _log(f"LightGBM trained in {time.perf_counter() - t:.1f}s (best_iter={best_iter})")

    # --- walk-forward stability check (opt-in: ~triples training time) ----- #
    if run_walk_forward:
        _log("running walk-forward validation...")
        t = time.perf_counter()
        wf = train.run_walk_forward(feats, feature_cols, cat_cols)
        if not wf.empty:
            wf.to_csv(config.REPORT_DIR / f"{level_key}_walk_forward.csv", index=False)
            _log(f"walk-forward mean MAE={wf['MAE'].mean():.3f}  "
                 f"SMAPE={wf['SMAPE'].mean():.1f}%  ({time.perf_counter() - t:.1f}s)")
    else:
        _log("walk-forward validation skipped (enable with --walk-forward).")

    # --- comparison table ------------------------------------------------- #
    comp = evaluate.comparison_table(results)
    comp.to_csv(config.REPORT_DIR / f"{level_key}_metrics_comparison.csv", index=False)
    _log(f"model comparison (test horizon):\n{comp.to_string(index=False)}")

    # --- feature importance ----------------------------------------------- #
    imp = train.get_feature_importance(model, feature_cols)
    interp = evaluate.interpret_importance(imp, top_n=20)
    imp.to_csv(config.REPORT_DIR / f"{level_key}_feature_importance.csv", index=False)
    interp.to_csv(config.REPORT_DIR / f"{level_key}_feature_importance_top20.csv", index=False)
    _log(f"top features: {', '.join(interp['feature'].head(8))} ...")

    # --- production model on ALL data + forecasts ------------------------- #
    _log(f"fitting production model on all data ({len(feats):,} rows, "
         f"{best_iter or config.LGBM_PARAMS['n_estimators']} trees)...")
    t = time.perf_counter()
    final_model = train.fit_final_model(feats, feature_cols, cat_cols,
                                         n_estimators=best_iter)
    _log(f"production model fitted in {time.perf_counter() - t:.1f}s")
    joblib.dump(
        {"model": final_model, "feature_cols": feature_cols,
         "categorical_cols": cat_cols, "id_col": id_col, "level": level_key},
        config.MODEL_DIR / f"{level_key}_lgbm.joblib",
    )

    # capture training categorical dtypes so the forecaster encodes categories
    # identically to fit time
    cat_dtypes = {c: feats[c].dtype for c in cat_cols}
    _log(f"generating {config.FORECAST_HORIZONS} day forecasts...")
    t = time.perf_counter()
    horizon_frames = forecast.generate_horizons(
        final_model, feature_cols, static, id_col, panel, cat_dtypes=cat_dtypes)
    _log(f"forecasts generated in {time.perf_counter() - t:.1f}s")
    for h, fdf in horizon_frames.items():
        fdf = fdf.assign(level=level_key)
        fdf.to_csv(config.FORECAST_DIR / f"{level_key}_forecast_{h}d.csv", index=False)
        _log(f"forecast {h}d -> {len(fdf):,} rows "
             f"(total qty={int(fdf['forecast_qty'].sum()):,})")

    return {"comparison": comp, "importance": interp, "forecasts": horizon_frames}


def main() -> None:
    parser = argparse.ArgumentParser(description="Apparel demand-forecasting pipeline")
    parser.add_argument("--data", default=str(config.DATA_PATH),
                        help="path to final_merged_data.csv")
    parser.add_argument("--level", choices=["sku", "design", "both"], default="both",
                        help="aggregation level(s) to run")
    parser.add_argument("--walk-forward", action="store_true",
                        help="run walk-forward CV (slower; ~triples training time)")
    args = parser.parse_args()

    _log(f"loading raw data from {args.data}")
    raw = dp.load_raw(args.data)
    clean = dp.clean_data(raw)

    qa = dp.data_quality_report(raw, clean)
    qa.to_csv(config.REPORT_DIR / "data_quality_report.csv", index=False)
    _log(f"data quality report:\n{qa.to_string(index=False)}")

    run_wf = args.walk_forward or config.RUN_WALK_FORWARD
    levels = ["sku", "design"] if args.level == "both" else [args.level]
    for lvl in levels:
        run_level(clean, lvl, run_walk_forward=run_wf)

    _log(f"done. artefacts in {config.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
