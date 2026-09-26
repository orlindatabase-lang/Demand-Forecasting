"""Retrospective (offline) walk-forward backtest for the design-level model
(lgbm_forecast.compute_design()) — 2026-09-22, user-requested: "Improve the
Back Test Accuracy" (the model's real accuracy, not the measurement plumbing).

The live walk-forward backtest (data._backtest_design_model_accuracy) can only
score a forecast snapshot once its target week has both calendar-elapsed AND
matured (MATURATION_DAYS=7) — and this app's entire on-disk snapshot cache
only spans 2026-09-17..2026-09-21, so as of today it has ZERO scoreable
weeks (earliest possible: 2026-10-05, verified separately). That means the
existing api/backtest_sweep.py (which reuses the same live-cache-dependent
scorer) is ALSO unusable right now for validating any change.

This script sidesteps the cache entirely: it re-derives what compute_design()
would have forecast AS OF several past historical dates, by simply truncating
the full sales history to <= that date before calling compute_design() (the
function has no other notion of "now" than the max date in what it's given —
verified by reading its source). The target week's REAL actual sales already
exist in the untruncated history, so this scores against real ground truth,
not held-out synthetic data. Same pooled/volume-weighted accuracy formula as
data._backtest_design_model_accuracy, applied identically across every cutoff
so the numbers are directly comparable to what the live backtest will
eventually report.

Two things get evaluated here, both already flagged as open/unverified
elsewhere in this codebase:
  1. GROSS vs NET training target (lgbm_forecast.py's own top-of-file
     docstring: "training on gross measurably hurt backtested accuracy vs.
     the NET allowlist" as of 2026-08-03, marked "re-verify ... once enough
     post-change snapshots exist" — this IS that re-verification, done
     retrospectively instead of waiting.
  2. FORECAST_BLEND value + the post-hoc festival multiplier (the two open
     questions backtest_sweep.py's own docstring already posed) — swept
     cheaply on top of each run's raw model output (no retraining needed
     per blend value).

Read-only: no cache files written, no production code touched while running
(GROSS/NET target swap is a monkeypatch of lgbm_forecast._GROSS_SALE_NORM,
restored in a finally block either way).

Run: venv/Scripts/python.exe retro_backtest.py
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

os.environ.setdefault("FORECAST_MODEL", "naive")
os.environ.setdefault("DAILY_REFRESH", "off")

import pandas as pd  # noqa: E402

import data  # noqa: E402
import lgbm_forecast  # noqa: E402

_BLEND_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
_CUTOFF_SPACING_DAYS = 28
_MIN_HISTORY_DAYS = 120  # don't cut off so early the model has near-nothing to train on


def _forecast_week1(cutoff: "pd.Timestamp"):
    last_monday = cutoff - pd.Timedelta(days=cutoff.weekday())
    week1_start = last_monday + pd.Timedelta(days=7)
    week1_end = week1_start + pd.Timedelta(days=6)
    return week1_start, week1_end


def _pick_cutoffs(min_date: "pd.Timestamp", max_date: "pd.Timestamp") -> list["pd.Timestamp"]:
    cutoffs = []
    c = min_date + pd.Timedelta(days=_MIN_HISTORY_DAYS)
    while True:
        w1s, w1e = _forecast_week1(c)
        if w1e + pd.Timedelta(days=data.MATURATION_DAYS) >= max_date:
            break
        cutoffs.append(c)
        c = c + pd.Timedelta(days=_CUTOFF_SPACING_DAYS)
    return cutoffs


def _run_one_cutoff(df_full: "pd.DataFrame", cutoff: "pd.Timestamp", gross_sales_full: "pd.DataFrame"):
    """Returns {design_no: [w1..w13 forecast]} as-of `cutoff`, plus the actual
    week-1 target window and the naive week-1 baseline, for later scoring."""
    trunc = df_full[df_full["order_date"] <= cutoff]
    forecasts = lgbm_forecast.compute_design(trunc)

    week1_start, week1_end = _forecast_week1(cutoff)
    actual = gross_sales_full[
        (gross_sales_full["order_date"] >= week1_start) & (gross_sales_full["order_date"] <= week1_end)
    ].groupby("DESIGN_NO")["qty"].sum()

    lo = cutoff - pd.Timedelta(days=34)
    hist = gross_sales_full[(gross_sales_full["order_date"] >= lo) & (gross_sales_full["order_date"] <= cutoff)]
    naive_week = hist.groupby("DESIGN_NO")["qty"].sum() / 5.0

    return forecasts, actual, naive_week, week1_start


def _score(forecasts, actual, naive_week, week1_start, blend: float, festival_on: bool) -> tuple[float, float, int]:
    mult = data._week_festival(week1_start.date())[0] if festival_on else 1.0
    err = act_tot = 0.0
    n = 0
    for design_no, fc in forecasts.items():
        weekly = fc.get("weekly") if fc else None
        if not weekly:
            continue
        nw = float(naive_week.get(design_no, 0.0))
        act = float(actual.get(design_no, 0.0))
        pred = max(0.0, (blend * weekly[0] + (1 - blend) * nw) * mult)
        err += abs(act - pred)
        act_tot += act
        n += 1
    return err, act_tot, n


def main() -> None:
    df_full = data._SOURCE_DF
    if df_full is None:
        raise SystemExit("data._SOURCE_DF is empty — the module-level rebuild() at import may have failed.")
    df_full = df_full.dropna(subset=["product_sku_code"]).drop_duplicates()
    df_full = df_full[df_full["order_date"] <= pd.Timestamp.today().normalize()]

    status_norm = df_full["order_status"].astype(str).str.strip().str.lower()
    gross_sales_full = df_full[status_norm.isin(data._GROSS_SALE_STATUSES)]

    min_date = df_full["order_date"].min()
    max_date = df_full["order_date"].max()
    cutoffs = _pick_cutoffs(min_date, max_date)
    print(f"Data spans {min_date.date()} .. {max_date.date()}; using {len(cutoffs)} cutoffs, "
          f"every {_CUTOFF_SPACING_DAYS}d, first={cutoffs[0].date() if cutoffs else None}, "
          f"last={cutoffs[-1].date() if cutoffs else None}", file=sys.stderr)
    if not cutoffs:
        raise SystemExit("No viable cutoffs — not enough history + matured future window.")

    orig_gross_norm = lgbm_forecast._GROSS_SALE_NORM
    results: dict[str, list[dict]] = {"GROSS": [], "NET": []}
    try:
        for label, norm in (("GROSS", orig_gross_norm), ("NET", data._SOLD_STATUSES)):
            lgbm_forecast._GROSS_SALE_NORM = norm
            for i, cutoff in enumerate(cutoffs):
                t0 = __import__("time").time()
                forecasts, actual, naive_week, week1_start = _run_one_cutoff(df_full, cutoff, gross_sales_full)
                dt = __import__("time").time() - t0
                results[label].append({
                    "cutoff": cutoff, "forecasts": forecasts, "actual": actual,
                    "naive_week": naive_week, "week1_start": week1_start,
                })
                print(f"[{label}] cutoff {cutoff.date()} ({i+1}/{len(cutoffs)}): "
                      f"{len(forecasts)} designs forecast, {dt:.0f}s", file=sys.stderr)
    finally:
        lgbm_forecast._GROSS_SALE_NORM = orig_gross_norm

    print("\n=== GROSS vs NET training target, pooled across all cutoffs ===")
    print(f"{'target':<8}{'festival':<10}{'blend':<8}{'model_acc%':<12}{'designs_scored'}")
    summary = {}
    for label in ("GROSS", "NET"):
        for festival_on in (True, False):
            for blend in _BLEND_GRID:
                err_tot = act_tot = n_tot = 0.0
                for r in results[label]:
                    err, act, n = _score(r["forecasts"], r["actual"], r["naive_week"], r["week1_start"],
                                          blend, festival_on)
                    err_tot += err
                    act_tot += act
                    n_tot += n
                pct = max(0, min(100, round((1 - err_tot / act_tot) * 100))) if act_tot > 0 else 0
                summary[(label, festival_on, blend)] = pct
                print(f"{label:<8}{'on' if festival_on else 'off':<10}{blend:<8}{pct:<12}{int(n_tot)}")

    best_key = max(summary, key=lambda k: summary[k])
    print(f"\nBest combination: target={best_key[0]}, festival={'on' if best_key[1] else 'off'}, "
          f"blend={best_key[2]} -> {summary[best_key]}%")

    cur_blend = data.FORECAST_BLEND
    print(f"\nCurrent production config: target=GROSS, festival=on, blend={cur_blend} "
          f"-> {summary.get(('GROSS', True, cur_blend), 'n/a')}%")


if __name__ == "__main__":
    main()
