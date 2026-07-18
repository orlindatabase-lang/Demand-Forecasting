// --- SKU Production Plan ---------------------------------------------------- //
export interface PlanningRow {
  skuCode: string;
  designNo: string;
  date: string;
  forecast7: number;
  forecast10: number;
  forecast35: number;
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
  weekStart: string;
  predictedQty: number;
  upliftPct: number;
  similarDesigns: SimilarDesignRef[];
}

export interface VerticalRollupRow {
  vertical: string;
  designCount: number;
  newDesignCount: number;
  skuCount: number;
  forecast7: number;
  forecast10: number;
  forecast35: number;
  inventoryQty: number;
  wipQty: number;
  availableQty: number;
  calculatedProductionSuggestion: number;
}

export interface VerticalTopDownWeek {
  weekStart: string;
  forecastQty: number;
  event: string | null;
}

export interface VerticalTopDownForecast {
  vertical: string;
  weeklyHistoryAvg: number;
  forecast: VerticalTopDownWeek[];
  bottomUpForecast35: number;
  topDownForecast35: number;
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
