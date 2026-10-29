"""
FastAPI service for the SKU Production Plan table.

Run via ``run.py`` (from the api/ directory: ``..\\venv\\Scripts\\python.exe
run.py``) — NOT ``uvicorn main:app`` directly. The API auto-starts at login
and self-restarts on crash via a watchdog; you shouldn't need to launch it
by hand. If you do, run.py is still the entry point — it binds the loopback
sockets run.py needs (see its docstring) and this module refuses to start a
second time if something is already answering on the port (see
``_refuse_if_already_running`` below): Windows silently allows two processes
to double-bind the same loopback port, which caused the dashboard to
intermittently show no data when a manually-started ``uvicorn main:app
--reload`` ran alongside the watchdog-managed instance.

Docs:  http://localhost:8000/docs
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
import urllib.request
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

# Load the data in the background (see data.start_background_load) so the
# server opens its port at once instead of after the multi-minute BigQuery +
# ERP load - Cloud Run fails a revision that doesn't listen within its
# startup timeout. Must be set before `import data`.
os.environ.setdefault("DEFER_INITIAL_LOAD", "1")

import catalog_style  # noqa: E402
import data  # noqa: E402
from models import (
    CatalogStyleTiersResponse,
    ChannelSourceWeeklyResponse,
    WeeklyGridResponse,
)


def _refuse_if_already_running(port: int = 8000) -> None:
    """Refuse to start a second copy of this API on the same port.

    Windows' SO_REUSEADDR (used by run.py's dual-socket bind) lets a second
    process silently double-bind 127.0.0.1:<port> without any "address
    already in use" error, so nothing used to stop someone from accidentally
    running ``uvicorn main:app --reload --port 8000`` alongside the
    watchdog-managed run.py. When that happened, incoming requests got
    routed non-deterministically between the two processes and the
    dashboard intermittently showed no data — this is exactly what it took
    a long live-debugging session to track down, so it's worth refusing
    outright rather than repeating that.

    Set ALLOW_DUPLICATE_API=1 to bypass (e.g. deliberately testing a second
    instance on a different port — this check only looks at ``port``).
    """
    if os.getenv("ALLOW_DUPLICATE_API") == "1":
        return
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as resp:
            if resp.status != 200:
                return
    except Exception:
        return  # nothing answering there — safe to proceed
    print(
        f"\n[main] REFUSING TO START: another API instance is already answering "
        f"on port {port}.\n"
        f"  Running a second copy causes the dashboard to intermittently show no "
        f"data (two processes can silently share the port on Windows).\n"
        f"  The API auto-starts at login and self-restarts on crash via a "
        f"watchdog — you shouldn't need to launch it manually. If you do need a "
        f"second instance on purpose, set ALLOW_DUPLICATE_API=1.\n",
        file=sys.stderr,
    )
    sys.stderr.flush()
    # os._exit(), not sys.exit(): when this module is imported via uvicorn's
    # own app-loader (uvicorn main:app, as opposed to a plain `import main`),
    # uvicorn wraps the import in broad exception handling that swallows
    # SystemExit and carries on starting the server anyway — verified this
    # the hard way (sys.exit(1) here worked under a plain `import main` but
    # silently did nothing under `uvicorn main:app`). os._exit() terminates
    # the process immediately at the OS level; nothing can intercept it.
    os._exit(1)


_refuse_if_already_running()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    data.start_background_load()
    yield


app = FastAPI(
    lifespan=_lifespan,
    title="Orlin · SKU Production Plan API",
    version="1.0.0",
    description=(
        "Demand forecasting dashboard API: Weekly Sales Report (forecast vs "
        "actual, production suggestion), channel drill-downs, Sub Category / "
        "Category / Tiers analysis, Production Log and Design Suggestions. "
        "Data: BigQuery sales + ERP views (offline fallback: final_merged_data.csv)."
    ),
)

@app.middleware("http")
async def _wait_for_data(request: Request, call_next):
    """Until the first data load finishes, answer data requests with 503 +
    Retry-After instead of empty tables; the dashboard keeps retrying.
    /health and /docs stay available. Registered BEFORE the CORS middleware
    so CORS wraps it and the 503 still carries the CORS headers the browser
    needs to read it."""
    if not data.DATA_READY and request.url.path.startswith(("/api/", "/admin/")):
        return JSONResponse(
            {"detail": "Data is still loading - please wait a few minutes."},
            status_code=503,
            headers={"Retry-After": "15"},
        )
    data.maybe_refresh_snapshot()
    return await call_next(request)


# Allow the Vite dev server (and previews) to call the API from the browser.
# Match ANY loopback origin/port: Vite auto-increments (5173 -> 5174 -> ...) when
# a port is busy, and the preview server uses 4173. Also match private-LAN IPs
# (192.168.x.x / 10.x.x.x / 172.16-31.x.x) so colleagues on the same office
# network can load the dashboard from this machine's LAN IP (see run.py's
# ALLOW_LAN socket and dashboard/.env's VITE_API_URL) — still bounded to
# private address space, not the open internet. ALSO allow any deployed
# Cloud Run service's *.run.app origin (2026-09-26, user-requested GCP
# deployment) - sidesteps the chicken-and-egg problem of the frontend's exact
# Cloud Run URL not being known until after it's first deployed. This adds no
# real exposure beyond what's already decided: both services are deployed
# with unauthenticated access, so an arbitrary script (not just a browser
# bound by CORS) could call this API regardless of this regex.
# Compress JSON responses (2026-10-05): the report payloads are 1-2 MB of
# repetitive JSON and shrink ~8-10x, which matters most over the internet.
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://[a-z0-9-]+-[a-z0-9]+\.[a-z0-9-]+\.run\.app|http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):\d+",
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok" if data.DATA_READY else "loading",
        "dataReady": data.DATA_READY,
        "rows": len(data.PLAN_ROWS),
        "snapshot": data.SNAPSHOT_DATE.isoformat(),
        "dataSource": data.DATA_SOURCE,
        **data.model_status(),
    }


# The dashboard's Refresh button calls this without credentials (a key in a
# public web page would not stay secret), so it is protected by a lock - one
# refresh at a time - and a cooldown between refreshes instead (2026-10-19
# security fix: anyone reaching the API could trigger rebuilds back to back).
REFRESH_COOLDOWN_SECS = int(os.getenv("REFRESH_COOLDOWN_SECS", "600"))
_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH = 0.0


@app.get("/api/data-health", tags=["meta"])
def data_health() -> dict:
    """Checks run on the loaded data: failed ERP stock / WIP (Suggested
    Production overstated), incomplete or stale sales, a sale spike inside the
    run-rate, sale-calendar mismatches, low accuracy of the last completed week,
    selling styles with no master data. {checkedOn, dataThrough, issues: [...]}"""
    if not data.DATA_READY:
        return {"checkedOn": "", "dataThrough": "", "issues": []}
    return data.data_health()


@app.post("/admin/refresh", tags=["meta"])
def refresh() -> dict:
    """Re-fetch the source data (live BigQuery + ERP, or CSV) and rebuild all
    in-memory tables. Use this to pull fresh data without restarting.
    With SERVING_MODE=snapshot it starts the daily Cloud Run Job instead; the
    API picks up the job's new snapshot within SNAPSHOT_CHECK_SECS.
    409 while a refresh is running; 429 within REFRESH_COOLDOWN_SECS of the last one."""
    global _LAST_REFRESH
    if not _REFRESH_LOCK.acquire(blocking=False):
        raise HTTPException(409, "A refresh is already running - please wait for it to finish.")
    try:
        wait = REFRESH_COOLDOWN_SECS - (time.time() - _LAST_REFRESH)
        if wait > 0:
            raise HTTPException(
                429, f"Data was refreshed less than {REFRESH_COOLDOWN_SECS // 60} min ago - "
                     f"try again in {math.ceil(wait / 60)} min.",
                headers={"Retry-After": str(math.ceil(wait))},
            )
        if data.SERVING_MODE == "snapshot":
            result = {"status": "job started", **data.start_daily_job()}
        else:
            result = {"status": "refreshed", **data.rebuild()}
        _LAST_REFRESH = time.time()
        return result
    finally:
        _REFRESH_LOCK.release()


@app.get("/api/reports/weekly-grid", response_model=WeeklyGridResponse, tags=["plan"])
def get_weekly_grid(
    groupBy: str = Query("subCategory", pattern="^(subCategory|style)$",
                          description="'subCategory' or 'style' (DESIGN_NO)"),
    weeks: int = Query(13, ge=1, le=40, description="Forward forecast horizon in weeks (max 40, supports the nine-month Design Suggestions horizon)"),
    limit: int = Query(50, ge=1, le=2000, description="Rows per page (max 2000, enough for the full Style list)"),
    offset: int = Query(0, ge=0, description="Start index for pagination"),
    search: str = Query("", description="Case-insensitive substring filter on the row key"),
    subCategory: str = Query("", description="Exact Sub Category filter (applies to both groupings)"),
    category: str = Query("", description="Exact top-level Category filter (applies to both groupings)"),
) -> WeeklyGridResponse:
    """Forecasted + Actual sales, pivoted by week (grouped into months), one
    row per Sub Category or Style — the Weekly Sales Report page's data
    source. Rows are sorted by combined actual+forecast volume descending."""
    return data.get_weekly_grid(groupBy, weeks, limit, offset, search, subCategory, category)


@app.get(
    "/api/reports/channel-source-total/weekly",
    response_model=ChannelSourceWeeklyResponse,
    tags=["plan"],
)
def get_channel_source_weekly_total(
    weeks: int = Query(9, ge=1, le=40, description="Forward forecast horizon in weeks — must match the Weekly Sales Report's own request for the returned week axis to line up"),
    search: str = Query("", description="Same search filter as /api/reports/weekly-grid, so the TOTAL row's breakdown covers exactly the same Styles"),
    subCategory: str = Query("", description="Same Sub Category filter as /api/reports/weekly-grid"),
    category: str = Query("", description="Same top-level Category filter as /api/reports/weekly-grid"),
) -> ChannelSourceWeeklyResponse:
    """Per-marketplace week-wise Gross Sale summed across every Style
    currently matching the Weekly Sales Report's own search/Sub Category/
    Category filter — the TOTAL row's own expandable channel breakdown
    (2026-09-18, user-requested), same rollup as the per-Style endpoint
    below just pooled across every matching Style instead of one."""
    return data.get_channel_source_weekly_total(weeks, search, subCategory, category)


@app.get("/api/reports/weekly-production-log", tags=["plan"])
def get_weekly_production_log(
    style: str = Query("", description="One Style (DESIGN_NO); blank = all"),
    status: str = Query("", pattern="^(|open|settling|completed)$", description="'open' (week still running), 'settling' (ended, actual still updating), 'completed' (final), or blank for all"),
) -> list[dict]:
    """Weekly production log (api/weekly_log.py): per Style and report week,
    the forecast and ~2-month suggested production captured when the week
    started, and the actual sale once it finished. Latest 8 completed weeks
    plus the running week (2026-09-28, user-requested)."""
    return data.get_weekly_production_log(style, status)


@app.get("/api/catalog/style-tiers", response_model=CatalogStyleTiersResponse, tags=["plan"])
def get_catalog_style_tiers() -> CatalogStyleTiersResponse:
    """Every active catalog Style's image + externally-maintained forecast
    tier (Cloud SQL's CatalogStyle.forecastStatus), grouped by tier —
    powers the Style Tier Gallery page's tier buttons + image grid
    (2026-09-21, user-requested)."""
    return CatalogStyleTiersResponse(tiers=catalog_style.get_style_tiers())


@app.get("/api/catalog/tier-movement", tags=["plan"])
def get_tier_movement() -> dict:
    """How long styles stay in each catalog tier and where they move, from the
    Cloud SQL tier-change log (2026-10-13, user-requested Tier movement tab):
    per-tier summary, from -> to moves and each style's tier journey."""
    return data.get_tier_movement()


@app.get("/api/catalog/style-lifecycle", tags=["plan"])
def get_style_lifecycle(
    subCategory: str = Query("", description="Exact Sub Category name; blank returns every Sub Category"),
) -> dict:
    """Styles that had a real (Gross) sale last calendar year vs styles newly
    launched this calendar year, grouped by Sub Category (2026-09-22, user-
    requested Weekly Sales Report feature)."""
    return data.get_style_lifecycle(subCategory)


@app.get("/api/reports/category-analysis", tags=["plan"])
def get_category_analysis(
    level: str = Query("subCategory", pattern="^(category|subCategory|style|tier)$",
                       description="'category', 'subCategory', 'style' (one row per style) or 'tier' (one row per catalog tier)"),
    category: str = Query("", description="Only styles in this Category (Tiers Spike Rate filter); blank = all"),
    subCategory: str = Query("", description="Only styles in this Sub Category (Tiers Spike Rate filter); blank = all"),
    tierMode: str = Query("current", pattern="^(current|sale)$",
                          description="tier level: 'current' tier for every month, or the tier at the time of 'sale'"),
) -> dict:
    """Per month (April 2025 to now) and Category / Sub Category: styles with a
    Gross sale, order count, units, and the units' % change vs the
    previous month (the partial current month vs the same days of the
    previous month) - the Category Analysis page (2026-10-03, user-requested)."""
    return data.get_category_analysis(level, category, subCategory, tierMode)


@app.get("/api/reports/category-analysis/channels", tags=["plan"])
def get_category_analysis_channels(
    level: str = Query("subCategory", pattern="^(category|subCategory|style|tier)$"),
    group: str = Query(..., min_length=1, description="the Category / Sub Category / Style row to break down"),
    category: str = Query("", description="Only styles in this Category; blank = all"),
    subCategory: str = Query("", description="Only styles in this Sub Category; blank = all"),
    tierMode: str = Query("current", pattern="^(current|sale)$"),
) -> dict:
    """One Category Analysis row split by marketplace (Amazon, Flipkart, ...):
    the same monthly styles / orders / units / spike - the row's channel
    drill-down (2026-10-09, user-requested)."""
    return data.get_category_analysis_channels(level, group, category, subCategory, tierMode)


@app.get("/api/reports/category-analysis/styles", tags=["plan"])
def get_category_analysis_styles(
    level: str = Query("subCategory", pattern="^(category|subCategory|style|tier)$"),
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="YYYY-MM"),
    group: str = Query("", description="the Category / Sub Category / Tier row; blank = the TOTAL row"),
    channel: str = Query("", description="a marketplace sub-row (Amazon, Flipkart, ...); blank = all"),
    category: str = Query("", description="Only styles in this Category; blank = all"),
    subCategory: str = Query("", description="Only styles in this Sub Category; blank = all"),
    tierMode: str = Query("current", pattern="^(current|sale)$"),
) -> dict:
    """The styles behind one STYLES cell: each style's units / orders that
    month vs the previous month, new styles flagged, plus the stopped ones
    (2026-10-29, user-requested)."""
    return data.get_category_analysis_styles(level, group, month, channel, category, subCategory, tierMode)


@app.get("/api/reports/category-analysis/filtered", tags=["plan"])
def get_category_analysis_filtered(
    tier: str = Query("", description="Catalog tier (T0, T1, ... or Unclassified); blank = all"),
    category: str = Query("", description="Category; blank = all"),
    subCategory: str = Query("", description="Sub Category; blank = all"),
    style: str = Query("", description="One style (design number); blank = all"),
    brand: str = Query("", description="Brand (Mokosh, J.Turritopsis, Curly Tales, C.O.E, ...); blank = all"),
) -> dict:
    """Monthly styles / orders / units / spike for the styles matching all the
    filters - the Tiers Spike Rate chart (2026-10-13, user-requested). Also
    lists every style's tier / category / sub category for the filter menus."""
    return data.get_category_analysis_filtered(tier, category, subCategory, style, brand)


@app.get(
    "/api/reports/channel-source/{style}/weekly",
    response_model=ChannelSourceWeeklyResponse,
    tags=["plan"],
)
def get_channel_source_weekly(
    style: str,
    weeks: int = Query(9, ge=1, le=40, description="Forward forecast horizon in weeks — must match the Weekly Sales Report's own request for the returned week axis to line up"),
) -> ChannelSourceWeeklyResponse:
    """Per-marketplace (Amazon/Flipkart/Meesho/Myntra/Nykaa for OMS,
    MOKOSH/Color's Of Earth for WEBSITE) week-wise Gross Sale for one Style —
    the inline expandable-row drill-down behind the Demand Forecasting
    page's per-Style rows."""
    return data.get_channel_source_weekly(style, weeks)


@app.get("/api/debug/tier-accuracy", tags=["debug"])
def debug_tier_accuracy() -> dict:
    """Design-level backtest accuracy broken out per catalog tier (2026-09-22,
    user-requested: "Run the Backtest for each tier") — {tier: {accuracy,
    designs, weeks}}. Empty until at least one forecasted week has both
    calendar-elapsed and matured (see data.MATURATION_DAYS)."""
    return {
        "designLevelAccuracy": data.DESIGN_ACCURACY,
        "tierAccuracy": data.TIER_ACCURACY,
    }


@app.get("/api/debug/overall-accuracy", tags=["debug"])
def debug_overall_accuracy() -> dict:
    """SKU/design-level overall accuracy the LIVE process already has
    loaded, read-only (2026-09-24, user-requested: "what is the overall
    accuracy" - added so this can be checked without spawning a fresh
    process that has to redo a full live data load, which has occasionally
    hit a real MemoryError on this machine). ``runBacktest: false`` means
    these are the naive-only proxy fallback, not a genuine walk-forward
    backtest (see data.RUN_BACKTEST)."""
    return {
        "runBacktest": data.RUN_BACKTEST,
        # 2026-10-19: say plainly what the numbers are - with the backtest off
        # they score the naive run-rate, not the forecasting model.
        "measures": "model walk-forward backtest" if data.RUN_BACKTEST
        else "naive run-rate proxy - NOT the model's accuracy (backtest off; set RUN_BACKTEST=1 to measure the model)",
        "overallAccuracySku": data.OVERALL_ACCURACY,
        "designAccuracy": data.DESIGN_ACCURACY,
        "latestWeekAccuracy": data.LATEST_WEEK_ACCURACY,
        "latestWeekIso": data.LATEST_WEEK_ISO,
        "dataSource": data.DATA_SOURCE,
    }


@app.get("/api/design/suggestions", tags=["design"])
def get_design_suggestions() -> dict:
    """Season-wise design suggestions for the running and upcoming season
    (2026-10-08, user-requested add-on, see design_suggest.py): preferred
    Category / Sub Category / Colour / Fabric, CatBoost attribute effects,
    best and untried combinations, gaps / saturated / declining, and a
    design-direction brief. Built in the background on first request per
    data refresh; returns {"building": true} until ready."""
    import design_suggest
    return design_suggest.get_design_suggestions()
