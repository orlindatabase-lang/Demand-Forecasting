"""
India festival / end-of-season-sale demand uplifts for the weekly forecast.

Apparel demand in India spikes around festival and sale seasons (EOSS, the
pre-Diwali festive sale, Diwali itself, etc.). The base forecast — LightGBM's
recursive weekly prediction, or the seasonal-naive fallback — does not model
these calendar events, so we layer a multiplicative uplift on top of it.

``festival_factor(d)`` returns ``(multiplier, name)`` for a date: ``1.0, None``
on ordinary days, and a >1 multiplier (with the event name) inside a festival
window. The uplift ramps smoothly to a peak at the middle of the window rather
than stepping up rectangularly, so the resulting spike looks organic.

Movable (lunar) festivals — Diwali, Holi — must be pinned per year; fixed sale
seasons recur on the same month/day each year. Extend ``_DIWALI`` / ``_HOLI`` and
``_fixed_windows`` as new years are needed.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

# Movable (lunar) festival dates, pinned per year (approximate to within a day).
_DIWALI = {2025: date(2025, 10, 20), 2026: date(2026, 11, 8), 2027: date(2027, 10, 29)}
_HOLI = {2025: date(2025, 3, 14), 2026: date(2026, 3, 3), 2027: date(2027, 3, 22)}
# Flipkart Big Billion Days / Amazon Great Indian Festival -- the major
# e-commerce flash-sale event, timed just before Navratri/Dussehra each year.
# Verified 2026-08-19 against this dataset's real 2025 order history:
# 2025-09-23 was the single highest-volume day of the entire year (7,929
# units vs an already-elevated ~2,900-3,100/day pre-event baseline, ~2.7x on
# top of trend) -- the sharpest, clearest spike of any kind in the data.
# 2026/2027 are UNVERIFIED ESTIMATES (these events haven't happened yet as of
# 2026-08-19, so there's no historical data to check them against) --
# extrapolated at ~29 days before Diwali, the gap observed in 2025.
# Flipkart/Amazon announce the exact dates only ~3-4 weeks ahead each year;
# correct these once each year's real dates are known, and re-verify the
# 2.3 peak multiplier below against real 2026 data once it exists (see
# api/backtest_sweep.py for how to check a specific window's real accuracy).
_BIG_BILLION_DAY = {2025: date(2025, 9, 23), 2026: date(2026, 10, 10), 2027: date(2027, 9, 30)}
# Raksha Bandhan (Rakhi). Verified 2026-08-19 against real 2025 order data,
# WITH day-of-week normalization (raw same-day comparison misses this --
# weekends are naturally higher, which masked the signal on a first pass):
# 2025-07-31 through 2025-08-03 ran +21% to +58% above the typical day of
# that same weekday, peaking 2025-08-02 (a Saturday, +58% vs the normal
# Saturday level) -- then reverted to completely normal right at the actual
# festival day (2025-08-09, ratio 0.98) and the day after (0.94). A real,
# multi-day pre-festival gifting ramp-up, not noise.
# 2026/2027 are UNVERIFIED ESTIMATES -- re-verify once each year's data exists.
_RAKSHA_BANDHAN = {2025: date(2025, 8, 9), 2026: date(2026, 8, 28), 2027: date(2027, 8, 17)}
# Checked against real 2025 order data but found NO measurable demand spike
# for this catalog: Eid-ul-Fitr, Eid-ul-Adha (no signal near the actual
# dates). Ganesh Chaturthi is actively a DIP, not a spike (ratios 0.67-0.99
# for two weeks around it) -- adding a positive multiplier there would hurt,
# not help. Karva Chauth shows real elevation but it's indistinguishable
# from the already-modeled Festive Sale + Diwali plateau (Sep30-Oct12 is one
# continuous elevated stretch, not a separable Karva Chauth-specific signal)
# -- not worth a separate overlapping entry.


def _fixed_windows(y: int) -> list[tuple[str, date, date, float]]:
    """Recurring apparel sale seasons (same month/day every year). Each entry is
    (name, start, end, peak_multiplier)."""
    return [
        ("Winter EOSS", date(y, 1, 1), date(y, 1, 18), 1.35),
        ("Republic Day Sale", date(y, 1, 23), date(y, 1, 26), 1.25),
        ("Summer EOSS", date(y, 6, 25), date(y, 7, 20), 1.40),
        ("Independence Day Sale", date(y, 8, 8), date(y, 8, 15), 1.30),
        ("Onam Sale", date(y, 8, 28), date(y, 9, 6), 1.30),
        ("Festive Sale", date(y, 9, 23), date(y, 10, 6), 1.60),
        ("Year-End Sale", date(y, 12, 24), date(y, 12, 31), 1.30),
    ]


@lru_cache(maxsize=8)
def _windows_for_year(y: int) -> tuple[tuple[str, date, date, float], ...]:
    windows = list(_fixed_windows(y))
    if (h := _HOLI.get(y)) is not None:
        windows.append(("Holi Sale", h - timedelta(days=3), h + timedelta(days=1), 1.20))
    if (rb := _RAKSHA_BANDHAN.get(y)) is not None:
        # Real 2025 data shows the shopping peak ~7 days BEFORE the actual
        # Rakhi day (2025-08-02, vs the festival itself on 2025-08-09), not
        # on the day itself -- the window is centered on that peak, not `rb`.
        peak_day = rb - timedelta(days=7)
        windows.append(("Raksha Bandhan", peak_day - timedelta(days=3), peak_day + timedelta(days=3), 1.35))
    if (bbd := _BIG_BILLION_DAY.get(y)) is not None:
        # A short, sharply-peaked window centered exactly on the verified
        # spike date -- layered ON TOP of "Festive Sale" above (which covers
        # the broader Sep23-Oct6 season more gently); festival_factor() picks
        # whichever window gives the larger multiplier on any given day, so
        # this one dominates right at the event and "Festive Sale" continues
        # to carry the surrounding weeks.
        windows.append(("Big Billion Day / Great Indian Festival",
                         bbd - timedelta(days=3), bbd + timedelta(days=3), 2.3))
    if (dv := _DIWALI.get(y)) is not None:
        # Diwali is the peak apparel-buying window of the year.
        windows.append(("Diwali", dv - timedelta(days=9), dv + timedelta(days=2), 1.80))
    return tuple(windows)


def _ramped(d: date, start: date, end: date, peak: float) -> float:
    """Triangular ramp: 1.0 at the window edges, ``peak`` at its midpoint."""
    span = (end - start).days or 1
    pos = (d - start).days
    frac = 1.0 - abs(pos - span / 2) / (span / 2)  # 1 at centre, 0 at edges
    return 1.0 + (peak - 1.0) * max(0.0, frac)


def festival_factor(d: date) -> tuple[float, str | None]:
    """Demand multiplier and event name for ``d`` (``1.0, None`` if no event).

    If overlapping windows apply, the one giving the largest ramped multiplier on
    that day wins."""
    best_mult, best_name = 1.0, None
    for name, start, end, peak in _windows_for_year(d.year):
        if start <= d <= end:
            mult = _ramped(d, start, end, peak)
            if mult > best_mult:
                best_mult, best_name = mult, name
    return best_mult, best_name


def _nearest_peak_distance(d: date, max_days: int = 999) -> int:
    """Signed days from ``d`` to the closest festival window's peak (midpoint),
    across the adjacent years too (a late-Dec/early-Jan week can be closer to
    next/last year's window than anything in its own year). Capped at
    ``max_days`` when nothing is nearby, so it stays a bounded, well-scaled
    model feature rather than an unbounded one."""
    best = max_days
    for y in (d.year - 1, d.year, d.year + 1):
        for _name, start, end, _peak in _windows_for_year(y):
            mid = start + (end - start) / 2
            dist = (mid - d).days
            if abs(dist) < abs(best):
                best = dist
    return best


def windows_between(start: date, end: date) -> list[tuple[str, date, date, float]]:
    """Every named festival/sale window overlapping ``[start, end]`` (inclusive),
    across every year touched by the range (a range spanning New Year's needs
    both years' windows)."""
    out: list[tuple[str, date, date, float]] = []
    for y in range(start.year, end.year + 1):
        for name, w_start, w_end, peak in _windows_for_year(y):
            if w_start <= end and w_end >= start:
                out.append((name, w_start, w_end, peak))
    return out


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
