import { apiGet, apiPost } from "./api";
import type {
  WeeklyGridResponse,
  ChannelSourceWeeklyResponse,
  CatalogStyleTiersResponse,
  StyleLifecycleResponse,
  WeeklyProductionLogRow,
  CategoryAnalysisChannelsResponse,
  CategoryAnalysisFilteredResponse,
  TierMovementResponse,
  CategoryAnalysisResponse,
  DesignSuggestionsResponse,
} from "@/types";

export interface AdminRefreshResponse {
  status: string;
  source: string; // "live" | "csv" | "mock"
  rows: number;
  snapshot: string;
  forecastModel: string;
  forecastSnapshot: string; // snapshot the served model was trained on ("" = none)
  modelTraining: boolean;
  lgbmPending: boolean;
}

/** Category / Sub Category filter of the Tiers Spike Rate table (blank = all). */
export interface CategoryFilter {
  category: string;
  subCategory: string;
  tierMode?: "current" | "sale"; // tier level: current tier, or the tier at the time of sale
}
export const NO_FILTER: CategoryFilter = { category: "", subCategory: "" };

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

  /** Weekly production log: per Style and report week, the forecast and
   * ~2-month suggested production captured at week start, plus the actual
   * sale once the week finished (latest 8 completed weeks + the running one). */
  async weeklyProductionLog() {
    return apiGet<WeeklyProductionLogRow[]>("/api/reports/weekly-production-log");
  },

  /** Monthly styles / orders / units / order spike per Category or Sub
   * Category, April 2025 to now - the Category Analysis page. */
  async categoryAnalysis(level: "category" | "subCategory" | "style" | "tier", f: CategoryFilter = NO_FILTER) {
    const params = new URLSearchParams({ level, category: f.category, subCategory: f.subCategory, tierMode: f.tierMode ?? "current" });
    return apiGet<CategoryAnalysisResponse>(`/api/reports/category-analysis?${params.toString()}`);
  },

  /** Monthly totals for the styles matching Tier / Category / Sub Category /
   * Style together - the Tiers Spike Rate chart. */
  async categoryAnalysisFiltered(f: { brand: string; tier: string; category: string; subCategory: string; style: string }) {
    const params = new URLSearchParams({ brand: f.brand, tier: f.tier, category: f.category, subCategory: f.subCategory, style: f.style });
    return apiGet<CategoryAnalysisFilteredResponse>(`/api/reports/category-analysis/filtered?${params.toString()}`);
  },

  /** How long styles stay in each catalog tier and where they move (Tier movement tab). */
  async tierMovement() {
    return apiGet<TierMovementResponse>("/api/catalog/tier-movement");
  },

  /** One Category Analysis row split by marketplace (channel drill-down). */
  async categoryAnalysisChannels(level: "category" | "subCategory" | "style" | "tier", group: string, f: CategoryFilter = NO_FILTER) {
    const params = new URLSearchParams({ level, group, category: f.category, subCategory: f.subCategory, tierMode: f.tierMode ?? "current" });
    return apiGet<CategoryAnalysisChannelsResponse>(`/api/reports/category-analysis/channels?${params.toString()}`);
  },

  /** Season-wise design suggestions (running + upcoming season). */
  async designSuggestions() {
    return apiGet<DesignSuggestionsResponse>("/api/design/suggestions");
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
