"""Pydantic response schemas for the SKU Production Plan API.

Field names match the dashboard's `PlanningRow` type exactly so the React app
can consume this API with no transformation.
"""
from __future__ import annotations

from pydantic import BaseModel


class PlanRow(BaseModel):
    skuCode: str
    designNo: str
    date: str  # snapshot date (ISO)
    forecast7: int
    forecast10: int
    forecast35: int
    designForecast35: int = 0  # this SKU's parent design's own 5-week forecast (compute_design(),
    # measurably more accurate than SKU-week — context only, NOT used for
    # DRR/reorder/production math below, which stays SKU-level deliberately
    # (redistributing a design total back to SKU grain tested worse)
    inventoryQty: int
    wipQty: int
    availableQty: int  # inventoryQty + wipQty
    # --- production policy: flat 10-week-demand target (currentDrr * 10 weeks) ---
    # Uses currentDrr (historical trailing-30-day actual, set by lifecycle.classify),
    # NOT the forward forecast, so this matches the dashboard's own DRR column.
    leadTimeDays: int = 0       # informational only; no longer gates production
    safetyStock: int = 0        # informational only; no longer gates production
    reorderPoint: int = 0       # = totalSuggestedProduction (10-week demand target)
    totalSuggestedProduction: int  # currentDrr * 10 weeks (target stock level)
    calculatedProductionSuggestion: int  # totalSuggestedProduction - availableQty (can be negative)
    stockStatus: str  # "In Stock" | "Reorder"
    historicalLast10d: int
    vertical: str = ""  # product vertical bucket derived from DESIGN_GROUP (see verticals.py)
    price: float = 0.0  # realized avg selling price (revenue/qty) over the last 90 days; 0 if no recent sales
    # --- velocity tier (back-compat; now mirrors currentTier) ---
    tier: str = ""
    tierSuggestedProduction: int = 0
    tierPolicy: str = ""  # = lifecycle stage policy label
    # --- product lifecycle intelligence ---
    launchTier: str = ""        # Launch_T0..T3 (first-20-day DRR percentile) / "Unknown"
    currentTier: str = ""       # Current_T0..T3 (trailing-30-day DRR percentile)
    lifecycleStage: str = ""    # Emerging/Growing/Stable/Declining/Dead/Reviving
    daysSinceLaunch: int = -1
    launchDate: str = ""        # ISO date (YYYY-MM-DD), "" if unknown
    launchDrr: float = 0.0      # units/day over first 20 days
    currentDrr: float = 0.0     # units/day over trailing 30 days
    growthRate: float = 0.0     # (currentDrr - launchDrr) / launchDrr
    healthScore: int = 0        # 0-100
    riskScore: int = 0          # 0-100 (higher = riskier)
    productionPriority: int = 0  # 0-100 (produce-first rank)
    suggestedProduction: int = 0  # stage-aware: max(0, stage target - availableQty)
    # --- predicted festival/sale spike (real calendar dates, e.g. "8-15 Aug") ---
    festivalEvent: str = ""        # "" if this design clears no festival window
    festivalEventStart: str = ""   # ISO date
    festivalEventEnd: str = ""     # ISO date
    festivalQty: int = 0           # forecast total for [festivalEventStart, festivalEventEnd]
    festivalUpliftPct: int = 0     # vs. this design's own non-festival-day daily-rate baseline


class PlanResponse(BaseModel):
    total: int  # total rows matching the query (before pagination)
    items: list[PlanRow]
    newDesignCount: int = 0  # distinct designs launched in the last 90 days (whole plan, not just this page)


class NewDesignRow(BaseModel):
    designNo: str
    launchDate: str  # ISO date (YYYY-MM-DD)
    daysSinceLaunch: int
    skuCount: int  # number of size/SKU variants for this design


class NewDesignsResponse(BaseModel):
    total: int
    items: list[NewDesignRow]


class SimilarDesignRef(BaseModel):
    design: str
    similarity: float
    sharedMaterials: list[str]


class NewDesignFestivalSpike(BaseModel):
    designNo: str
    event: str
    eventStart: str  # ISO date - real festival/sale start (e.g. "8 Aug"), clipped to the forecast horizon
    eventEnd: str  # ISO date - real festival/sale end (e.g. "15 Aug"), clipped to the forecast horizon
    predictedQty: int  # forecast total for exactly [eventStart, eventEnd], pro-rated across weekly buckets
    upliftPct: int  # vs. this design's own non-festival-week daily-rate baseline
    similarDesigns: list[SimilarDesignRef]  # material-similar neighbors the cold-start blend borrowed from


class NewDesignFestivalSpikesResponse(BaseModel):
    total: int
    items: list[NewDesignFestivalSpike]


class HistoricalPoint(BaseModel):
    date: str  # Monday start of the week
    forecast: int  # what had been forecast for that past week (back-test)
    actual: int  # what actually sold that week
    variance: int  # actual - forecast
    partial: bool = False  # True for the current, still-in-progress week (actual will keep rising)


class ForecastPoint(BaseModel):
    date: str  # Monday start of the forecast week
    qty: int  # forecast units for the week
    event: str | None = None  # festival / sale season driving an uplift, if any


class BreakdownResponse(BaseModel):
    sku: str
    snapshotDate: str
    periodWeeks: int
    accuracyPct: int  # this SKU's accuracy (100 - MAPE over the historical weeks)
    overallAccuracyPct: int = 0  # model-wide accuracy (volume-weighted WAPE)
    historical: list[HistoricalPoint]  # last weeks, forecast vs actual
    forecast: list[ForecastPoint]  # next `periodWeeks` weeks


class TopRegion(BaseModel):
    """Top-selling state/city (matches dashboard's TopRegionRow); revenue +
    quantity for the last 10/30/90 days."""
    name: str
    revenue10: int
    revenue30: int
    revenue90: int
    units10: int
    units30: int
    units90: int


class TopWarehouse(BaseModel):
    """Top warehouse (matches dashboard's TopWarehouseRow); revenue +
    quantity for the last 10/30/90 days."""
    name: str
    revenue10: int
    revenue30: int
    revenue90: int
    units10: int
    units30: int
    units90: int


class SkuTopRegionsResponse(BaseModel):
    """One SKU's own top-selling states/cities/warehouses (10/30/90-day
    revenue + units) — same shape as the plan-wide top tables, filtered to
    just this SKU's order history. Powers the SKU detail drawer."""
    topStates: list[TopRegion]
    topCities: list[TopRegion]
    topWarehouses: list[TopWarehouse]


class AllBreakdownsResponse(BaseModel):
    """Week-wise breakdown for many SKUs at once, paginated (offset/limit)
    since the per-week arrays make the full all-SKU payload too large for a
    browser to render in one go."""
    snapshotDate: str
    periodWeeks: int
    count: int        # total SKUs available
    returned: int     # SKUs in this page
    offset: int
    limit: int
    items: list[BreakdownResponse]  # each: weekly forecast + weekly forecast-vs-actual
