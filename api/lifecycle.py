"""
Product Lifecycle Intelligence — dynamic SKU classification beside the forecast.

For every SKU (from real sold units; returns tracked separately) it derives:
  * launch_drr   — units/day over the first 20 days after LAUNCH_DATE
  * current_drr  — units/day over the trailing 30 days (rolling, refreshed each load)
  * growth_rate  — (current_drr − launch_drr) / launch_drr   (the lifecycle trend)
  * launch / current tier — percentile bucket T0..T3 of the launch / current DRR
  * lifecycle_stage — Emerging / Growing / Stable / Declining / Dead / Reviving
  * scores       — health / risk / production_priority (0-100)
  * suggested production — stage factor × forecast35 − available

The trajectory is driven by ``growth_rate`` (current vs launch) — the signal that
directly answers "launched strong but now declining?" / "launched weak but now
growing?". ``classify(rows, …)`` mutates the plan rows in place; all thresholds
are the named constants below.

NOTE: this is a DECISION layer. Only the causal launch features (days_since_launch,
launch_drr, launch_tier) may feed the weekly model's training. The dynamic
signals here would leak the future.
"""
from __future__ import annotations

LAUNCH_WINDOW = 20        # days after launch that define launch_drr
DEAD_DAYS = 60            # no sales in this many days → Dead
GROW_THRESH = 0.20        # growth ≥ +20% → Growing
DECLINE_THRESH = -0.30    # growth ≤ −30% → Declining
WINNER_GROWTH = 1.00      # growth ≥ +100% (doubled) AND above-median velocity → Emerging winner
_TIER_PCTL = (0.40, 0.70, 0.90)   # T1 / T2 / T3 percentile cuts of active DRR

# stage → production factor, policy label, and component scores (0..1)
STAGE = {
    "Emerging":  {"factor": 1.40, "policy": "Scale up — back the ramp", "h": .85, "r": .35, "p": .90},
    "Growing":   {"factor": 1.25, "policy": "Increase production",      "h": .95, "r": .15, "p": .95},
    "Stable":    {"factor": 1.00, "policy": "Maintain to demand",       "h": .60, "r": .30, "p": .50},
    "Reviving":  {"factor": 1.15, "policy": "Increase cautiously",      "h": .70, "r": .50, "p": .70},
    "Declining": {"factor": 0.50, "policy": "Reduce — taper off",       "h": .25, "r": .80, "p": .20},
    "Dead":      {"factor": 0.00, "policy": "Stop / discontinue",       "h": .00, "r": 1.0, "p": .00},
}


def _tier(drr: float, cuts: list[float], prefix: str) -> str:
    """Percentile tier label prefix_T0..T3 (T0 = weakest / zero)."""
    return f"{prefix}_T0" if drr <= 0 else f"{prefix}_T{sum(drr >= c for c in cuts)}"


def classify(rows, sales, returns, launch_ts, snap) -> None:
    """Attach lifecycle intelligence to each plan row (mutates in place)."""
    import pandas as pd

    snap_ts = pd.Timestamp(snap)
    idx = pd.Index([r.skuCode for r in rows], name="sku")

    s = sales[["product_sku_code", "order_date", "qty"]].copy()
    s["qty"] = pd.to_numeric(s["qty"], errors="coerce").fillna(0.0)
    s["dtt"] = (snap_ts - s["order_date"]).dt.days                              # days-to-today
    s["dal"] = (s["order_date"] - s["product_sku_code"].map(launch_ts)).dt.days  # days-after-launch

    def win(mask) -> "pd.Series":
        return s.loc[mask].groupby("product_sku_code")["qty"].sum().reindex(idx).fillna(0.0).astype(float)

    t30 = win((s.dtt >= 0) & (s.dtt < 30))
    t60 = win((s.dtt >= 0) & (s.dtt < DEAD_DAYS))
    prior = win((s.dtt >= 30) & (s.dtt < 60))    # 30–60 days ago (dormancy test)
    older = win((s.dtt >= 60) & (s.dtt < 90))    # 60–90 days ago (sold before?)
    lwin = win((s.dal >= 0) & (s.dal < LAUNCH_WINDOW))

    rr = returns[["product_sku_code", "order_date", "qty"]].copy()
    rr["qty"] = pd.to_numeric(rr["qty"], errors="coerce").abs().fillna(0.0)
    rr["dtt"] = (snap_ts - rr["order_date"]).dt.days
    ret30 = rr.loc[(rr.dtt >= 0) & (rr.dtt < 30)].groupby("product_sku_code")["qty"].sum().reindex(idx).fillna(0.0).astype(float)

    launch_ts_idx = launch_ts.reindex(idx)
    age = (snap_ts - launch_ts_idx).dt.days.astype("float64")   # NaN where no launch date
    fc35 = pd.Series([r.forecast35 for r in rows], index=idx, dtype=float)
    avail = pd.Series([r.availableQty for r in rows], index=idx, dtype=float)

    current = t30 / 30.0
    eff = age.clip(upper=LAUNCH_WINDOW)                                   # effective launch-window days
    launch = (lwin / eff.where(eff > 0)).where(age.notna()).fillna(current)
    growth = (current - launch) / launch.clip(lower=1e-6)
    ret_rate = (ret30 / (t30 + ret30).clip(lower=1e-6)).clip(0, 1)
    turnover = (t30 / avail.clip(lower=1)).clip(0, 1)                     # demand vs on-hand stock
    overstock = ((avail - fc35) / fc35.clip(lower=1)).clip(0, 1)         # stock >> demand
    stockout = ((fc35 - avail) / fc35.clip(lower=1)).clip(0, 1)          # demand >> stock

    act = current[current > 0]
    cc = [float(act.quantile(p)) for p in _TIER_PCTL] if len(act) else [0.0, 0.0, 0.0]
    actL = launch[launch > 0]
    lc = [float(actL.quantile(p)) for p in _TIER_PCTL] if len(actL) else [0.0, 0.0, 0.0]
    med = float(act.median()) if len(act) else 0.0
    vel_rank = current.rank(pct=True)
    grow_rank = growth.clip(-1, 5).rank(pct=True)

    def stage_of(sku: str) -> str:
        a = age.get(sku)
        if pd.notna(a) and a <= LAUNCH_WINDOW:
            return "Emerging"                        # launch / observation phase
        if t60[sku] <= 0:
            return "Dead"
        if older[sku] > 0 and prior[sku] <= 0 and t30[sku] > 0:
            return "Reviving"                        # sold before, went quiet, selling again
        g = growth[sku]
        if g <= DECLINE_THRESH:
            return "Declining"
        if g >= WINNER_GROWTH and current[sku] >= med:
            return "Emerging"                        # winner: doubled+ off a real base
        if g >= GROW_THRESH:
            return "Growing"
        return "Stable"

    for r in rows:
        sku = r.skuCode
        st = stage_of(sku)
        sc = STAGE[st]
        health = 100 * (.35 * vel_rank[sku] + .20 * grow_rank[sku] + .15 * turnover[sku]
                        + .10 * (1 - ret_rate[sku]) + .20 * sc["h"])
        risk = 100 * (.45 * sc["r"] + .30 * overstock[sku] + .25 * ret_rate[sku])
        prio = 100 * (.45 * sc["p"] + .30 * vel_rank[sku] + .25 * stockout[sku])
        a = age.get(sku)

        r.launchTier = "Unknown" if pd.isna(a) else _tier(float(launch[sku]), lc, "Launch")
        r.currentTier = _tier(float(current[sku]), cc, "Current")
        r.tier = r.currentTier                                            # back-compat ("Tier" field)
        r.lifecycleStage = st
        r.daysSinceLaunch = -1 if pd.isna(a) else int(a)
        r.launchDate = "" if pd.isna(a) else launch_ts_idx[sku].date().isoformat()
        r.launchDrr = round(float(launch[sku]), 2)
        r.currentDrr = round(float(current[sku]), 2)
        r.growthRate = round(float(growth[sku]), 2)
        r.healthScore = int(round(health))
        r.riskScore = int(round(risk))
        r.productionPriority = int(round(prio))
        r.suggestedProduction = max(0, round(r.forecast35 * sc["factor"]) - r.availableQty)
        r.tierSuggestedProduction = r.suggestedProduction                 # back-compat
        r.tierPolicy = sc["policy"]
