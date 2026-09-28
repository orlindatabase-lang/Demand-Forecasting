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

import os
import sys
import urllib.request

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

import catalog_style
import data
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

app = FastAPI(
    title="Orlin · SKU Production Plan API",
    version="1.0.0",
    description=(
        "Serves the SKU Production Plan table, the per-SKU date-wise breakdown, "
        "and the top-selling state / city / warehouse tables. All figures are "
        "derived from real order history (final_merged_data.csv)."
    ),
)

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
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://[a-z0-9-]+-[a-z0-9]+\.[a-z0-9-]+\.run\.app|http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):\d+",
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "rows": len(data.PLAN_ROWS),
        "snapshot": data.SNAPSHOT_DATE.isoformat(),
        "dataSource": data.DATA_SOURCE,
        **data.model_status(),
    }


@app.post("/admin/refresh", tags=["meta"])
def refresh() -> dict:
    """Re-fetch the source data (live BigQuery + ERP, or CSV) and rebuild all
    in-memory tables. Use this to pull fresh data without restarting."""
    result = data.rebuild()
    return {"status": "refreshed", **result}


@app.get("/api/reports/weekly-grid", response_model=WeeklyGridResponse, tags=["plan"])
def get_weekly_grid(
    groupBy: str = Query("subCategory", pattern="^(subCategory|style)$",
                          description="'subCategory' or 'style' (DESIGN_NO)"),
    weeks: int = Query(13, ge=1, le=13, description="Forward forecast horizon in weeks (max 13, =~ 3 months)"),
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
    weeks: int = Query(9, ge=1, le=13, description="Forward forecast horizon in weeks — must match the Weekly Sales Report's own request for the returned week axis to line up"),
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
    status: str = Query("", pattern="^(|open|completed)$", description="'open' (week still running), 'completed', or blank for both"),
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


@app.get("/api/catalog/style-lifecycle", tags=["plan"])
def get_style_lifecycle(
    subCategory: str = Query("", description="Exact Sub Category name; blank returns every Sub Category"),
) -> dict:
    """Styles that had a real (Gross) sale last calendar year vs styles newly
    launched this calendar year, grouped by Sub Category (2026-09-22, user-
    requested Weekly Sales Report feature)."""
    return data.get_style_lifecycle(subCategory)


@app.get(
    "/api/reports/channel-source/{style}/weekly",
    response_model=ChannelSourceWeeklyResponse,
    tags=["plan"],
)
def get_channel_source_weekly(
    style: str,
    weeks: int = Query(9, ge=1, le=13, description="Forward forecast horizon in weeks — must match the Weekly Sales Report's own request for the returned week axis to line up"),
) -> ChannelSourceWeeklyResponse:
    """Per-marketplace (Amazon/Flipkart/Meesho/Myntra/Nykaa for OMS,
    MOKOSH/Color's Of Earth for WEBSITE) week-wise Gross Sale for one Style —
    the inline expandable-row drill-down behind the Demand Forecasting
    page's per-Style rows."""
    return data.get_channel_source_weekly(style, weeks)


@app.get("/api/inventory/planning", tags=["plan"])
def inventory_planning(
    limit: int = Query(500, ge=1, le=5000, description="Max designs to return"),
) -> dict:
    """Optimal finished-goods stock levels by design: current stock, demand,
    weeks of cover, safety stock, reorder point, and production suggestion."""
    return data.get_inventory_planning(limit=limit)



@app.get("/api/debug/rebuild", tags=["debug"])
def debug_rebuild() -> dict:
    """Trigger a data rebuild (re-fetches all ERP views) and return immediately.
    The background delay thread runs asynchronously; poll /api/debug/allocation to confirm."""
    result = data.rebuild()
    return {"triggered": True, "rebuild_result": result}




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
    """SKU/design/channel-level overall accuracy the LIVE process already has
    loaded, read-only (2026-09-24, user-requested: "what is the overall
    accuracy" - added so this can be checked without spawning a fresh
    process that has to redo a full live data load, which has occasionally
    hit a real MemoryError on this machine). ``runBacktest: false`` means
    these are the naive-only proxy fallback, not a genuine walk-forward
    backtest (see data.RUN_BACKTEST)."""
    return {
        "runBacktest": data.RUN_BACKTEST,
        "overallAccuracySku": data.OVERALL_ACCURACY,
        "designAccuracy": data.DESIGN_ACCURACY,
        "channelAccuracy": data.CHANNEL_ACCURACY,
        "latestWeekAccuracy": data.LATEST_WEEK_ACCURACY,
        "latestWeekIso": data.LATEST_WEEK_ISO,
        "dataSource": data.DATA_SOURCE,
    }


@app.get("/api/debug/section-lookup", tags=["debug"])
def debug_section_lookup(design: str = Query("")) -> dict:
    """Show section dict size, sample entries, and look up a specific design."""
    sec_map = data._DESIGN_SECTION
    sample_bottom = {k: v for k, v in sec_map.items() if v == "Bottom"}
    sample_top    = {k: v for k, v in sec_map.items() if v == "Top"}
    sample_dup    = {k: v for k, v in sec_map.items() if v == "Dupatta"}
    return {
        "total_designs": len(sec_map),
        "bottom_count": len(sample_bottom),
        "top_count": len(sample_top),
        "dupatta_count": len(sample_dup),
        "bottom_sample": dict(list(sample_bottom.items())[:10]),
        "lookup": {design: data._design_section(design)} if design else {},
    }
