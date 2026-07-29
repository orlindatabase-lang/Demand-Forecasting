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
from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import data
import debit_note
import embroidery
import fob
import inhouse
import job_work
import purchase_order
from models import (
    AllBreakdownsResponse,
    BreakdownResponse,
    NewDesignFestivalSpikesResponse,
    NewDesignsResponse,
    PlanResponse,
    SkuTopRegionsResponse,
    TopRegion,
    TopWarehouse,
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
# a port is busy, and the preview server uses 4173. The API binds loopback only,
# so allowing any localhost/127.0.0.1 port is safe and avoids CORS breakage.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
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
        "forecastModel": data.ACTIVE_FORECAST_MODEL,
        "lgbmPending": data.FORECAST_MODEL == "lgbm" and data.ACTIVE_FORECAST_MODEL != "xgboost",
    }


@app.post("/admin/refresh", tags=["meta"])
def refresh() -> dict:
    """Re-fetch the source data (live BigQuery + ERP, or CSV) and rebuild all
    in-memory tables. Use this to pull fresh data without restarting."""
    result = data.rebuild()
    return {"status": "refreshed", **result}


@app.get("/api/sku-production-plan", response_model=PlanResponse, tags=["plan"])
def list_plan(
    search: str | None = Query(None, description="Match SKU or design number"),
    status: str | None = Query(None, description='Filter by status: "In Stock" or "Produce"'),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
) -> PlanResponse:
    """Paginated SKU Production Plan rows (matches the dashboard table).

    Built from real order history: net sales (excluding cancellations & returns)
    drive the 7/10/35-day demand forecasts; ``wipQty`` comes from TOTAL_WIP_QTY.
    Rows are ordered by 35-day forecast (highest demand first)."""
    total, items = data.get_plan(search=search, status=status, offset=offset, limit=limit)
    return PlanResponse(total=total, items=items, newDesignCount=data.get_new_design_count())


@app.get("/api/new-designs", response_model=NewDesignsResponse, tags=["plan"])
def list_new_designs(
    maxAgeDays: int = Query(90, ge=1, le=3650, description="Launched within this many days"),
) -> NewDesignsResponse:
    """One row per distinct design launched within ``maxAgeDays`` days, newest
    first. Backs the "Newly Launched Designs" KPI drill-down — computed
    against the full plan so it matches ``newDesignCount`` exactly."""
    items = data.get_new_designs(max_age_days=maxAgeDays)
    return NewDesignsResponse(total=len(items), items=items)


@app.get("/api/new-designs/festival-outlook", response_model=NewDesignFestivalSpikesResponse, tags=["plan"])
def list_new_design_festival_spikes(
    maxAgeDays: int = Query(90, ge=1, le=3650, description="Launched within this many days"),
) -> NewDesignFestivalSpikesResponse:
    """Newly-launched designs whose 6-week forecast predicts a genuine uplift
    in a festival/sale week, plus the material-similar designs the cold-start
    blend borrowed that demand shape from (these designs haven't lived
    through a festival of their own yet)."""
    items = data.get_new_design_festival_spikes(max_age_days=maxAgeDays)
    return NewDesignFestivalSpikesResponse(total=len(items), items=items)


# Declared BEFORE "/{sku}" so the static "breakdown" path is matched first.
@app.get("/api/sku-production-plan/breakdown", response_model=AllBreakdownsResponse, tags=["plan"])
def get_breakdowns(
    weeks: int = Query(6, ge=1, le=6, description="Forecast horizon in weeks (max 6)"),
    offset: int = Query(0, ge=0, description="Start index for pagination"),
    limit: int = Query(500, ge=1, le=500, description="SKUs per page (max 500)"),
) -> AllBreakdownsResponse:
    """Week-wise breakdown for ALL SKUs, paginated: each SKU's next `weeks` of
    forecast plus its recent weekly forecast-vs-actual comparison. Page through
    with offset/limit (the full week-wise dataset is too large to render at once)."""
    total, items = data.get_all_breakdowns(weeks, offset, limit)
    return AllBreakdownsResponse(
        snapshotDate=data.SNAPSHOT_DATE.isoformat(),
        periodWeeks=weeks,
        count=total,
        returned=len(items),
        offset=offset,
        limit=limit,
        items=items,
    )


@app.get("/api/sku-production-plan/{sku}", tags=["plan"])
def get_one(sku: str):
    """A single SKU's plan row."""
    row = data.PLAN_BY_SKU.get(sku)
    if row is None:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found")
    return row


@app.get("/api/sku-production-plan/{sku}/breakdown", response_model=BreakdownResponse, tags=["plan"])
def get_sku_breakdown(
    sku: str,
    weeks: int = Query(6, ge=1, le=6, description="Forecast horizon in weeks (e.g. 4, 6)"),
) -> BreakdownResponse:
    """Single-SKU week-wise breakdown for the drill-down popup: recent weekly
    forecast-vs-actual comparison plus the next `weeks` of forecast."""
    result = data.get_breakdown(sku, weeks)
    if result is None:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found")
    return result


@app.get("/api/sku-production-plan/{sku}/top-regions", response_model=SkuTopRegionsResponse, tags=["plan"])
def get_sku_top_regions(
    sku: str,
    limit: int = Query(10, ge=1, le=100, description="Max states/cities/warehouses to return each"),
) -> SkuTopRegionsResponse:
    """This SKU's own top-selling states/cities/warehouses (10/30/90-day
    revenue + units) — same shape as the plan-wide top tables on the main
    dashboard, but filtered to just this SKU's order history."""
    result = data.get_sku_top_regions(sku, cap=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found")
    return SkuTopRegionsResponse(**result)


@app.get("/api/inventory/planning", tags=["plan"])
def inventory_planning(
    limit: int = Query(500, ge=1, le=5000, description="Max designs to return"),
) -> dict:
    """Optimal finished-goods stock levels by design: current stock, demand,
    weeks of cover, safety stock, reorder point, and production suggestion."""
    return data.get_inventory_planning(limit=limit)



@app.get("/api/production/embroidery", tags=["production"])
def embroidery_lots(
    limit: int = Query(1000, ge=1, le=5000, description="Max lots to return"),
) -> dict:
    """Embroidery lots: Issue vs Receive (GRN) per lot.

    Merges 2 ERP views:
      Issue view → Issue Qty, Issue Date, Vendor, Design (from ARTICLE_NAME)
      GRN view   → Receive Qty, Receive Date

    Returns only open lots (pendingQty > 0), sorted oldest-first.
    Refreshes from ERP every 5 minutes in background."""
    return embroidery.get_data(limit=limit)


@app.get("/api/production/job-work", tags=["production"])
def job_work_lots(
    limit:   int      = Query(5000, ge=1, le=10000, description="Max rows to return"),
    process: str | None = Query(None, description=(
        "Filter by process: 'Cut to Pack Issue' | 'Cut To Pack Dispatch' | "
        "'Cut to Stitching Issue' | 'Job Work Stitching GRN' | "
        "'Only Stitching Issue' | 'Job QC Process'"
    )),
) -> dict:
    """Job Work flat view: one row per process event from the ERP view.

    Single ERP view: View_Dboard_Trans_JOB_WORK_ISSUE_RECEIVE_For_Test_BI
    Optional process filter to drill into a specific process type.
    Response includes per-process row counts for the dropdown badges.
    Refreshes from ERP every 5 minutes in background."""
    return job_work.get_data(limit=limit, process=process)


@app.get("/api/production/inhouse", tags=["production"])
def inhouse_lots(
    limit:   int      = Query(5000, ge=1, le=10000, description="Max rows to return"),
    process: str | None = Query(None, description=(
        "Filter by process: 'Cutting' | 'Stitching' | 'Thread Cutting Store' | "
        "'General Store' | 'Final Barcode Generator'"
    )),
) -> dict:
    """Inhouse production rows. Refreshes from ERP every 5 minutes in background."""
    return inhouse.get_data(limit=limit, process=process)


@app.get("/api/production/fob", tags=["production"])
def fob_lots(
    limit: int = Query(5000, ge=1, le=10000, description="Max rows to return"),
) -> dict:
    """FOB Issue rows enriched with FOB Receive data.
    Refreshes from ERP every 5 minutes in background."""
    return fob.get_data(limit=limit)


@app.get("/api/debug/inhouse-columns", tags=["debug"])
def inhouse_columns() -> dict:
    """Return column names + first 2 raw rows from the Inhouse ERP view for debugging."""
    import requests as _req
    headers = {
        "Report-Api-Token": inhouse._API_TOKEN,
        "ViewName":         inhouse._VIEW,
        "CompanyYearId":    inhouse._COMPANY_YEAR_IDS[0],
        "Accept":           "application/json",
    }
    try:
        r = _req.get(inhouse._ERP_URL, headers=headers, timeout=60)
        r.raise_for_status()
        rows = r.json()
        return {
            "columns":  list(rows[0].keys()) if rows else [],
            "sample":   rows[:2] if rows else [],
            "rowCount": len(rows),
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/debug/inhouse-raw-processes", tags=["debug"])
def inhouse_raw_processes() -> dict:
    """Return ERP process names + receive-row stats from the last inhouse refresh (no new ERP call)."""
    with inhouse._LOCK:
        rows = list(inhouse._RAW_ROWS)
    return {
        "erpCols":        inhouse._ALL_ERP_COLS,
        "detectedCols":   inhouse._DETECTED_COLS,
        "receiveStats":   inhouse._RECEIVE_STATS,
        "cachedRowCount": len(rows),
        "sampleRow":      rows[0] if rows else None,
    }


@app.get("/api/debug/inhouse-processes", tags=["debug"])
def inhouse_processes() -> dict:
    """Return every unique process name the ERP view actually returns — use this to fix mismatches."""
    import requests as _req
    headers = {
        "Report-Api-Token": inhouse._API_TOKEN,
        "ViewName":         inhouse._VIEW,
        "CompanyYearId":    inhouse._COMPANY_YEAR_IDS[0],
        "Accept":           "application/json",
    }
    try:
        r = _req.get(inhouse._ERP_URL, headers=headers, timeout=60)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return {"processNames": [], "rowCount": 0}
        proc_col = next(
            (k for k in rows[0].keys()
             if k.upper().replace(" ", "_") in ("PROCESS", "PROCESS_NAME")),
            None,
        )
        if proc_col is None:
            return {"error": "Process column not found", "columns": list(rows[0].keys())}
        names = sorted({str(row.get(proc_col, "")).strip() for row in rows if row.get(proc_col)})
        return {
            "procColumn":    proc_col,
            "processNames":  names,
            "rowCount":      len(rows),
            "expectedInhouse": inhouse.ALL_PROCESSES,
            "expectedFob":     [fob._FOB_ISSUE, fob._FOB_RECEIVE],
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/production/purchase-order", tags=["production"])
def purchase_order_lots(
    limit: int = Query(1000, ge=1, le=5000, description="Max lots to return"),
) -> dict:
    """Purchase Order lots with FCM and Allocation status for delay prediction.

    Merges 4 ERP views per lot:
      PO view    → Issue Qty, Issue Date, Vendor, Est. Delivery
      GRN view   → Receive Qty, Receive Date
      FCM view   → Fabric QC status (Pass / Fail / Partial / Pending)
      Alloc view → Allocation Qty, Allocation Date

    Returns only open lots (pendingQty > 0), sorted oldest-first.
    Refreshes from ERP every 5 minutes in background."""
    return purchase_order.get_data(limit=limit)


@app.get("/api/production/debit-notes", tags=["production"])
def debit_notes_by_lot() -> dict:
    """All debit notes grouped by lot number.
    Only includes lots that actually have a debit note applied."""
    lookup = debit_note.by_lot()
    return {
        "available":  debit_note._FETCHED_AT > 0,
        "asOf":       str(date.today()),
        "totalLots":  len(lookup),
        "totalNotes": sum(len(v) for v in lookup.values()),
        "byLot":      lookup,
    }


@app.get("/api/production/bottlenecks", tags=["plan"])
def production_bottlenecks() -> dict:
    """Per-process bottleneck ranking derived from inhouse production lots.

    Scores each process (Cutting / Stitching / …) by a composite of delay rate,
    average overrun vs expected days, and queue depth. Returns processes sorted
    worst-first so the dashboard can surface the primary blocker immediately."""
    return inhouse.get_bottlenecks()


@app.get("/api/production/delays", tags=["plan"])
def production_delays(
    limit: int = Query(100, ge=1, le=1000, description="Max lots to return"),
    risk: str | None = Query(None, description="Filter by band: High / Medium / Low"),
) -> dict:
    """Open production lots ranked by predicted delay probability (Module 3).

    Trained on the lot-journey (order→dispatch lead time) with a censoring-aware
    'delayed = exceeds target lead days' label. Returns model metrics + at-risk
    lots. Empty/`available:false` until the background model has trained."""
    return data.get_delays(limit=limit, risk=risk)


# --- top-selling tables -------------------------------------------------------
# Each row carries gross revenue (sum of order `total`) and sale quantity for
# the last 10 / 30 / 90 days, ordered by 30-day quantity (highest first).
@app.get("/api/top-states", response_model=list[TopRegion], tags=["tops"])
def top_states() -> list[TopRegion]:
    """Top-selling states by gross revenue & sale quantity (10 / 30 / 90 days)."""
    return data.TOP_STATES


@app.get("/api/top-cities", response_model=list[TopRegion], tags=["tops"])
def top_cities() -> list[TopRegion]:
    """Top-selling cities by gross revenue & sale quantity (10 / 30 / 90 days)."""
    return data.TOP_CITIES


@app.get("/api/top-warehouses", response_model=list[TopWarehouse], tags=["tops"])
def top_warehouses() -> list[TopWarehouse]:
    """Top warehouses by gross revenue & sale quantity (10 / 30 / 90 days)."""
    return data.TOP_WAREHOUSES



@app.get("/api/debug/rebuild", tags=["debug"])
def debug_rebuild() -> dict:
    """Trigger a data rebuild (re-fetches all ERP views) and return immediately.
    The background delay thread runs asynchronously; poll /api/debug/allocation to confirm."""
    result = data.rebuild()
    return {"triggered": True, "rebuild_result": result}




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
