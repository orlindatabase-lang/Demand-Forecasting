"""Tests for the "N SKUs" drill-down (/api/weekly-sales/skus/{style}):
data._split_by_share (how a Style's forecast is split over its SKUs) and
data._sku_key (one SKU spelled two ways or relisted counts once).

Run: python -m pytest api/tests/test_style_skus.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DEFER_INITIAL_LOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import data  # noqa: E402


def test_frozen_by_month_sums_frozen_weeks_from_the_running_month_on():
    today = date.today()
    this_month = today.strftime("%Y-%m")
    last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    cell = lambda q: SimpleNamespace(frozenForecast=q)  # noqa: E731
    row = SimpleNamespace(cells={
        f"{last_month}-24": cell(50),      # before the running month
        f"{this_month}-01": cell(10),
        f"{this_month}-08": cell(20),
        f"{this_month}-16": cell(None),    # not frozen yet
    })
    assert data._frozen_by_month(row) == {this_month: 30}
    assert data._frozen_by_month(None) == {}


def test_split_adds_back_up_to_the_style_forecast():
    parts = data._split_by_share(100, [1, 1, 1])
    assert sum(parts) == 100 and sorted(parts) == [33, 33, 34]


def test_split_follows_the_sale_share():
    assert data._split_by_share(60, [10, 20, 30]) == [10, 20, 30]


def test_no_sale_splits_equally_and_no_forecast_gives_zeros():
    assert data._split_by_share(4, [0, 0]) == [2, 2]
    assert data._split_by_share(0, [5, 5]) == [0, 0]


def test_dash_and_underscore_spellings_are_the_same_sku():
    assert data._sku_key("474-03_M") == data._sku_key("474-03-M") == "474-03-M"
    assert data._sku_key(" 474-03 xl ") == "474-03-XL"
    assert data._sku_key("474-03-M") != data._sku_key("474-03-XL")


def test_a_letter_after_the_size_is_a_relisting_of_the_same_sku():
    assert data._sku_key("005-03-XL-A") == data._sku_key("005-03-XL") == "005-03-XL"
    assert data._sku_key("474-05-XS_B") == "474-05-XS"
    # not a size + one letter: a different variant, kept apart
    assert data._sku_key("442-01-BB-M") == "442-01-BB-M"
    assert data._sku_key("404-01-Without Dupatta-L") == "404-01-WITHOUT-DUPATTA-L"


def test_another_design_number_before_the_size_is_the_same_sku():
    assert data._sku_key("423-03-427-03-L", "423-03") == data._sku_key("423-03-L", "423-03") == "423-03-L"
    assert data._sku_key("423-03-427-03-XL-A", "423-03") == "423-03-XL"
    # without the Style, or when no size follows, the code is left as it is
    assert data._sku_key("423-03-427-03-L") == "423-03-427-03-L"
    assert data._sku_key("K-7502_13-14 Years", "K-7502") == "K-7502-13-14-YEARS"


def test_2xl_is_xxl_and_3xl_is_xxxl():
    assert data._sku_key("001-04-2XL") == data._sku_key("001-04-XXL") == "001-04-XXL"
    assert data._sku_key("001-04-2XL-A") == "001-04-XXL"
    assert data._sku_key("001-04-3XL") == data._sku_key("001-04_XXXL") == "001-04-XXXL"
