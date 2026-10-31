import { apiGet, apiPost } from "./api";
import type {
  WeeklyGridResponse,
  ChannelSourceWeeklyResponse,
  CatalogStyleTiersResponse,
  StyleLifecycleResponse,
  WeeklyProductionLogRow,
  CategoryAnalysisChannelsResponse,
  CategoryAnalysisStylesResponse,
  CategoryAnalysisFilteredResponse,
  CategoryAnalysisResponse,
  DesignSuggestionsResponse,
  DataHealthResponse,
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
/** Weekly Sales Report forward horizon: 13 report weeks =~ 3 months, so the
 * running month plus the next 3 (e.g. through January in late October;
 * 2026-10-30, user-requested - was 9 weeks / 2 months). Suggested Production
 * covers the same weeks. */
export const FORECAST_WEEKS = 13;

export interface CategoryFilter {
  category: string;
  subCategory: string;
  tierMode?: "current" | "sale"; // tier level: current tier, or the tier at the time of sale
  brand?: string;
  tier?: string; // the style's current catalog tier
  style?: string;
}

/** Query params of a CategoryFilter (blank = any). */
function filterParams(f: CategoryFilter) {
  return {
    category: f.category, subCategory: f.subCategory, tierMode: f.tierMode ?? "current",
    brand: f.brand ?? "", tier: f.tier ?? "", style: f.style ?? "",
  };
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
    const { weeks = FORECAST_WEEKS, limit = 50, offset = 0, search = "", subCategory = "", category = "" } = opts;
    const params = new URLSearchParams({
      groupBy,
      weeks: String(weeks),
      limit: String(limit),
      offset: String(offset),
    });
    if (search.trim()) params.set("search", search.trim());
    if (subCategory.trim()) params.set("subCategory", subCategory.trim());
    if (category.trim()) params.set("category", category.trim());
    return apiGet<WeeklyGridResponse>(`/api/weekly-sales?${params.toString()}`);
  },

  /** Weekly production log: per Style and report week, the forecast and
   * ~2-month suggested production captured at week start, plus the actual
   * sale once the week finished (latest 8 completed weeks + the running one). */
  async weeklyProductionLog() {
    return apiGet<WeeklyProductionLogRow[]>("/api/production-log");
  },

  /** Monthly styles / orders / units / order spike per Category or Sub
   * Category, April 2025 to now - the Category Analysis page. */
  async categoryAnalysis(level: "category" | "subCategory" | "style" | "tier", f: CategoryFilter = NO_FILTER) {
    const params = new URLSearchParams({ level, ...filterParams(f) });
    return apiGet<CategoryAnalysisResponse>(`/api/category-analysis?${params.toString()}`);
  },

  /** Monthly totals for the styles matching Tier / Category / Sub Category /
   * Style together - the Tiers Spike Rate chart. */
  async categoryAnalysisFiltered(f: { brand: string; tier: string; category: string; subCategory: string; style: string }) {
    const params = new URLSearchParams({ brand: f.brand, tier: f.tier, category: f.category, subCategory: f.subCategory, style: f.style });
    return apiGet<CategoryAnalysisFilteredResponse>(`/api/category-analysis/chart?${params.toString()}`);
  },

  /** One Category Analysis row split by marketplace (channel drill-down). */
  async categoryAnalysisChannels(level: "category" | "subCategory" | "style" | "tier", group: string, f: CategoryFilter = NO_FILTER) {
    const params = new URLSearchParams({ level, group, ...filterParams(f) });
    return apiGet<CategoryAnalysisChannelsResponse>(`/api/category-analysis/channels?${params.toString()}`);
  },

  /** The styles behind one STYLES cell (group "" = TOTAL row, channel = a marketplace sub-row). */
  async categoryAnalysisStyles(level: "category" | "subCategory" | "style" | "tier", group: string, month: string, channel = "", f: CategoryFilter = NO_FILTER) {
    const params = new URLSearchParams({ level, group, month, channel, ...filterParams(f) });
    return apiGet<CategoryAnalysisStylesResponse>(`/api/category-analysis/styles?${params.toString()}`);
  },

  /** Season-wise design suggestions (running + upcoming season). */
  async designSuggestions() {
    return apiGet<DesignSuggestionsResponse>("/api/design-suggestions");
  },

  /** Per-marketplace week-wise Gross Sale for one Style, on the same week
   * axis as the Weekly Sales Report's own cells — the inline expandable-row
   * drill-down behind the Demand Forecasting page's per-Style rows. */
  async channelSourceWeekly(style: string, weeks = FORECAST_WEEKS) {
    return apiGet<ChannelSourceWeeklyResponse>(
      `/api/weekly-sales/channels/${encodeURIComponent(style)}?weeks=${weeks}`,
    );
  },

  /** Same per-marketplace breakdown as channelSourceWeekly(), summed across
   * EVERY Style currently matching the Weekly Sales Report's own search/Sub
   * Category filter — the TOTAL row's own expandable channel breakdown. */
  async channelSourceWeeklyTotal(weeks = FORECAST_WEEKS, search = "", subCategory = "", category = "") {
    const params = new URLSearchParams({ weeks: String(weeks) });
    if (search.trim()) params.set("search", search.trim());
    if (subCategory.trim()) params.set("subCategory", subCategory.trim());
    if (category.trim()) params.set("category", category.trim());
    return apiGet<ChannelSourceWeeklyResponse>(
      `/api/weekly-sales/channels?${params.toString()}`,
    );
  },

  /** Every active catalog Style's image + externally-maintained forecast
   * tier (Cloud SQL CatalogStyle.forecastStatus), grouped by tier — powers
   * the Style Tier Gallery page. */
  async catalogStyleTiers() {
    return apiGet<CatalogStyleTiersResponse>("/api/style-tiers");
  },

  /** Styles present (sold) last calendar year vs newly launched this one,
   * for one Sub Category — the Weekly Sales Report's style-lifecycle panel
   * (2026-09-22, user-requested). */
  async styleLifecycle(subCategory: string) {
    const params = new URLSearchParams({ subCategory });
    return apiGet<StyleLifecycleResponse>(`/api/style-lifecycle?${params.toString()}`);
  },

  /** Re-fetch the source data (live BigQuery + ERP, or CSV) and rebuild every
   * in-memory table — the "Refresh" button in the top bar. Does not restart
   * the actual API process (that's watchdog-managed); this is the safe,
   * no-downtime equivalent an admin can trigger from the UI at any time. */
  /** Data checks: failed ERP stock / WIP, incomplete or stale sales, a sale
   * spike inside the run-rate, calendar mismatches, low accuracy, missing
   * master data (2026-10-19). */
  async dataHealth() {
    return apiGet<DataHealthResponse>("/api/data-health");
  },

  async adminRefresh() {
    return apiPost<AdminRefreshResponse>("/admin/refresh");
  },
};
