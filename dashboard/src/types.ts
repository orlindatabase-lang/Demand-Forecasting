
// --- Weekly Sales Report (Style / Sub Category pivot grid) ------------------ //
export interface WeeklyGridCell {
  actual: number | null; // null = future/forecast-only week
  forecast: number;
  partial: boolean;
}

export interface WeeklyGridEvent {
  name: string;
  category: string; // "festival" | "sale"
  start: string; // ISO date - the event's OWN real start date, not the week's
  end: string; // ISO date - the event's OWN real end date, not the week's
}

export interface WeeklyGridWeek {
  weekStart: string; // ISO Monday date - key into each row's `cells`
  label: string; // "W1", "W2", ...
  eventCategory: string; // "festival" | "sale" | "both" | "" - see api/festival_calendar.py (2026-09-24)
  eventName: string; // overlapping event name(s), joined with " + " if more than one; "" if none
  events: WeeklyGridEvent[]; // per-event exact start/end dates, shown when the W1..W4 header is clicked (2026-09-24)
}

export interface WeeklyGridMonth {
  label: string; // e.g. "March 2025"
  weeks: WeeklyGridWeek[];
}

export interface WeeklyGridRow {
  key: string; // Sub Category name, or Style/DESIGN_NO
  subCategory: string; // this row's Sub Category (== key when groupBy is "subCategory")
  category: string; // this row's top-level Category
  skuCount: number; // distinct SKU (size/color variant) codes pooled into this row
  styleCount: number; // distinct Style/DESIGN_NO codes pooled into this row (== 1 for a "style" row)
  launchDate: string; // ISO date of this row's earliest-launched SKU, "" if unknown/a Sub Category row
  daysSinceLaunch: number; // days since that earliest launch, -1 if unknown
  cells: Record<string, WeeklyGridCell>; // weekStart (ISO) -> cell
  monthActualTotal: Record<string, number>;
  monthForecastTotal: Record<string, number>;
  grandActualTotal: number;
  grandForecastTotal: number;
  futureForecastTotal: number; // forward-only forecast (excludes historical backtested figures)
  availableQty: number; // inventory + WIP summed across this row's SKUs
  suggestedProduction: number; // max(0, futureForecastTotal - availableQty)
  festivalBoosted: boolean; // true if the upcoming festival's weeks were raised above the base forecast
}

export interface FestivalSubCategorySales {
  subCategory: string;
  qty: number; // real actual units sold during the historical festival window
  upliftPct: number | null; // vs. an equal-length pre-event control period; null = no baseline to compare
}

export interface FestivalColorSales {
  color: string; // design's primary garment color (Top_Color in the SKU master sheet)
  qty: number; // real actual units sold during the historical festival window
  upliftPct: number | null; // vs. an equal-length pre-event control period; null = no baseline to compare
}

export interface UpcomingEventOutlook {
  eventName: string;
  eventStart: string; // ISO date, the UPCOMING occurrence
  eventEnd: string;
  historicalYear: number | null; // null if no real data covers a past occurrence
  historicalStart: string;
  historicalEnd: string;
  spikeLeadDays: number | null; // days before the historical occurrence real demand started rising
  topSubCategories: FestivalSubCategorySales[];
  topColors: FestivalColorSales[];
}

export interface FestivalOutlook {
  upcomingFestival: UpcomingEventOutlook | null;
  upcomingSale: UpcomingEventOutlook | null;
}

export interface WeeklyGridTotals {
  cells: Record<string, WeeklyGridCell>; // weekStart (ISO) -> cell, summed across every matching group
  monthActualTotal: Record<string, number>;
  monthForecastTotal: Record<string, number>;
}

export interface WeeklyGridResponse {
  groupBy: "subCategory" | "style";
  total: number;
  totalForecast: number; // sum of futureForecastTotal across EVERY matching group, not just this page
  totalActual: number;   // sum of grandActualTotal across EVERY matching group, not just this page (GROSS: the
  // user's own explicit status allowlist, see api/data.py's _GROSS_SALE_STATUSES - includes returns/RTO)
  currentWeekForecast: number; // current (possibly still in-progress) week's forecast, ALL groups
  currentWeekActual: number;   // current week's actual-so-far, ALL groups
  currentWeekLabel: string;    // ISO week number of the current week, e.g. "W37"
  totalSuggestedProduction: number; // sum of suggestedProduction across EVERY matching group, not just this page
  subCategoryOptions: string[]; // every distinct Sub Category in the current plan, for the filter dropdown
  categoryOptions: string[]; // every distinct top-level Category in the current plan, for the filter dropdown
  categorySubCategoryMap: Record<string, string[]>; // category -> its own Sub Categories, for the cascading filter
  festivalOutlook: FestivalOutlook | null;
  months: WeeklyGridMonth[];
  rows: WeeklyGridRow[];
  weeklyTotals: WeeklyGridTotals; // grand-total row, every matching group (not just this page)
}

// --- Catalog Style tier + image gallery (Cloud SQL CatalogStyle) ------------ //
export interface CatalogStyleImage {
  name: string; // Style/DESIGN_NO, e.g. "417-03"
  tier: string; // "T0".."T3"/"T11"/"T12" as found in the source, "" if unclassified
  imageUrl: string; // public GCS URL, "" if no image on file
}

export interface CatalogStyleTierGroup {
  tier: string; // tier label, or "Unclassified" for a null forecastStatus
  count: number;
  styles: CatalogStyleImage[];
}

export interface CatalogStyleTiersResponse {
  tiers: CatalogStyleTierGroup[]; // natural tier order, Unclassified last
}

// --- Style lifecycle: per-Sub-Category style counts (2026-09-22,
// user-requested) — styles present (sold) last calendar year vs newly
// launched this one. -------------------------------------------------- //
export interface StyleLifecycleEntry {
  presentCount: number;
  presentStyles: string[]; // sorted DESIGN_NO list
  addedCount: number;
  addedStyles: string[]; // sorted DESIGN_NO list
}

export interface StyleLifecycleResponse {
  lastYear: number;
  thisYear: number;
  bySubCategory: Record<string, StyleLifecycleEntry>;
}

// --- Channel & Source drill-down (per-Style, week-wise, by marketplace) ----- //
export interface ChannelSourceWeeklySeries {
  // Marketplace/site name, e.g. "Amazon", "Flipkart", "Meesho", "Myntra",
  // "Nykaa" (OMS) or "MOKOSH", "Color's Of Earth" (WEBSITE) — several raw
  // internal channel names (different legal entities, same platform) rolled
  // up into one row.
  marketplace: string;
  source: string;  // "OMS" | "WEBSITE"
  totalQty: number; // summed across every week in the response's weekStarts
  cells: Record<string, number>; // weekStart (ISO) -> Gross qty, sparse (only weeks with any qty)
}

export interface ChannelSourceWeeklyResponse {
  style: string;
  weekStarts: string[]; // ISO dates, oldest to newest — same axis as WeeklyGridRow.cells keys
  series: ChannelSourceWeeklySeries[]; // one per marketplace with any sales, sorted by totalQty descending
}


// --- Weekly production log (api/weekly_log.py) ------------------------------ //
export interface WeeklyProductionLogRow {
  style: string;
  sub_category: string | null;
  category: string | null;
  month: string; // e.g. "September 2026"
  week: string; // "W1".."W4"
  week_start: string; // ISO date
  week_end: string; // ISO date
  forecast_qty: number; // forecast for this week, as of week start
  forecast_2m_qty: number; // forecast for the ~2 months from the week (live until completed)
  available_qty: number; // stock + WIP (live until completed)
  suggested_production_qty: number; // max(0, forecast_2m - available) (live until completed)
  captured_on: string; // data date of the week-start capture
  updated_on: string | null; // data date of the last live update
  actual_qty: number | null; // gross units sold in the week (null until completed)
  actual_so_far: number | null; // running week only: units sold so far (live), null once completed
  completed_on: string | null;
  status: "open" | "settling" | "completed";
}

/** One (month, Category / Sub Category) row of the Category Analysis page -
 * GET /api/reports/category-analysis. Gross sale. */
export interface CategoryAnalysisRow {
  month: string; // "2026-09"
  group: string; // Category or Sub Category name
  styles: number; // distinct styles with a sale that month
  orders: number; // order lines
  units: number;
  prevUnits: number; // previous month's units (same days for the partial month)
  changePct: number | null; // units vs prevUnits, null when prevUnits = 0
  partial: boolean; // current month, data not complete yet
}

export interface CategoryAnalysisResponse {
  level: "category" | "subCategory";
  dataThrough: string;
  partialMonth: string;
  rows: CategoryAnalysisRow[];
}
