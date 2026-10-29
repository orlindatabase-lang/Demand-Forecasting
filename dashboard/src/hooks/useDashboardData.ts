import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { dataService, NO_FILTER, type CategoryFilter } from "@/services/dataService";

const REFRESH_MS = 60_000;

export function useWeeklyGrid(
  groupBy: "subCategory" | "style",
  opts: { weeks?: number; limit?: number; offset?: number; search?: string; subCategory?: string; category?: string } = {},
) {
  const { weeks = 9, limit = 50, offset = 0, search = "", subCategory = "", category = "" } = opts;
  return useQuery({
    queryKey: ["weeklyGrid", groupBy, weeks, limit, offset, search, subCategory, category],
    queryFn: () => dataService.weeklyGrid(groupBy, { weeks, limit, offset, search, subCategory, category }),
    refetchInterval: REFRESH_MS,
    // Keep showing the last page's rows while a new limit/offset/filter
    // combination loads (2026-09-21, found while adding the tier filter -
    // switching it jumps the fetch size from 200 to 2000 rows, and without
    // this the whole page briefly blanked to a full loading skeleton on
    // every tier click instead of just the table refreshing in place).
    placeholderData: keepPreviousData,
  });
}

export function useChannelSourceWeekly(style: string | null, weeks = 9) {
  return useQuery({
    queryKey: ["channelSourceWeekly", style, weeks],
    queryFn: () => dataService.channelSourceWeekly(style as string, weeks),
    enabled: !!style,
  });
}

/** Same per-marketplace breakdown as useChannelSourceWeekly(), summed across
 * every Style currently matching the Weekly Sales Report's own filter — the
 * TOTAL row's own expandable channel breakdown. Only fetches while
 * ``enabled`` (the TOTAL row's own expand toggle) is true. */
export function useChannelSourceWeeklyTotal(
  enabled: boolean, weeks = 9, search = "", subCategory = "", category = "",
) {
  return useQuery({
    queryKey: ["channelSourceWeeklyTotal", weeks, search, subCategory, category],
    queryFn: () => dataService.channelSourceWeeklyTotal(weeks, search, subCategory, category),
    enabled,
  });
}

export function useCatalogStyleTiers() {
  return useQuery({
    queryKey: ["catalogStyleTiers"],
    queryFn: () => dataService.catalogStyleTiers(),
    refetchInterval: REFRESH_MS,
  });
}

/** Styles present last calendar year vs newly launched this one, for one
 * Sub Category — only fetches once a specific Sub Category is selected
 * (blank/"all" has nothing meaningful to show). */
export function useStyleLifecycle(subCategory: string) {
  return useQuery({
    queryKey: ["styleLifecycle", subCategory],
    queryFn: () => dataService.styleLifecycle(subCategory),
    enabled: !!subCategory,
  });
}

/** Production Log popup - only fetches while it's open. */
export function useWeeklyProductionLog(enabled: boolean) {
  return useQuery({
    queryKey: ["weeklyProductionLog"],
    queryFn: () => dataService.weeklyProductionLog(),
    refetchInterval: REFRESH_MS,
    enabled,
  });
}

/** Category Analysis page - one query per level (category / subCategory). */
export function useCategoryAnalysis(level: "category" | "subCategory" | "style" | "tier", f: CategoryFilter = NO_FILTER) {
  return useQuery({
    queryKey: ["categoryAnalysis", level, f.category, f.subCategory, f.tierMode ?? "current"],
    queryFn: () => dataService.categoryAnalysis(level, f),
    refetchInterval: 10 * REFRESH_MS,
    placeholderData: keepPreviousData,
  });
}

/** Tiers Spike Rate chart: monthly totals for the chosen Tier / Category /
 * Sub Category / Style (keeps showing the previous result while a new one loads). */
export function useCategoryAnalysisFiltered(f: { brand: string; tier: string; category: string; subCategory: string; style: string }) {
  return useQuery({
    queryKey: ["categoryAnalysisFiltered", f.brand, f.tier, f.category, f.subCategory, f.style],
    queryFn: () => dataService.categoryAnalysisFiltered(f),
    refetchInterval: 10 * REFRESH_MS,
    placeholderData: keepPreviousData,
  });
}

/** Channel (marketplace) drill-down of one Category Analysis row - fetched
 * only once that row is expanded. */
export function useCategoryAnalysisChannels(
  level: "category" | "subCategory" | "style" | "tier",
  group: string,
  f: CategoryFilter = NO_FILTER,
) {
  return useQuery({
    queryKey: ["categoryAnalysisChannels", level, group, f.category, f.subCategory, f.tierMode ?? "current"],
    queryFn: () => dataService.categoryAnalysisChannels(level, group, f),
    refetchInterval: 10 * REFRESH_MS,
  });
}

/** Design Suggestions tab. The API builds the result in the background on
 * first request (~1-2 min); poll every 5 s while it says "building". */
export function useDesignSuggestions() {
  return useQuery({
    queryKey: ["designSuggestions"],
    queryFn: () => dataService.designSuggestions(),
    refetchInterval: (query) => (query.state.data?.building ? 5_000 : 10 * REFRESH_MS),
  });
}
