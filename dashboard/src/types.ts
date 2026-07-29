// --- SKU Production Plan ---------------------------------------------------- //
export interface PlanningRow {
  skuCode: string;
  designNo: string;
  date: string;
  forecast7: number;
  forecast10: number;
  forecast35: number;
  /** This SKU's parent design's own 5-week forecast (context only — DRR/reorder/production stay SKU-level). */
  designForecast35: number;
  inventoryQty: number;
  wipQty: number;
  availableQty: number;
  leadTimeDays: number;
  safetyStock: number;
  reorderPoint: number;
  totalSuggestedProduction: number;
  calculatedProductionSuggestion: number;
  stockStatus: string; // "In Stock" | "Reorder"
  historicalLast10d: number;
  vertical: string;
  price: number;
  tier: string;
  tierSuggestedProduction: number;
  tierPolicy: string;
  launchTier: string;
  currentTier: string;
  lifecycleStage: string;
  daysSinceLaunch: number;
  launchDate: string;
  launchDrr: number;
  currentDrr: number;
  growthRate: number;
  healthScore: number;
  riskScore: number;
  productionPriority: number;
  suggestedProduction: number;
  festivalEvent: string;
  festivalEventStart: string;
  festivalEventEnd: string;
  festivalQty: number;
  festivalUpliftPct: number;
}

export interface TopRegionRow {
  name: string;
  revenue10: number;
  revenue30: number;
  revenue90: number;
  units10: number;
  units30: number;
  units90: number;
}

export type TopWarehouseRow = TopRegionRow;

export interface SkuTopRegions {
  topStates: TopRegionRow[];
  topCities: TopRegionRow[];
  topWarehouses: TopWarehouseRow[];
}

export interface NewDesignRow {
  designNo: string;
  launchDate: string;
  daysSinceLaunch: number;
  skuCount: number;
}

export interface SimilarDesignRef {
  design: string;
  similarity: number;
  sharedMaterials: string[];
}

export interface NewDesignFestivalSpike {
  designNo: string;
  event: string;
  eventStart: string;
  eventEnd: string;
  predictedQty: number;
  upliftPct: number;
  similarDesigns: SimilarDesignRef[];
}

export interface HistoricalPoint {
  date: string;
  forecast: number;
  actual: number;
  variance: number;
  partial: boolean;
}

export interface ForecastPoint {
  date: string;
  qty: number;
  event: string | null;
}

export interface BreakdownResponse {
  sku: string;
  snapshotDate: string;
  periodWeeks: number;
  accuracyPct: number;
  overallAccuracyPct: number;
  historical: HistoricalPoint[];
  forecast: ForecastPoint[];
}

