"""Catalog Style tier + image gallery, sourced from the Cloud SQL (Postgres)
"CatalogStyle" table (2026-09-21, user-requested). Each row's
`forecastStatus` column is an externally-maintained demand tier label
(T0/T1/T2/T3/T11/T12 as found in the live data, or null = not yet
classified) - a SEPARATE thing from lifecycle.py's own launch/current
velocity tiers, entered/maintained by the catalog team in this other
system, not computed by this app. `approvedImage`/`launchImage` are public
GCS URLs (storage.googleapis.com/orlin-admin-assets/...) - no auth needed
to fetch/display them once we have the URL, only to read the row itself.

Auth: username/password from api/.env (CLOUDSQL_DB_USER/CLOUDSQL_DB_PASSWORD,
git-ignored, never committed) via python-dotenv. IAM database
authentication was tried first (matching the rest of this app's Application
Default Credentials pattern for BigQuery) and rejected by the instance
("password authentication failed") - not enabled/registered on orlin-prod -
so this uses a regular Postgres role instead.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

_INSTANCE = "orlinappareldataset:asia-south1:orlin-prod"
_DB = "orlin-db"

# Catalog images/tiers change rarely (a merchandising decision, not live
# sales data) - an hourly refresh is plenty, same style as similar_design.py's
# own _REFRESH_SECS/_CACHE_MAX_AGE pattern.
_REFRESH_SECS = 3600
_UNCLASSIFIED = "Unclassified"

_LOCK = threading.RLock()
_STYLES: list[dict] = []  # [{name, tier, imageUrl}]
# Point-in-time tier CHANGE history (2026-09-21, user-requested: feed tier
# into the demand model as a feature) - [{name, tier, effectiveFrom: date}],
# sorted by (name, effectiveFrom). CatalogStyleForecastHistory only starts
# 2026-06-01 (when the catalog team's own tracking began) - a design's tier
# before its own first recorded change is genuinely UNKNOWN, not "whatever
# its current tier happens to be" - using today's tier on a training row
# from 2025 would leak the future into the model. Callers (lgbm_forecast.py)
# must treat any week before a design's earliest entry here as unknown, not
# silently fall back to current tier.
_TIER_HISTORY: list[dict] = []
_LAST_FETCH = 0.0
_LOADED = False


def _fetch_from_cloudsql() -> tuple[list[dict], list[dict]]:
    from google.cloud.sql.connector import Connector

    user = os.getenv("CLOUDSQL_DB_USER", "")
    password = os.getenv("CLOUDSQL_DB_PASSWORD", "")
    if not user or not password:
        print(
            "[catalog_style] CLOUDSQL_DB_USER/CLOUDSQL_DB_PASSWORD not set in api/.env - skipping",
            file=sys.stderr,
        )
        return [], []

    connector = Connector()
    try:
        conn = connector.connect(_INSTANCE, "pg8000", user=user, password=password, db=_DB)
    except Exception as exc:  # noqa: BLE001
        print(f"[catalog_style] Cloud SQL connection failed: {exc!r}", file=sys.stderr)
        connector.close()
        return [], []

    styles: list[dict] = []
    history: list[dict] = []
    try:
        cur = conn.cursor()
        # isActive IS NOT FALSE keeps both true and null - only excludes rows
        # explicitly marked inactive/discontinued.
        cur.execute(
            'SELECT name, "forecastStatus", "approvedImage", "launchImage" '
            'FROM public."CatalogStyle" WHERE "isActive" IS NOT FALSE'
        )
        for name, tier, approved_image, launch_image in cur.fetchall():
            if not name:
                continue
            image_url = approved_image or launch_image or ""
            styles.append({"name": str(name).strip(), "tier": (tier or "").strip(), "imageUrl": image_url})

        cur.execute(
            'SELECT s.name, h.status, h."createdAt" '
            'FROM public."CatalogStyleForecastHistory" h '
            'JOIN public."CatalogStyle" s ON s.id = h."catalogStyleId" '
            'ORDER BY s.name, h."createdAt"'
        )
        for name, tier, created_at in cur.fetchall():
            if not name or not tier:
                continue
            history.append({"name": str(name).strip(), "tier": str(tier).strip(), "effectiveFrom": created_at.date()})
    except Exception as exc:  # noqa: BLE001
        print(f"[catalog_style] query failed: {exc!r}", file=sys.stderr)
    finally:
        conn.close()
        connector.close()

    print(
        f"[catalog_style] fetched {len(styles)} active styles and "
        f"{len(history)} tier-history rows from Cloud SQL",
        file=sys.stderr,
    )
    return styles, history


def _ensure_fresh() -> None:
    global _STYLES, _TIER_HISTORY, _LAST_FETCH, _LOADED
    with _LOCK:
        now = time.time()
        if _LOADED and (now - _LAST_FETCH) < _REFRESH_SECS:
            return
        fresh_styles, fresh_history = _fetch_from_cloudsql()
        # Keep serving the last-known-good list on a transient fetch failure
        # rather than blanking the whole gallery out.
        if fresh_styles or not _LOADED:
            _STYLES = fresh_styles
            _TIER_HISTORY = fresh_history
        _LAST_FETCH = now
        _LOADED = True


def get_tier_history() -> list[dict]:
    """[{name, tier, effectiveFrom: date}, ...], sorted by (name,
    effectiveFrom) - the point-in-time tier change log used to build a
    leakage-safe "tier as of week W" training feature (see
    lgbm_forecast.py's _attach_tier_feature())."""
    _ensure_fresh()
    with _LOCK:
        return list(_TIER_HISTORY)


def get_current_tier_map() -> dict[str, str]:
    """{name: tier} for every active style's CURRENT tier ("" if
    unclassified) - used as the tier feature's value for FUTURE forecast
    weeks, where "as of this future week" naturally means "as of today"."""
    _ensure_fresh()
    with _LOCK:
        return {s["name"]: s["tier"] for s in _STYLES}


def _tier_sort_key(tier: str) -> tuple[int, int, str]:
    """Natural numeric order (T0, T1, T2, T3, T11, T12, ...), Unclassified
    last - a plain alphabetical sort would wrongly put T11/T12 before T2/T3."""
    if tier == _UNCLASSIFIED:
        return (1, 0, tier)
    digits = re.sub(r"\D", "", tier)
    return (0, int(digits) if digits else 0, tier)


def get_style_tiers() -> list[dict]:
    """[{tier, count, styles: [{name, tier, imageUrl}, ...]}, ...], sorted in
    natural tier order - powers the tier filter buttons on the Demand
    Forecasting page. A style with no forecastStatus is NOT its own
    "Unclassified" class (2026-09-22, user-requested) - it simply isn't
    tier-classified yet, and stays visible only under the default "All
    Tiers" (unfiltered) view, same as any other style."""
    _ensure_fresh()
    with _LOCK:
        styles = list(_STYLES)
    grouped: dict[str, list[dict]] = {}
    for s in styles:
        if not s["tier"]:
            continue
        grouped.setdefault(s["tier"], []).append(s)
    for label in grouped:
        grouped[label].sort(key=lambda s: s["name"])
    return [
        {"tier": label, "count": len(grouped[label]), "styles": grouped[label]}
        for label in sorted(grouped, key=_tier_sort_key)
    ]
