import { apiGet, apiPost } from "./api";
import type {
  WeeklyGridResponse,
  ChannelSourceWeeklyResponse,
  CatalogStyleTiersResponse,
  StyleLifecycleResponse,
} from "@/types";

export interface AdminRefreshResponse {
  status: string;
  source: string; // "live" | "csv" | "mock"
  rows: number;
  snapshot: string;
  forecastModel: string;
  lgbmPending: boolean;
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
  /** Forecasted + Actual sales pivoted by week/month, one row per Sub
   * Category or Style — the Weekly Sales Report page's data source. */
  async weeklyGrid(
    groupBy: "subCategory" | "style",
    opts: {
      weeks?: number; limit?: number; offset?: number; search?: string;
      subCategory?: string; category?: string;
    } = {},
  ) {
    // 9 weeks =~ 2 months forward horizon (2026-09-14, user-requested; matches
    // the same ~30.4-day/month rounding this endpoint's 13-week/~3-month cap
    // already used elsewhere).
    const { weeks = 9, limit = 50, offset = 0, search = "", subCategory = "", category = "" } = opts;
    const params = new URLSearchParams({
      groupBy,
      weeks: String(weeks),
      limit: String(limit),
      offset: String(offset),
    });
    if (search.trim()) params.set("search", search.trim());
    if (subCategory.trim()) params.set("subCategory", subCategory.trim());
    if (category.trim()) params.set("category", category.trim());
    return apiGet<WeeklyGridResponse>(`/api/reports/weekly-grid?${params.toString()}`);
  },

  /** Finished-goods inventory planning by design. */
  async inventoryPlanning(limit = 500) {
    return apiGet<InventoryPlanningResponse>(`/api/inventory/planning?limit=${limit}`);
  },

  /** Per-marketplace week-wise Gross Sale for one Style, on the same week
   * axis as the Weekly Sales Report's own cells — the inline expandable-row
   * drill-down behind the Demand Forecasting page's per-Style rows. */
  async channelSourceWeekly(style: string, weeks = 9) {
    return apiGet<ChannelSourceWeeklyResponse>(
      `/api/reports/channel-source/${encodeURIComponent(style)}/weekly?weeks=${weeks}`,
    );
  },

  /** Same per-marketplace breakdown as channelSourceWeekly(), summed across
   * EVERY Style currently matching the Weekly Sales Report's own search/Sub
   * Category filter — the TOTAL row's own expandable channel breakdown. */
  async channelSourceWeeklyTotal(weeks = 9, search = "", subCategory = "", category = "") {
    const params = new URLSearchParams({ weeks: String(weeks) });
    if (search.trim()) params.set("search", search.trim());
    if (subCategory.trim()) params.set("subCategory", subCategory.trim());
    if (category.trim()) params.set("category", category.trim());
    return apiGet<ChannelSourceWeeklyResponse>(
      `/api/reports/channel-source-total/weekly?${params.toString()}`,
    );
  },

  /** Every active catalog Style's image + externally-maintained forecast
   * tier (Cloud SQL CatalogStyle.forecastStatus), grouped by tier — powers
   * the Style Tier Gallery page. */
  async catalogStyleTiers() {
    return apiGet<CatalogStyleTiersResponse>("/api/catalog/style-tiers");
  },

  /** Styles present (sold) last calendar year vs newly launched this one,
   * for one Sub Category — the Weekly Sales Report's style-lifecycle panel
   * (2026-09-22, user-requested). */
  async styleLifecycle(subCategory: string) {
    const params = new URLSearchParams({ subCategory });
    return apiGet<StyleLifecycleResponse>(`/api/catalog/style-lifecycle?${params.toString()}`);
  },

  /** Re-fetch the source data (live BigQuery + ERP, or CSV) and rebuild every
   * in-memory table — the "Refresh" button in the top bar. Does not restart
   * the actual API process (that's watchdog-managed); this is the safe,
   * no-downtime equivalent an admin can trigger from the UI at any time. */
  async adminRefresh() {
    return apiPost<AdminRefreshResponse>("/admin/refresh");
  },
};
