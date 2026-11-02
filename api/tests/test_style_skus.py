"""Tests for data._split_by_share: how a Style's forecast is split over its
SKUs on the "N SKUs" popup (/api/weekly-sales/skus/{style}).

Run: python -m pytest api/tests/test_style_skus.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DEFER_INITIAL_LOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import data  # noqa: E402


def test_split_adds_back_up_to_the_style_forecast():
    parts = data._split_by_share(100, [1, 1, 1])
    assert sum(parts) == 100 and sorted(parts) == [33, 33, 34]


def test_split_follows_the_sale_share():
    assert data._split_by_share(60, [10, 20, 30]) == [10, 20, 30]


def test_no_sale_splits_equally_and_no_forecast_gives_zeros():
    assert data._split_by_share(4, [0, 0]) == [2, 2]
    assert data._split_by_share(0, [5, 5]) == [0, 0]
