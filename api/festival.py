"""
India festival / end-of-season-sale demand uplifts for the daily forecast.

Apparel demand in India spikes around festival and sale seasons (EOSS, the
pre-Diwali festive sale, Diwali itself, etc.). The base forecast — LightGBM's
recursive daily prediction, or the seasonal-naive fallback — does not model
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
