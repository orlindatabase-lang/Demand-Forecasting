
// --- Weekly Sales Report (Style / Sub Category pivot grid) ------------------ //
export interface WeeklyGridCell {
  actual: number | null; // null = future/forecast-only week
  forecast: number;
  partial: boolean;
  // Frozen vs unfrozen (2026-10-14): frozenForecast = the forecast when the week
  // started (null if not recorded / upcoming); liveForecast = today's forecast
  // for a running or upcoming week (null for completed weeks).
  liveForecast?: number | null;
  frozenForecast?: number | null;
  frozenMissing?: boolean; // page only: Frozen view, no frozen value recorded for this week
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
  frozenOn?: string; // date the active 2-month forecast freeze was taken
  frozenUntil?: string; // end of its window
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
  // Same-style spike (2026-10-13): only the styles that sold in BOTH months
  // (the current month: in the same days of the previous month).
  stylesBoth?: number;
  sameUnits?: number;
  samePrevUnits?: number;
  sameChangePct?: number | null;
  newStyles?: number; // sold this month, not the month before
  newUnits?: number;
  stoppedStyles?: number; // sold the month before, not this month
}

export interface CategoryAnalysisResponse {
  level: "category" | "subCategory" | "style" | "tier";
  dataThrough: string;
  subCategoryOf?: Record<string, string>; // style level only
  tierOf?: Record<string, string>; // tier level only: style -> tier
  partialMonth: string;
  rows: CategoryAnalysisRow[];
  filterOptions?: Record<string, string[]>; // tier level: {category: its sub categories}, for the filters
}

/** One Category Analysis row split by marketplace - rows' `group` is the marketplace. */
/** One style with its tier / category / sub category (Tiers Spike Rate chart filters). */
export interface CategoryStyleOption {
  style: string;
  brand: string;
  tier: string;
  category: string;
  subCategory: string;
}

/** GET /api/reports/category-analysis/filtered - monthly totals of the styles
 * matching all the filters (group = "selection"), plus every style's options. */
export interface CategoryAnalysisFilteredResponse {
  dataThrough: string;
  partialMonth: string;
  rows: CategoryAnalysisRow[];
  styleCount: number;
  options: CategoryStyleOption[];
}

export interface CategoryAnalysisChannelsResponse {
  level: "category" | "subCategory" | "style" | "tier";
  group: string;
  dataThrough: string;
  partialMonth: string;
  rows: CategoryAnalysisRow[];
}

/** One style behind a STYLES cell - GET /api/reports/category-analysis/styles. */
export interface CategoryStyleSale {
  style: string;
  subCategory: string;
  tier: string;
  units: number;
  orders: number;
  prevUnits: number; // the whole previous month
  new: boolean; // did not sell in the previous month
}

export interface CategoryAnalysisStylesResponse {
  month: string;
  prevMonth: string;
  partial: boolean;
  dataThrough: string;
  styles: CategoryStyleSale[];
  stopped: { style: string; subCategory: string; tier: string; prevUnits: number }[]; // sold last month, not this one
}

// --- Design Suggestions (GET /api/design/suggestions) ---------------------
export interface DesignRankItem {
  value: string;
  units: number;
  styles: number;
  share: number; // % of the season's units
  lift: number; // season share / usual share
  perStyle: number; // units per style (shrunk toward the average)
  perStyleRaw: number;
  momentum: number | null; // % vs same 8 weeks last year
  confidence: number; // 0..1
  score: number; // 0..100
  reasons: string;
  trend?: number | null; // share of the last 4 weeks' sales vs the 4 weeks before, %
  nowShare?: number; // % of the last 4 weeks' units
  activeStyles?: number; // styles with a sale in the last 4 weeks
  nowUnits?: number; // units in the last 4 weeks
  nowPerStyle?: number; // last 4 weeks' units per selling style (shrunk toward the average)
  lastSeason?: boolean; // false = not sold in last year's same season
  stage?: DesignStage | "none" | null; // trend stage (Category / Sub Category / Colour / Fabric); suggestions are trending / in trend only
  spike?: DesignSpike | null; // sudden jump in its share of sales in one report week (window.spike.week)
}
export interface DesignSpike {
  ratio: number | null; // share in the spike week / share of the 4 report weeks before (null = new)
  lastWeekUnits: number;
  lastWeekShare: number;
  usualShare: number;
  activeStyles: number;
  new: boolean;
}

export interface DesignDriver {
  attribute: string;
  value: string;
  effectPct: number; // model effect on units, %
  rows: number;
}

export type DesignLevel = "strong" | "good" | "trySmall";
/** One product (Sub Category) card on the Suggestion tab - every part from
 * that product's own ranking (api design_suggest._products). */
export interface DesignProductCard {
  subCategory: string;
  category: string; // its Category (e.g. Ethnic)
  stage: DesignStage | "none" | null;
  score: number;
  level: DesignLevel;
  nowShare: number; // % of all sales, last 4 weeks
  activeStyles: number;
  vsAverage: number | null; // a design of it sells this x an average design now
  lastSeason: boolean;
  lift: number; // share in this season last year vs its usual
  trend: number | null;
  use: {
    colour: { value: string; stage: DesignStage | "none" | null; trend: number | null }[];
    fabric: { value: string; stage: DesignStage | "none" | null; trend: number | null }[];
    details: Partial<Record<"embroidery" | "neck" | "sleeve" | "topType", string>>;
  };
  mixes: { colour: string; fabric: string; tried: boolean; level: DesignLevel; vsAveragePct: number }[];
  makeMore: { attribute: string; value: string; styles: number; times: number }[];
  cutBack: { attribute: string; value: string; reason: string; styles: number }[];
}

export interface DesignOpportunity {
  type: "gap" | "rising" | "declining" | "saturated";
  subCategory: string;
  attribute: "colour" | "fabric";
  value: string;
  styles: number;
  units: number;
  perStyle: number;
  subCategoryPerStyle: number;
  momentum: number | null;
  trend?: number | null; // share within the sub category, last 4 weeks vs the 4 before, %
  basis?: "now" | "lastSeason"; // what perStyle / subCategoryPerStyle measure
  score?: number; // the same score as in the ranked cards, within the sub category
}

/** Trend status of one Category / Sub Category / Colour / Fabric value. */
export type DesignStage = "trending" | "steady" | "fading" | "out";
export interface DesignLifecycleItem {
  value: string;
  stage: DesignStage;
  share: number[]; // % of sales in each 4-week block, oldest -> newest
  nowShare: number;
  change: number | null; // latest block vs the one before, %
  fromPeakPct: number | null; // latest share vs its best block, %
  peakBlock: number;
  nowUnits: number; // last block's units incl. the running week's expected rest
  realNowUnits?: number; // real pieces sold so far in the last block
  expectedUnits?: number; // the running week's expected rest (forecast x this week's pace)
  activeStyles: number;
  onSale: boolean;
  gain: number; // share points gained vs the block before
  spike?: DesignSpike | null; // sudden jump in its share of one report week (window.spike.week)
  styles?: [string, number, number][]; // [style, pieces in the latest block so far, pieces in the block before]
}
/** The report weeks behind every "now" number (Weekly Sales Report's W1-W4). */
export interface DesignWindow {
  runningWeek: string; // e.g. "Oct W2"
  from: string;
  to: string;
  actualThrough: string; // real sales up to this day; the rest of the week is its weekly forecast
  forecastDays: number;
  frozenStyles: number;
  estimatedStyles: number;
  realDays?: number;
  pace?: Record<string, number>; // per Sub Category: this week's real pace vs its forecast (1 = on forecast)
  spike?: { week: string; from: string; to: string; baseWeeks: string; includesForecast: boolean };
}
export interface DesignLifecycle {
  blocks: { from: string; to: string; weeks?: string }[];
  category?: DesignLifecycleItem[];
  subCategory?: DesignLifecycleItem[];
  colour?: DesignLifecycleItem[];
  fabric?: DesignLifecycleItem[];
  neck?: DesignLifecycleItem[];
  styleInfo?: Record<string, Record<"category" | "subCategory" | "colour" | "fabric" | "neck" | "tier" | "launchDate", string> & {
    daysSinceLaunch?: number | null; // days from launch to the data date, null if unknown
  }>; // each listed style's attributes
}

/** The one rule set every section of the page uses (api design_suggest.rules()). */
export interface DesignRules {
  trendWeeks: number; // report weeks (W1-W4 of each month) in the trend window
  momentumWeeks: number;
  trendStrong: number; // % share change that counts as rising / falling
  onSaleMinUnits: number;
  minStyles: number;
  gapRatio: number;
  saturatedRatio: number;
  saturatedMinStyles: number;
}

/** "Trending now" - the last 4 weeks vs the 4 weeks before. */
export interface DesignTrendingStyle {
  style: string;
  nowUnits: number; // real pieces sold so far in the last 4 report weeks
  expectedUnits?: number; // + the running week's expected rest
  beforeUnits: number; // real pieces in the 4 report weeks before
  growthPct: number | null;
  newLaunch: boolean;
  launchDate?: string; // ISO, "" if unknown
  subCategory: string;
  colour: string;
  fabric: string;
  neck: string;
  sleeve: string;
}
export interface DesignTrendingNow {
  windowWeeks: number;
  nowWeeks?: string; // e.g. "Sep W3 – Oct W2"
  nowFrom: string;
  nowTo: string;
  beforeFrom: string;
  beforeTo: string;
  beforeWeeks?: string;
  runningWeek?: string;
  styles: DesignTrendingStyle[];
}


export interface DesignSeason {
  role: "running" | "upcoming";
  season: string;
  label: string;
  months: string;
  occasion: string;
  start: string;
  end: string;
  reference: string;
  referenceCoverage: string;
  note?: string;
  rankings: Record<string, DesignRankItem[]>;
  opportunities: DesignOpportunity[];
  products?: DesignProductCard[];
  overview?: { focus: string[]; colours: string[]; fabrics: string[] };
  drivers: DesignDriver[];
  recommendedStyles?: DesignRecommendedStyle[];
  // The report weeks behind expectedSale; normally 9 fixed calendar report weeks.
  expectedSaleWindow?: {
    weeks?: number; from?: string; to?: string; start?: string; end?: string; unavailable?: boolean;
    running?: boolean; // the first week is the running one (has real sales so far)
    // The 9 report weeks, as the Weekly Sales Report shows them.
    weekList?: { start: string; end: string; month: string; label: string; eventCategory: string; eventName: string }[];
  };
}

/** An existing style carrying a product card's suggested features - most
 * matched features first, then the bigger expected sale. */
export interface DesignRecommendedStyle {
  rank: number;
  style: string;
  subCategory: string;
  category: string;
  subCategoryRank: number; // its sub-category card's position (0 = best)
  colour: string; // one of its card's suggested colours (required to be listed)
  colourRank: number; // that colour's position on the card (0 = best)
  colourStage?: DesignStage | null; // the colour's trend status
  expectedSale: number; // Weekly Sales Report forecast, running report week + next 8 report weeks (9 weeks)
  weekly?: number[]; // its forecast per week of expectedSaleWindow.weekList
  soldSoFar?: number | null; // real sales so far in the running week (null if it has not started)
  featureMatchCount: number;
  suggestedFeatureCount: number;
  matchedFeatures: Record<string, string>; // attribute -> the style's (suggested) value
  featureStages?: Record<string, DesignStage>; // Trend status of matched features that have one
  missingFeatures: string[]; // suggested attributes the style does not match
  styleFeatures?: Record<string, string>; // what the style has instead, per missing attribute
  featureFitScore: number;
  productScore: number;
  productLevel: DesignLevel;
}

export interface DesignSuggestionsResponse {
  available: boolean;
  building?: boolean;
  error?: string;
  dataThrough?: string;
  historyFrom?: string;
  measure?: string;
  tiers?: string[]; // catalog tiers included (T1, T2, T3)
  tierStyles?: number; // styles in those tiers with sales
  model?: {
    type: string;
    rows: number;
    styles: number;
    seasons: string[];
    holdoutWape: number;
    baselineWape: number;
    validation?: string;
    cvFolds?: number;
    cvFoldWape?: number[];
    params?: Record<string, string | number>;
    paramSetsTried?: number;
    topPickHitPct?: number | null; // % of predicted top-20% designs that really were top 20% (out-of-sample)
    allTiersWape?: number;
    trainingTiers?: string[];
    focusTiers?: string[];
    focusRows?: number;
    focusStyles?: number;
  } | null;
  trendingNow?: DesignTrendingNow;
  rules?: DesignRules;
  lifecycle?: DesignLifecycle;
  window?: DesignWindow;
  seasons: DesignSeason[];
}

/** GET /api/data-health - checks on the loaded data (2026-10-19). */
export interface DataHealthIssue {
  level: "error" | "warning" | "info";
  code: string;
  title: string;
  detail: string;
}
export interface DataHealthResponse {
  checkedOn: string;
  dataThrough: string;
  issues: DataHealthIssue[];
}
