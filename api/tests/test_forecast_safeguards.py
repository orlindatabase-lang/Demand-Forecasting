"""Regression tests for the 2026-10-19 forecast fixes and data checks:
week-by-date matching, the published blend, no after-the-week model inputs,
no hard-coded ERP token, and the data-health warnings.

Run: python -m pytest api/tests/test_forecast_safeguards.py -v
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

API = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API))   # the api/ modules
os.environ["DEFER_INITIAL_LOAD"] = "1"   # import data.py without loading live data
os.environ.setdefault("DAILY_REFRESH", "off")

import pytest

import data


class _Monday(date):
    """date.today() = Monday 19 Oct 2026 (data through Sunday 18 Oct)."""
    @classmethod
    def today(cls):
        return cls(2026, 10, 19)


@pytest.fixture
def monday(monkeypatch):
    monkeypatch.setattr(data, "date", _Monday)
    monkeypatch.setattr(data, "SNAPSHOT_DATE", date(2026, 10, 18))
    monkeypatch.setattr(data, "_week_festival", lambda ws: (1.0, None))


def test_weekly_forecast_is_matched_to_its_week_by_date(monday):
    # series[w] = forecast of the week starting _forecast_week_start(snapshot, w):
    # 19 Oct, 26 Oct, 2 Nov, 9 Nov. On a Monday the 19 Oct week is the running
    # (historical) week; 26 Oct must get 200, not 100 (the old one-week shift).
    forecast, historical = data._build_week_points(3, 10.0, {}, [100.0, 200.0, 300.0, 400.0], {})
    assert [(p.date, p.qty) for p in forecast[:2]] == [("2026-10-26", 200), ("2026-11-02", 300)]
    # ...and the running week gets the model's forecast, not the run-rate (10).
    assert historical[-1].date == "2026-10-19" and historical[-1].forecast == 100


def test_published_blend_puts_festival_on_the_run_rate_only(monkeypatch):
    monkeypatch.setattr(data, "FORECAST_BLEND", 0.5)
    monkeypatch.setattr(data, "FESTIVAL_ON", "runrate")
    assert data._blend(100.0, 40.0, 1.5) == pytest.approx(0.5 * 100 + 0.5 * 40 * 1.5)
    assert data._blend(None, 40.0, 1.5) == pytest.approx(60.0)       # no model: run-rate x festival
    monkeypatch.setattr(data, "FESTIVAL_ON", "both")                 # the old formula, still selectable
    assert data._blend(100.0, 40.0, 1.5) == pytest.approx((50 + 20) * 1.5)


def test_models_have_no_inputs_known_only_after_the_week():
    src = (API / "lgbm_forecast.py").read_text(encoding="utf-8")
    blocks = re.findall(r"feature_cols = \((.*?)\n\s*\+ festival_cols\)", src, re.S)
    assert len(blocks) == 2, "expected the SKU and the style model's feature lists"
    for b in blocks:
        assert "promo_discount" not in b and "promotion_flag" not in b


def test_erp_token_is_not_hard_coded():
    src = (API / "live_source.py").read_text(encoding="utf-8")
    assert 'os.getenv("ERP_TOKEN", "")' in src


# --- data health ------------------------------------------------------------------
@pytest.fixture
def health(monkeypatch):
    """A quiet, complete load: 91 normal days of 1,000 units, data through yesterday."""
    snap = date.today() - timedelta(days=1)
    days = {(snap - timedelta(days=i)).isoformat(): 1000 for i in range(120)}
    monkeypatch.setattr(data, "SNAPSHOT_DATE", snap)
    monkeypatch.setattr(data, "_DAY_ACTUAL_GROSS", {"SKU-1": days})
    monkeypatch.setattr(data, "PLAN_ROWS", [])
    monkeypatch.setattr(data, "_LOADED_FROM", "live")
    monkeypatch.setattr(data, "_HEALTH_CACHE", None)
    monkeypatch.setattr(data, "_DATA_VERSION", -1)
    monkeypatch.setattr(data.festival_calendar, "events_overlapping", lambda a, b: [])
    monkeypatch.setattr(data.festival, "festival_factor", lambda d: (1.0, None))
    import live_source
    import weekly_log
    monkeypatch.setattr(live_source, "LAST_LOAD", {"sales_rows": 100, "sales_rows_expected": 100,
                                                   "master_ok": True, "wip_ok": True, "stock_ok": True})
    monkeypatch.setattr(weekly_log, "read", lambda style="", status="": [])
    return days, snap, live_source


def _codes():
    return {i["code"] for i in data.data_health()["issues"]}


def test_clean_load_has_no_issues(health):
    assert _codes() == set()


def test_failed_erp_stock_is_an_error(health):
    _days, _snap, live_source = health
    live_source.LAST_LOAD["stock_ok"] = False
    issues = data.data_health()["issues"]
    assert issues[0]["code"] == "erp_stock_missing" and issues[0]["level"] == "error"


def test_incomplete_sales_download_is_an_error(health):
    _days, _snap, live_source = health
    live_source.LAST_LOAD["sales_rows"] = 90
    assert "sales_incomplete" in _codes()


def test_sale_spike_in_run_rate_and_missing_calendar_event_are_flagged(health):
    days, snap, _ls = health
    for i in (3, 4, 5):                                    # a 3-day sale, not in the calendar
        days[(snap - timedelta(days=i)).isoformat()] = 4000
    assert {"runrate_spike", "calendar_missing_event"} <= _codes()


def test_calendar_sale_with_normal_sales_is_flagged(health, monkeypatch):
    monkeypatch.setattr(data.festival, "festival_factor", lambda d: (1.5, "Some Sale"))
    assert "calendar_event_not_seen" in _codes()


def test_stale_data_is_flagged(health, monkeypatch):
    monkeypatch.setattr(data, "SNAPSHOT_DATE", date.today() - timedelta(days=5))
    assert "data_stale" in _codes()


def test_robust_runrate_drops_an_unlisted_catalogue_spike(monkeypatch):
    """A flash-sale day cannot lift every SKU's normal weekly baseline."""
    import pandas as pd

    start = pd.Timestamp("2026-01-01")
    days = pd.date_range(start, periods=21, freq="D")
    qty = [10.0] * len(days)
    qty[-1] = 100.0
    sales = pd.DataFrame({
        "order_date": days,
        "product_sku_code": ["A"] * len(days),
        "qty": qty,
    })
    monkeypatch.setattr(data, "RUNRATE_DAYS", 21)
    monkeypatch.setattr(data, "RUNRATE_MIN_CLEAN_DAYS", 14)
    monkeypatch.setattr(data.festival_calendar, "is_event_day", lambda _d: False)

    weekly, excluded = data._robust_runrate(sales, days[-1])

    assert weekly["A"] == pytest.approx(70.0)
    assert days[-1].date().isoformat() in excluded
