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
