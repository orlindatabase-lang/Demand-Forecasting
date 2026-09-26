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
    stockStatus: str  # "In Stock" | "Needs Production"
    historicalLast10d: int
    subCategory: str = ""  # SUB CATEGORY from the SKU master sheet (see design_attributes.sub_category_of)
    category: str = ""  # top-level CATEGORY from the SKU master sheet (see design_attributes.category_of)
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


class WeeklyGridCell(BaseModel):
    """One week's numbers for one row of the weekly sales grid (see
    WeeklyGridResponse). ``actual`` is None for a future/forecast-only week
    (it hasn't happened yet) - the frontend renders that as "-"."""
    actual: int | None = None
    forecast: int = 0
    partial: bool = False  # the current, still-in-progress week


class WeeklyGridEvent(BaseModel):
    name: str
    category: str  # "festival" | "sale"
    start: str  # ISO date - the event's OWN real start date, not the week's
    end: str    # ISO date - the event's OWN real end date, not the week's


class WeeklyGridWeek(BaseModel):
    weekStart: str  # ISO Monday date - matches the key in each row's `cells`
    label: str       # "W1", "W2", ... position within its month
    eventCategory: str = ""  # "festival" | "sale" | "both" | "" - see festival_calendar.py (2026-09-24, user-requested)
    eventName: str = ""      # overlapping event name(s), joined with " + " if more than one; "" if none
    events: list[WeeklyGridEvent] = []  # per-event exact start/end dates (2026-09-24, user-requested: click a W1..W4 header to see them)


class WeeklyGridMonth(BaseModel):
    label: str  # e.g. "March 2025"
    weeks: list[WeeklyGridWeek]


class WeeklyGridRow(BaseModel):
    key: str  # Sub Category name, or Style/DESIGN_NO
    subCategory: str  # this row's Sub Category (== key when groupBy="subCategory")
    category: str = ""  # this row's top-level Category (see design_attributes.category_of)
    skuCount: int    # distinct SKU (size/color variant) codes pooled into this row
    styleCount: int  # distinct STYLE/DESIGN_NO codes pooled into this row (== 1 for a "style" row)
    launchDate: str = ""      # ISO date of this row's earliest-launched SKU, "" if unknown/a Sub Category row
    daysSinceLaunch: int = -1  # days since that earliest launch, -1 if unknown
    cells: dict[str, WeeklyGridCell]  # weekStart (ISO) -> cell
    monthActualTotal: dict[str, int]     # month label -> summed actual
    monthForecastTotal: dict[str, int]   # month label -> summed forecast
    grandActualTotal: int
    grandForecastTotal: int
    futureForecastTotal: int   # sum of the forward-only forecast weeks (excludes historical backtested figures)
    availableQty: int          # summed inventory + WIP across this row's SKUs
    suggestedProduction: int   # max(0, futureForecastTotal - availableQty)
    festivalBoosted: bool = False  # true if the upcoming festival's weeks were raised above the base forecast


class WeeklyGridTotals(BaseModel):
    """Grand-total row (2026-09-14, user-requested) - same cell/month shape
    as a WeeklyGridRow, but summed across EVERY matching group (search/Sub
    Category filtered, but not paginated), not just the current page."""
    cells: dict[str, WeeklyGridCell]  # weekStart (ISO) -> cell, summed across every matching group
    monthActualTotal: dict[str, int]     # month label -> summed actual, every matching group
    monthForecastTotal: dict[str, int]   # month label -> summed forecast, every matching group


class FestivalSubCategorySales(BaseModel):
    subCategory: str
    qty: int  # real actual units sold during the historical festival window
    upliftPct: int | None = None  # vs. an equal-length pre-event control period; None = no baseline to compare


class FestivalColorSales(BaseModel):
    color: str  # design's primary garment color (Top_Color in the SKU master sheet)
    qty: int  # real actual units sold during the historical festival window
    upliftPct: int | None = None  # vs. an equal-length pre-event control period; None = no baseline to compare


class UpcomingEventOutlook(BaseModel):
    """One upcoming event from the real festival/sale calendar
    (festival_calendar.py) - either a Festival or a Sale - plus the
    top-selling Sub Categories and Design colors during that SAME event's
    most recent past occurrence, from REAL actual sales (not a forecast)."""
    eventName: str
    eventStart: str  # ISO date, the UPCOMING occurrence
    eventEnd: str
    historicalYear: int | None  # year of the past occurrence used, None if no real data covers one
    historicalStart: str = ""
    historicalEnd: str = ""
    spikeLeadDays: int | None = None  # days before the historical occurrence real demand started rising
    topSubCategories: list[FestivalSubCategorySales]
    topColors: list[FestivalColorSales] = []


class FestivalOutlook(BaseModel):
    """The next upcoming Festival and the next upcoming Sale, from the real
    festival/sale calendar (festival_calendar.py) - shown as two distinct
    things rather than just whichever comes first chronologically
    (2026-09-12, user-requested). Powers the Weekly Sales Report's festival
    outlook card.
    Either can be None if none is found within the lookahead window (999
    days) - shouldn't normally happen since festival.py's windows repeat
    every year."""
    upcomingFestival: UpcomingEventOutlook | None
    upcomingSale: UpcomingEventOutlook | None


class WeeklyGridResponse(BaseModel):
    """Forecasted + Actual sales pivoted by week/month, grouped by Sub
    Category or Style - powers the Weekly Sales Report page. ``months`` is
    the shared week/month axis every row's `cells` are keyed against; `rows`
    is one page (see `total`) of groups, sorted by combined actual+forecast
    volume descending."""
    groupBy: str  # "subCategory" | "style"
    total: int  # total distinct groups matching (before pagination)
    totalForecast: int  # sum of futureForecastTotal across EVERY matching group, not just this page
    totalActual: int    # sum of grandActualTotal across EVERY matching group, not just this page (GROSS: the
    # user's own explicit status allowlist, see data.py's _GROSS_SALE_STATUSES - 2026-09-15, includes returns/RTO)
    currentWeekForecast: int  # sum of the current (possibly still in-progress) week's forecast, ALL groups
    currentWeekActual: int    # sum of the current week's actual-so-far, ALL groups
    currentWeekLabel: str     # ISO week number of the current week, e.g. "W37"
    totalSuggestedProduction: int  # sum of suggestedProduction across EVERY matching group, not just this page
    subCategoryOptions: list[str]  # every distinct Sub Category in the current plan, for the filter dropdown
    categoryOptions: list[str] = []  # every distinct top-level Category in the current plan, for the filter dropdown
    categorySubCategoryMap: dict[str, list[str]] = {}  # category -> its own Sub Categories, for the cascading filter
    festivalOutlook: FestivalOutlook | None  # None if no source data is loaded yet
    months: list[WeeklyGridMonth]
    rows: list[WeeklyGridRow]
    weeklyTotals: WeeklyGridTotals  # grand-total row, every matching group (not just this page)


class CatalogStyleImage(BaseModel):
    """One Style's catalog image + externally-maintained forecast tier (from
    Cloud SQL's CatalogStyle.forecastStatus) - see api/catalog_style.py."""
    name: str  # Style/DESIGN_NO, e.g. "417-03"
    tier: str  # "T0".."T3"/"T11"/"T12" as found in the source, or "" if unclassified
    imageUrl: str  # public GCS URL - "" if this Style has neither approvedImage nor launchImage


class CatalogStyleTierGroup(BaseModel):
    tier: str  # tier label, or "Unclassified" for a null forecastStatus
    count: int
    styles: list[CatalogStyleImage]


class CatalogStyleTiersResponse(BaseModel):
    """Every active catalog Style, grouped by its forecastStatus tier and
    sorted in natural tier order (T0, T1, T2, T3, T11, T12, ..., Unclassified
    last) - powers the Style Tier Gallery page's tier buttons + image grid."""
    tiers: list[CatalogStyleTierGroup]


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


class ChannelSourceWeeklySeries(BaseModel):
    # Marketplace/site name (2026-09-16, user-requested: name the actual
    # platform, not the raw internal channel_name) - "Amazon", "Flipkart",
    # "Meesho", "Myntra", "Nykaa" for OMS rows, "MOKOSH"/"Color's Of Earth"
    # for WEBSITE rows. Several raw channel_name values (different legal
    # entities selling on the same platform, e.g. "DiEGO International -
    # Amazon FBA" and "Turritopsis - Amazon FBA") roll up into one row here.
    marketplace: str
    source: str   # "OMS" | "WEBSITE"
    totalQty: int  # summed across every week in the response's weekStarts
    cells: dict[str, int]  # weekStart (ISO) -> Gross qty, sparse (only weeks with any qty)
    # weekStart (ISO) -> this channel's forecasted qty for that week
    # (2026-09-18, user-requested: show Forecast, not just Actual, per
    # channel). There is no per-channel-TRAINED model - this is a top-down
    # proportional split of the row's own already-computed pooled forecast
    # (the same number the row's main cells show), allocated by this
    # channel's own historical share of the row's real actual sales. See
    # data.py's _channel_source_forecast_by_chunk().
    forecastCells: dict[str, float] = {}


class ChannelSourceWeeklyResponse(BaseModel):
    """Per-marketplace week-wise Gross Sale for one Style, on the SAME
    calendar-day week axis as the Weekly Sales Report's own row cells (see
    data.py's _calendar_chunk_axis) - the inline expandable-row drill-down
    behind the Demand Forecasting page's per-Style rows (2026-09-16,
    user-requested: replace the month-level dialog with an inline dropdown,
    shown week-wise)."""
    style: str
    weekStarts: list[str]  # ISO dates, oldest to newest - same axis as WeeklyGridRow.cells keys
    series: list[ChannelSourceWeeklySeries]  # one per marketplace with any sales, sorted by totalQty descending
