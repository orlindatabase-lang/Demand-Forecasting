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
    inventoryQty: int
    wipQty: int
    availableQty: int  # inventoryQty + wipQty
    # --- inventory policy (safety stock + reorder point + MOQ) ---
    leadTimeDays: int = 0       # replenishment lead time used (per-design or default)
    safetyStock: int = 0        # z * weekly-demand-sigma * sqrt(lead weeks)
    reorderPoint: int = 0       # lead-time demand + safety stock
    totalSuggestedProduction: int  # order-up-to level (target stock)
    calculatedProductionSuggestion: int  # MOQ-rounded produce-now qty (0 unless below reorder pt)
    stockStatus: str  # "In Stock" | "Reorder"
    historicalLast10d: int
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
    weekStart: str  # ISO date (Monday start of the predicted spike week)
    predictedQty: int
    upliftPct: int  # vs. this design's own non-festival-week baseline
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
    """Aggregated top-selling state or city (matches the dashboard's TopRegionRow).

    Revenue and sale quantity are reported for the last 10 / 30 / 90 days.
    """
    name: str
    revenue10: int
    revenue30: int
    revenue90: int
    units10: int
    units30: int
    units90: int


class TopWarehouse(BaseModel):
    """Aggregated top warehouse (matches the dashboard's TopWarehouseRow).

    Revenue and sale quantity are reported for the last 10 / 30 / 90 days.
    """
    name: str
    revenue10: int
    revenue30: int
    revenue90: int
    units10: int
    units30: int
    units90: int


class AllBreakdownsResponse(BaseModel):
    """Week-wise breakdown for many SKUs at once.

    Paginated (offset/limit) because the per-week arrays make the full
    all-SKU payload too large for a browser to render in one go.
    """
    snapshotDate: str
    periodWeeks: int
    count: int        # total SKUs available
    returned: int     # SKUs in this page
    offset: int
    limit: int
    items: list[BreakdownResponse]  # each: weekly forecast + weekly forecast-vs-actual
