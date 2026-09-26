"""
Real Indian festival + e-commerce sale calendar (2025-2028), from
Indian_Festivals_Ecommerce_Sales_2025_2028.csv (repo root) - 2026-09-13,
user-requested replacement for festival.py's hand-curated/partly-estimated
windows as the source for the Weekly Sales Report's "Upcoming Festival" /
"Upcoming Sale" outlook card (and the festival-aware production boost that
reads from it, see data.py's _event_boost_windows()).

Deliberately NOT wired into festival.py's own per-day demand MULTIPLIER
system (festival_factor()/week_signal(), used as an actual feature/uplift by
the trained forecasting model) - that system's peak multipliers were
individually verified against real order data (see festival.py's own
comments), and swapping its date source would mean re-verifying every one of
them against this sheet instead. This module only answers "what's the next
festival/sale, and when did it last happen" for display + the optimistic
production-boost heuristic, not "how much should today's demand be
multiplied by".

Each CSV row is (name, start, end); the name always ends in a parenthetical
tag - "(Festival)" for a religious/cultural event, or the platform name
("(Amazon)", "(Flipkart)", "(Myntra)", "(Meesho)", "(Shopify)") for a
promotional sale - which is exactly the "festival" vs "sale" split
data.get_festival_outlook() needs.
"""
from __future__ import annotations

import csv
import sys
import threading
from datetime import date, datetime
from pathlib import Path

_CSV_PATH = Path(__file__).resolve().parent.parent / "Indian_Festivals_Ecommerce_Sales_2025_2028.csv"
_NAME_COL = "Festival_Name/Sale_Name(platform)"

_LOCK = threading.RLock()
_EVENTS: list[tuple[str, date, date, str]] = []  # (name, start, end, category), sorted by start
_LOADED = False


def _load() -> None:
    global _EVENTS, _LOADED
    if not _CSV_PATH.exists():
        print(f"[festival_calendar] {_CSV_PATH} not found - skipping", file=sys.stderr)
        with _LOCK:
            _LOADED = True
        return
    events: list[tuple[str, date, date, str]] = []
    try:
        with _CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get(_NAME_COL) or "").strip()
                start_raw = (row.get("Start_date") or "").strip()
                end_raw = (row.get("End_date") or "").strip()
                if not name or not start_raw or not end_raw:
                    continue
                start = datetime.strptime(start_raw, "%d-%m-%Y").date()
                end = datetime.strptime(end_raw, "%d-%m-%Y").date()
                category = "festival" if name.endswith("(Festival)") else "sale"
                events.append((name, start, end, category))
    except Exception as exc:  # noqa: BLE001
        print(f"[festival_calendar] failed to read {_CSV_PATH}: {exc!r}", file=sys.stderr)
        with _LOCK:
            _LOADED = True
        return

    events.sort(key=lambda e: e[1])
    with _LOCK:
        _EVENTS = events
        _LOADED = True
    print(f"[festival_calendar] loaded {len(events)} events from {_CSV_PATH.name} "
          f"({sum(1 for e in events if e[3] == 'festival')} festival, "
          f"{sum(1 for e in events if e[3] == 'sale')} sale)", file=sys.stderr)


def _ensure_loaded() -> None:
    if not _LOADED:
        with _LOCK:
            if not _LOADED:
                _load()


def upcoming(category: str, as_of: date) -> tuple[str, date, date] | None:
    """The earliest-starting event of ``category`` ("festival" or "sale")
    whose END hasn't passed as of ``as_of``. A currently-in-progress event
    counts as "upcoming" too - matches how a user reads it on the day of.
    ``None`` if the sheet has no matching event (shouldn't normally happen
    inside 2025-2028) or failed to load."""
    _ensure_loaded()
    with _LOCK:
        events = _EVENTS
    candidates = [e for e in events if e[3] == category and e[2] >= as_of]
    if not candidates:
        return None
    name, start, end, _cat = candidates[0]  # `events` is pre-sorted by start
    return name, start, end


def overlapping_upcoming(category: str, as_of: date) -> list[tuple[str, date, date]]:
    """Every upcoming/in-progress event of ``category`` whose date range
    overlaps the PRIMARY upcoming event's own range (see ``upcoming()``) -
    i.e. genuinely concurrent events, not just "also happens sometime in the
    future" (2026-09-15, user-requested: "what if there are multiple sales
    at once in the same date range" - ``upcoming()`` alone silently picks
    only the earliest-starting one and ignores any other sale/festival
    actually running at the same time). Always includes the primary event
    itself, sorted by start date. ``[]`` if there's no upcoming event of
    this category at all."""
    _ensure_loaded()
    with _LOCK:
        events = _EVENTS
    primary = upcoming(category, as_of)
    if primary is None:
        return []
    _p_name, p_start, p_end = primary
    group = [
        (n, s, e) for n, s, e, c in events
        if c == category and s <= p_end and e >= p_start
    ]
    group.sort(key=lambda t: t[1])
    return group


def most_recent_past(name: str, before: date) -> tuple[date, date] | None:
    """The most recent (start, end) occurrence of the EXACT SAME event name
    that ended before ``before`` - for comparing against real historical
    sales during that same event. ``None`` if the sheet has no earlier
    occurrence of this name."""
    _ensure_loaded()
    with _LOCK:
        events = _EVENTS
    past = [(s, e) for n, s, e, _c in events if n == name and e < before]
    if not past:
        return None
    return max(past, key=lambda p: p[0])


def events_overlapping(start: date, end: date) -> list[tuple[str, date, date, str]]:
    """Every event (any category) whose [start, end] range overlaps
    [``start``, ``end``], sorted by its own start date - for highlighting
    which weeks (past or upcoming) of the Weekly Sales Report fall inside a
    real festival/sale window (2026-09-24, user-requested), as distinct from
    upcoming()/overlapping_upcoming() above which only look forward from
    "today" for the outlook card."""
    _ensure_loaded()
    with _LOCK:
        events = _EVENTS
    matches = [(n, s, e, c) for n, s, e, c in events if s <= end and e >= start]
    matches.sort(key=lambda t: t[1])
    return matches


def is_event_day(d: date, exclude_name: str | None = None) -> bool:
    """True if ``d`` falls inside ANY event's [start, end] range (any
    category), other than ``exclude_name`` if given. Indian festivals/sales
    cluster densely, especially Aug-Oct, so a "normal, no-event" reference
    day for one event can easily land inside a DIFFERENT event's own real
    spike (e.g. Raksha Bandhan landing inside the pre-Ganesh-Chaturthi
    baseline window) - used to keep spike-lead-time detection's baseline
    genuinely quiet (see data._detect_spike_lead_days())."""
    _ensure_loaded()
    with _LOCK:
        events = _EVENTS
    return any(s <= d <= e for n, s, e, _c in events if n != exclude_name)
