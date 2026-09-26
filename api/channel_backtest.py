"""Read-only diagnostic: walk-forward backtest of the CHANNEL-WISE forecast
(api/data.py's per-channel proportional allocation - see
_apply_channel_forecast_with_cold_start() / _borrow_channel_mix() /
_apply_channel_forecast_share()) against real per-channel actual sales.

IMPORTANT SCOPE NOTE: the channel-wise forecast is NOT an independently
trained model. It is a top-down split of the already-backtested POOLED
Style/design-level forecast (scored by data.py's
_backtest_model_accuracy()/_backtest_design_model_accuracy(), which populate
SKU_MODEL_HIST/DESIGN_MODEL_HIST) by each channel's historical share of real
actual sales. This backtest can therefore only ever be as good as - and can
only ever produce a result once - the POOLED backtest itself has scoreable
matured snapshots (see data.MATURATION_DAYS). If that shows 0 scoreable
snapshots, this will too - it isn't a separate limitation, it's the same
one, one layer downstream.

This script also does NOT attempt to replay the cold-start channel-MIX
borrowing (_borrow_channel_mix) point-in-time — reconstructing "which
designs were eligible donors, and what their own mix looked like, as of
each historical week" is a materially bigger undertaking than the
walk-forward share reconstruction below, and cold-start-blended designs are
by definition thin-history (small volume), so they contribute little to a
volume-weighted WAPE regardless. This scores the PLAIN proportional-share
case (data.py's _apply_channel_forecast_share), which covers every design
with enough of its own history to not need borrowing.

Method, for every DESIGN_MODEL_HIST entry ((design, week) with a genuine,
already-walk-forward-validated pooled forecast):
  1. Take that REAL backtested pooled forecast for the design/week (no
     re-derivation - reuses the same validated number the pooled backtest
     already computed).
  2. Reconstruct the channel-mix share for that design AS OF just BEFORE
     that week (real actual sales strictly before the week's Monday only) -
     a point-in-time split, not today's full-history mix, so a channel-mix
     shift that happened AFTER the scored week can't leak into its own
     evaluation.
  3. Multiply to get a predicted per-channel qty for that week.
  4. Compare against the REAL per-channel actual for that week.
  5. Report pooled WAPE/accuracy, overall and per-channel.

Run: python api/channel_backtest.py
"""
from __future__ import annotations

import os

# Same reasoning as backtest_sweep.py: avoid a real LGBM retrain / lingering
# daily-refresh thread - this only needs the already-cached backtest state
# (SKU_MODEL_HIST/DESIGN_MODEL_HIST), which populates the same way
# regardless of FORECAST_MODEL.
os.environ.setdefault("FORECAST_MODEL", "naive")
os.environ.setdefault("DAILY_REFRESH", "off")

import pandas as pd

import data


def main() -> None:
    if not data.DESIGN_MODEL_HIST:
        print(
            "0 scoreable (design, week) pairs in DESIGN_MODEL_HIST - the pooled "
            "walk-forward backtest itself has no matured snapshots yet (see "
            "data.MATURATION_DAYS and how many days of lgbm_design_forecasts_d*_*.json "
            "history exist in api/.cache). The channel-wise backtest is entirely "
            "downstream of that and can't produce a result until it does."
        )
        return

    df = data._channel_source_base_df()
    if df is None or df.empty:
        print("_channel_source_base_df() is empty - can't score against real actual sales.")
        return

    model_err = model_act = 0.0
    channel_err: dict[str, float] = {}
    channel_act: dict[str, float] = {}
    scored_pairs = 0
    skipped_no_prior_history = 0

    for design_no, weeks in data.DESIGN_MODEL_HIST.items():
        design_rows = df[df["DESIGN_NO"].astype(str) == design_no]
        if design_rows.empty:
            continue
        design_rows = design_rows.assign(
            _marketplace=[
                data._marketplace_of(ch, src)
                for ch, src in zip(design_rows["channel_name"], design_rows["source"])
            ]
        )
        for week_iso, pooled_forecast in weeks.items():
            week_start = pd.Timestamp(week_iso)
            week_end = week_start + pd.Timedelta(days=6)

            prior = design_rows[design_rows["order_date"] < week_start]
            if prior.empty:
                skipped_no_prior_history += 1
                continue
            prior_by_channel = prior.groupby("_marketplace")["qty"].sum()
            prior_total = float(prior_by_channel.sum())
            if prior_total <= 0:
                skipped_no_prior_history += 1
                continue
            share = prior_by_channel / prior_total

            actual_week = design_rows[
                (design_rows["order_date"] >= week_start) & (design_rows["order_date"] <= week_end)
            ]
            actual_by_channel = actual_week.groupby("_marketplace")["qty"].sum()

            for channel in set(share.index) | set(actual_by_channel.index):
                pred = float(pooled_forecast) * float(share.get(channel, 0.0))
                act = float(actual_by_channel.get(channel, 0.0))
                model_err += abs(act - pred)
                model_act += act
                channel_err[channel] = channel_err.get(channel, 0.0) + abs(act - pred)
                channel_act[channel] = channel_act.get(channel, 0.0) + act
            scored_pairs += 1

    print(f"Scored {scored_pairs} (design, week) pairs "
          f"({skipped_no_prior_history} skipped - no channel history before that week).")
    if model_act <= 0:
        print("No real actual sales matched to score against - can't compute WAPE/accuracy.")
        return

    overall_pct = max(0, min(100, round((1 - model_err / model_act) * 100)))
    print(f"\nOverall channel-wise accuracy: {overall_pct}% "
          f"(volume-weighted, pooled across every channel/design/week)")

    print("\nPer-channel:")
    for channel in sorted(channel_act, key=lambda c: channel_act[c], reverse=True):
        act = channel_act[channel]
        err = channel_err[channel]
        pct = max(0, min(100, round((1 - err / act) * 100))) if act > 0 else 0
        print(f"  {channel:<20} accuracy={pct:>3}%  actual={act:>10,.0f}  abs_err={err:>10,.0f}")


if __name__ == "__main__":
    main()
