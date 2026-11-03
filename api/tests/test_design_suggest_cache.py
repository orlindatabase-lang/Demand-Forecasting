"""Tests for design_suggest's saved result (api/.cache/design_suggestions.json,
uploaded to the bucket with the rest of the cache): reused only for the data
it was built from.

Run: python -m pytest api/tests/test_design_suggest_cache.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("DEFER_INITIAL_LOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import design_suggest  # noqa: E402


def test_saved_result_is_reused_only_for_the_same_data(tmp_path, monkeypatch):
    monkeypatch.setattr(design_suggest, "_RESULT_FILE", tmp_path / "design_suggestions.json")
    built = NS(SNAPSHOT_DATE=date(2026, 11, 2), FORECAST_SNAPSHOT="2026-11-02")
    value = {"available": True, "seasons": [{"code": "WIN"}]}
    design_suggest._save_result(built, value)

    assert design_suggest._load_result(built) == value
    assert design_suggest._load_result(NS(SNAPSHOT_DATE=date(2026, 11, 3), FORECAST_SNAPSHOT="2026-11-02")) is None
    assert design_suggest._load_result(NS(SNAPSHOT_DATE=date(2026, 11, 2), FORECAST_SNAPSHOT="2026-11-01")) is None


def test_an_unavailable_result_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(design_suggest, "_RESULT_FILE", tmp_path / "design_suggestions.json")
    design_suggest._save_result(NS(SNAPSHOT_DATE=date(2026, 11, 2), FORECAST_SNAPSHOT=""), {"available": False})
    assert not (tmp_path / "design_suggestions.json").exists()
