import { apiGet } from "./api";
import type {
  PlanningRow,
  TopRegionRow,
  TopWarehouseRow,
  BreakdownResponse,
  NewDesignRow,
} from "@/types";

interface PlanResponse {
  total: number;
  items: PlanningRow[];
  newDesignCount: number;
}

interface NewDesignsResponse {
  total: number;
  items: NewDesignRow[];
}

// --- Inhouse / FOB (shared row shape) ---------------------------------------- //
export interface IHFRow {
  lotNo: string;
  design: string;
  section: string;
  vendor: string;
  process: string;
  issueQty: number;
  issueDate: string;
  receiveQty: number;
  receiveDate: string;
  balMtr: number;
  ageDays: number;
  riskLevel: string; // "Delayed" | "At Risk" | "On Track" | "Completed"
  expectedDays: number;
  lotStatus: string; // "Open" | "Completed"
  delayProb: number | null;
  riskBand: string | null;
  alreadyLate: boolean;
  debitNotes?: { voucherNo: string; voucherDate: string; partyName: string; article: string; qty: number; rate: number; amount: number; netAmount: number }[];
  debitNoteCount?: number;
  debitNoteAmount?: number;
}

export interface IHFResponse {
  available: boolean;
  total: number;
  delayed: number;
  atRisk: number;
  onTrack: number;
  completed: number;
  processes: string[];
  counts: Record<string, number>;
  asOf: string;
  items: IHFRow[];
}

// --- Job Work ----------------------------------------------------------------- //
export interface JWRawRow {
  lotNo: string;
  design: string;
  section: string;
  vendor: string;
  process: string;
  issueQty: number;
  issueDate: string;
  receiveQty: number;
  receiveDate: string;
  pending: number;
  ageDays: number;
  riskLevel: string;
  expectedDays: number;
  lotStatus: string;
  delayProb: number | null;
  riskBand: string | null;
  alreadyLate: boolean;
}

export interface JWFlatResponse {
  available: boolean;
  total: number;
  processes: string[];
  counts: Record<string, number>;
  asOf: string;
  items: JWRawRow[];
}

// --- Purchase Order ------------------------------------------------------------ //
export interface POLot {
  lotNo: string;
  design: string;
  section: string;
  articleGroup: string;
  vendor: string;
  issueQty: number;
  issueDate: string;
  receiveQty: number;
  receiveDate: string;
  pendingQty: number;
  fcmStatus: string;
  fcmQty: number;
  allocQty: number;
  allocDate: string;
  estDelivery: string;
  delayStatus: string; // "On Track" | "At Risk" | "Delayed" | "Received"
  riskLevel: string; // "Delayed" | "At Risk" | "On Track" | "Completed"
  lotStatus: string;
  ageDays: number;
  delayProb: number | null;
  riskBand: string | null;
  alreadyLate: boolean;
}

export interface POResponse {
  available: boolean;
  total: number;
  items: POLot[];
}

// --- Embroidery ----------------------------------------------------------------- //
export interface EmbLot {
  lotNo: string;
  design: string;
  section: string;
  vendor: string;
  issueQty: number;
  issueDate: string;
  receiveQty: number;
  receiveDate: string;
  pendingQty: number;
  ageDays: number;
  riskLevel: string;
  lotStatus: string;
  delayProb: number | null;
  riskBand: string | null;
  alreadyLate: boolean;
}

export interface EmbResponse {
  available: boolean;
  total: number;
  over30: number;
  delayed: number;
  atRisk: number;
  onTrack: number;
  completed: number;
  asOf: string;
  items: EmbLot[];
}

// --- Bottleneck ------------------------------------------------------------------ //
export interface BottleneckLot {
  lotNo: string;
  design: string;
  process: string;
  section: string;
  vendor: string;
  riskLevel: string;
  ageDays: number;
  expectedDays: number;
  overrunDays: number;
  issueQty: number;
  pendingPieces: number;
  issueDate: string;
  delayProb: number | null;
  riskBand: string | null;
  /** This design's summed SKU-level daily run-rate (units/day, all sizes). */
  designDrr: number;
  /** This design's summed 35-day forecast (units, all sizes). */
  designForecast35: number;
  /** Per-LOT priority score: designDrr × this lot's own pendingPieces. */
  lotDemandImpact: number;
}

export interface BottleneckProcessStats {
  process: string;
  severity: string; // High | Medium | Low
  bottleneckScore: number;
  openLots: number;
  delayedLots: number;
  atRiskLots: number;
  onTrackLots: number;
  pendingPieces: number;
  avgAgeDays: number;
  avgExpectedDays: number;
  avgOverrunDays: number;
  delayRate: number;
}

export interface BottleneckResponse {
  available: boolean;
  asOf: string;
  worstProcess: string | null;
  processes: BottleneckProcessStats[];
  lots: BottleneckLot[];
}

// --- Inventory Planning ----------------------------------------------------------- //
export interface InventoryPlanningRow {
  design: string;
  size: string;
  currentStock: number;
  dailyRunRate: number;
  daysToFinish: number;
}

export interface InventoryPlanningResponse {
  available: boolean;
  total: number;
  items: InventoryPlanningRow[];
}

export const dataService = {
  /** Main SKU Production Plan + top-selling tables (parallel fetch). */
  async planningTables() {
    const [plan, topStates, topCities, topWarehouses] = await Promise.all([
      apiGet<PlanResponse>("/api/sku-production-plan?limit=1000"),
      apiGet<TopRegionRow[]>("/api/top-states"),
      apiGet<TopRegionRow[]>("/api/top-cities"),
      apiGet<TopWarehouseRow[]>("/api/top-warehouses"),
    ]);
    return {
      planningRows: plan.items,
      newDesignCount: plan.newDesignCount,
      topStates,
      topCities,
      topWarehouses,
    };
  },

  /** All newly-launched designs (not just the count) for the KPI drill-down. */
  async newDesigns(maxAgeDays = 90) {
    return apiGet<NewDesignsResponse>(`/api/new-designs?maxAgeDays=${maxAgeDays}`);
  },

  /** Server-side SKU/design search across the FULL plan (not just the
   * top-1000-by-forecast page the table fetches by default) — otherwise
   * low-forecast rows (e.g. newly-launched designs) are unsearchable. */
  async searchPlan(query: string) {
    const plan = await apiGet<PlanResponse>(
      `/api/sku-production-plan?search=${encodeURIComponent(query)}&limit=1000`,
    );
    return plan.items;
  },

  /** Per-SKU week-wise breakdown for the drill-down popup. */
  async skuBreakdown(sku: string, weeks: number) {
    return apiGet<BreakdownResponse>(
      `/api/sku-production-plan/${encodeURIComponent(sku)}/breakdown?weeks=${weeks}`,
    );
  },

  /** Inhouse production rows (Cutting/Stitching/Thread Cutting/General Store/Barcode). */
  async inhouseLots(limit = 5000, process?: string) {
    const qs = process ? `&process=${encodeURIComponent(process)}` : "";
    return apiGet<IHFResponse>(`/api/production/inhouse?limit=${limit}${qs}`);
  },

  /** Job Work flat rows: one row per process event, optional process filter. */
  async jobWorkLots(limit = 5000, process?: string) {
    const qs = process ? `&process=${encodeURIComponent(process)}` : "";
    return apiGet<JWFlatResponse>(`/api/production/job-work?limit=${limit}${qs}`);
  },

  /** Purchase Order lots: Issue / Receive / FCM / Allocation per lot. */
  async purchaseOrderLots(limit = 5000) {
    return apiGet<POResponse>(`/api/production/purchase-order?limit=${limit}`);
  },

  /** Embroidery lots (Issue enriched with GRN receive data). */
  async embroideryLots(limit = 5000) {
    return apiGet<EmbResponse>(`/api/production/embroidery?limit=${limit}`);
  },

  /** FOB production rows (FOB Issue enriched with FOB Receive data). */
  async fobLots(limit = 5000) {
    return apiGet<IHFResponse>(`/api/production/fob?limit=${limit}`);
  },

  /** Per-process bottleneck ranking (Inhouse + Job Work combined). */
  async bottlenecks() {
    return apiGet<BottleneckResponse>("/api/production/bottlenecks");
  },

  /** Finished-goods inventory planning by design. */
  async inventoryPlanning(limit = 500) {
    return apiGet<InventoryPlanningResponse>(`/api/inventory/planning?limit=${limit}`);
  },
};
