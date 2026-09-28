"""
India festival / sale-event demand multipliers for the weekly forecast.

Event DATES come from the same CSV calendar as the dashboard's festival
outlook (festival_calendar.py, Indian_Festivals_Ecommerce_Sales_2025_2028.csv),
plus two derived windows the sheet doesn't list (see _derived_windows()).
Each event TYPE's MULTIPLIER is measured from real sales, not hand-set
(2026-09-28, user-requested). The previous hand-curated calendar had Big
Billion Days 2026 in the wrong week, no Navratri / Amazon Great Indian
Festival window at all, and a 1.8x Diwali peak - while real 2025 sales
DROPPED to ~0.4-0.7x of normal on the Diwali days themselves.

``calibrate(daily_units)`` fits the effects: for every day with a
same-weekday "normal" baseline (median of the nearest non-event days of the
same weekday), log(units / baseline) = sum of the effects of every event
active that day, solved as a ridge regression. Fitting jointly splits the
lift of overlapping events apart (2025's Navratri, Big Billion Days, Myntra
BFF and Amazon GIF all ran together; in 2026 they don't), and the ridge
penalty keeps events seen on only a day or two close to "no effect".
data.rebuild() recalibrates on every refresh, so this year's festive days
update the effects as they happen. Until then _DEFAULT_EFFECTS (the same
fit on data up to 2026-09-26) is used.

``festival_factor(d)`` returns ``(multiplier, name)``: the product of the
effects of every event active on ``d`` (clamped to [0.5, 3.0]), named after
the event with the strongest effect; ``1.0, None`` when nothing with a real
effect (beyond +-7%) is active. Multipliers below 1 are real: Diwali and
Ganesh Chaturthi are demand DIPS for this catalog.
"""
from __future__ import annotations

import math
import sys
import threading
from datetime import date, timedelta

import festival_calendar

# Effects within this band of 1.0 are treated as "no event" (noise).
_NOISE = 0.07
_MIN_MULT, _MAX_MULT = 0.5, 3.0
_RIDGE_LAMBDA = 5.0

# Fitted 2026-09-28 on real gross sales 2025-03-18..2026-09-26 (see module
# docstring); replaced at runtime by calibrate(). Unlisted event types = 1.0.
_DEFAULT_EFFECTS: dict[str, float] = {
    "Pre-Rakhi shopping": 1.50,
    "Amazon Great Indian Festival (Amazon)": 1.30,
    "Myntra Birthday Blast (Myntra)": 1.29,
    "Meesho Mega Blockbuster Sale (Meesho)": 1.24,
    "Shopify Merchant Independence Sale Window (Shopify)": 1.22,
    "Navratri (Festival)": 1.22,
    "Shopify Merchant Festive/Diwali Sale Window (Shopify)": 1.18,
    "Myntra End of Reason Sale - Winter (Myntra)": 1.14,
    "Meesho Independence Sale (Meesho)": 1.12,
    "Myntra Big Fashion Festival (Myntra)": 1.11,
    "Amazon Independence/Freedom Sale (Amazon)": 1.11,
    "Flipkart Freedom Sale (Flipkart)": 1.11,
    "Flipkart Big Billion Days (Flipkart)": 1.08,
    "Amazon Prime Day (Amazon)": 1.06,
    "Onam (Festival)": 1.06,
    "Janmashtami (Festival)": 1.06,
    "Myntra End of Reason Sale - Summer (Myntra)": 0.92,
    "Flipkart Diwali Sale (Flipkart)": 0.91,
    "Meesho Diwali Sale (Meesho)": 0.91,
    "Meesho Christmas Sale (Meesho)": 0.94,
    "Bakrid / Eid al-Adha (Festival)": 0.94,
    "Ganesh Chaturthi (Festival)": 0.86,
    "Chhath Puja (Festival)": 0.85,
    "Diwali dip": 0.84,
}

_LOCK = threading.RLock()
_EFFECTS: dict[str, float] = dict(_DEFAULT_EFFECTS)
_DAY_CACHE: dict[date, tuple[float, str | None]] = {}


def _derived_windows() -> list[tuple[str, date, date]]:
    """Windows around CSV events that the sheet doesn't list itself:
    - "Diwali dip": sales fall from 2 days BEFORE the listed Diwali start
      (delivery cut-off - real 2025 data: 3,481 units on 10-18 vs ~4,500
      earlier that week, 1,690 on Diwali day 10-20) through its end.
    - "Pre-Rakhi shopping": the gifting ramp peaks ~7 days before Raksha
      Bandhan, not on the day (real 2025 data: +21% to +58% vs the normal
      same weekday on 07-31..08-03, back to normal on 08-09)."""
    out = []
    for name, start, end, _cat in festival_calendar.all_events():
        if name == "Diwali (Festival)":
            out.append(("Diwali dip", start - timedelta(days=2), end))
        elif name == "Raksha Bandhan (Festival)":
            out.append(("Pre-Rakhi shopping", start - timedelta(days=10), start - timedelta(days=4)))
    return out


_WINDOWS: list[tuple[str, date, date]] | None = None


def _windows() -> list[tuple[str, date, date]]:
    """Every (event type, start, end) that can carry an effect (the CSV is
    read once). The CSV's "Diwali (Festival)" row is represented by the
    derived "Diwali dip"."""
    global _WINDOWS
    if _WINDOWS is None:
        csv_rows = [(n, s, e) for n, s, e, _c in festival_calendar.all_events() if n != "Diwali (Festival)"]
        _WINDOWS = csv_rows + _derived_windows()
    return _WINDOWS


def calibrate(daily_units) -> None:
    """Re-fit every event type's multiplier from real daily sales.

    ``daily_units``: pandas Series of total units per calendar day
    (DatetimeIndex). Keeps the current effects if there isn't enough data
    to fit (e.g. a CSV-only run with no recent history)."""
    import numpy as np
    import pandas as pd

    global _EFFECTS
    try:
        daily = daily_units.sort_index().asfreq("D", fill_value=0.0)
        # The newest day is usually still syncing - never fit on it.
        daily = daily.iloc[:-1]
        windows = _windows()
        quiet = pd.Series(True, index=daily.index)
        for _n, s, e in windows:
            quiet[(quiet.index >= pd.Timestamp(s)) & (quiet.index <= pd.Timestamp(e))] = False

        values, is_quiet = daily.to_dict(), quiet.to_dict()
        days, ratios = [], []
        for d in daily.index:
            base = []
            for k in range(1, 27):
                p = d - pd.Timedelta(weeks=k)
                if is_quiet.get(p):
                    base.append(values[p])
                    if len(base) == 6:
                        break
            if len(base) >= 4 and np.median(base) > 0:
                days.append(d)
                ratios.append(values[d] / np.median(base))
        if len(days) < 60:
            print(f"[festival] only {len(days)} days with a baseline - keeping current effects", file=sys.stderr)
            return

        types = sorted({n for n, *_ in windows})
        col = {t: i for i, t in enumerate(types)}
        row = {d: i for i, d in enumerate(days)}
        X = np.zeros((len(days), len(types)))
        for n, s, e in windows:
            for d in pd.date_range(s, e):
                if d in row:
                    X[row[d], col[n]] = 1.0
        y = np.log(np.clip(np.array(ratios), 0.2, 5.0))
        beta = np.linalg.solve(X.T @ X + _RIDGE_LAMBDA * np.eye(len(types)), X.T @ y)
        seen = X.sum(axis=0) > 0
        effects = {t: round(float(np.exp(b)), 3) for t, b, ok in zip(types, beta, seen) if ok}
        with _LOCK:
            _EFFECTS = effects
            _DAY_CACHE.clear()
        strongest = sorted(effects.items(), key=lambda kv: -abs(math.log(kv[1])))[:6]
        print(f"[festival] calibrated {len(effects)} event effects on {len(days)} days; strongest: "
              + ", ".join(f"{n.split(' (')[0]} {m:.2f}" for n, m in strongest), file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — keep the current effects rather than break a rebuild
        print(f"[festival] calibration failed ({exc!r}); keeping current effects", file=sys.stderr)


def effects() -> dict[str, float]:
    """Current {event type: multiplier} (a copy)."""
    with _LOCK:
        return dict(_EFFECTS)


def festival_factor(d: date) -> tuple[float, str | None]:
    """Demand multiplier and dominant event name for ``d`` (``1.0, None`` if
    no event with a real effect is active)."""
    with _LOCK:
        cached = _DAY_CACHE.get(d)
        if cached is not None:
            return cached
        eff = _EFFECTS
    log_mult, best_name, best_strength = 0.0, None, 0.0
    for name, start, end in _windows():
        if start <= d <= end:
            m = eff.get(name, 1.0)
            if abs(m - 1.0) <= _NOISE:
                continue
            log_mult += math.log(m)
            if abs(math.log(m)) > best_strength:
                best_name, best_strength = name, abs(math.log(m))
    mult = min(_MAX_MULT, max(_MIN_MULT, math.exp(log_mult)))
    result = (mult, best_name) if best_name else (1.0, None)
    with _LOCK:
        _DAY_CACHE[d] = result
    return result


def _uplift_windows() -> list[tuple[str, date, date, float]]:
    """(name, start, end, multiplier) for every event with a real UPLIFT."""
    eff = effects()
    return [(n, s, e, eff[n]) for n, s, e in _windows() if eff.get(n, 1.0) > 1.0 + _NOISE]


def _nearest_peak_distance(d: date, max_days: int = 999) -> int:
    """Signed days from ``d`` to the closest uplift event's midpoint. Capped
    at ``max_days`` when nothing is nearby, so it stays a bounded,
    well-scaled model feature rather than an unbounded one."""
    best = max_days
    for _name, start, end, _m in _uplift_windows():
        dist = ((start + (end - start) / 2) - d).days
        if abs(dist) < abs(best):
            best = dist
    return best


def windows_between(start: date, end: date) -> list[tuple[str, date, date, float]]:
    """Every uplift event (name, start, end, multiplier) overlapping
    ``[start, end]`` (inclusive)."""
    return [w for w in _uplift_windows() if w[1] <= end and w[2] >= start]


def week_signal(week_start: date) -> tuple[float, str | None, int]:
    """Festival signal for use as a model INPUT feature (as opposed to
    ``festival_factor``'s post-hoc multiplicative use on a final prediction).

    Returns (avg_multiplier, dominant_event_or_None, days_to_nearest_peak) for
    the 7-day week starting ``week_start`` — the same weekly-averaging
    convention already used for the post-hoc uplift, so a model trained on
    this feature sees the identical signal the outer blend applies."""
    total = 0.0
    counts: dict[str, int] = {}
    for i in range(7):
        mult, event = festival_factor(week_start + timedelta(days=i))
        total += mult
        if event:
            counts[event] = counts.get(event, 0) + 1
    dominant = max(counts, key=counts.get) if counts else None
    return total / 7, dominant, _nearest_peak_distance(week_start)
