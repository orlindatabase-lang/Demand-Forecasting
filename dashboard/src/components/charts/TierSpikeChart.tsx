import { useMemo } from "react";
import { Typography } from "@mui/material";
import CategoryAnalysisCharts, { type ChartCell } from "./CategoryAnalysisCharts";
import { useCategoryAnalysisFiltered } from "@/hooks/useDashboardData";
import type { StyleFilters } from "@/components/common/StyleFilterBar";

/** The Tiers Spike Rate chart (2026-10-13, user-requested): monthly totals
 * summed on the server for exactly the styles matching the page's Brand /
 * Tier / Category / Sub Category / Style filters (StyleFilterBar, shared with
 * the table since 2026-10-30; /api/category-analysis/chart). */
export default function TierSpikeChart({ months, partialMonth, filters }: { months: string[]; partialMonth: string; filters: StyleFilters }) {
  const { data } = useCategoryAnalysisFiltered(filters);

  const totals: Record<string, ChartCell> = useMemo(() => {
    const out: Record<string, ChartCell> = {};
    for (const r of data?.rows ?? []) out[r.month] = { units: r.units, prevUnits: r.prevUnits, changePct: r.changePct, sameChangePct: r.sameChangePct };
    return out;
  }, [data]);

  return (
    <CategoryAnalysisCharts
      months={months}
      groups={[]}
      totals={totals}
      partialMonth={data?.partialMonth || partialMonth}
      groupName="Tier"
      filters={
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600 }}>
          {[filters.brand, filters.tier, filters.category, filters.subCategory, filters.style].filter(Boolean).join(" · ") || "All styles"}
        </Typography>
      }
    />
  );
}
