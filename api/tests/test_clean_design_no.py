"""Tests for live_source.clean_design_no (style codes with extra suffixes).

Run: python -m pytest api/tests/test_clean_design_no.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import pandas as pd

import live_source


def test_clean_design_no_drops_extra_suffixes_only():
    raw = pd.Series(["050-02-XS-A", "319-01-SHIRT", "224-01_B", "K-100-01", "K-1003", "417-03", "6302", "k-7504", None],
                    dtype=object)
    assert list(live_source.clean_design_no(raw)) == [
        "050-02", "319-01", "224-01", "K-100-01", "K-1003", "417-03", "6302", "k-7504", None]
