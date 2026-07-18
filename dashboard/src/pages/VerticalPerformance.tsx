import { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import type { ColumnDef } from "@tanstack/react-table";
import PageHeader from "@/components/common/PageHeader";
import DataTable from "@/components/tables/DataTable";
import ExportButton from "@/components/common/ExportButton";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { useVerticalRollup, useVerticalTopDownForecast } from "@/hooks/useDashboardData";
import type { VerticalRollupRow, VerticalTopDownForecast } from "@/types";
import { formatNumber } from "@/utils/format";

function TopDownTable({ items }: { items: VerticalTopDownForecast[] }) {
  const columns = useMemo<ColumnDef<VerticalTopDownForecast, any>[]>(
    () => [
      { accessorKey: "vertical", header: "Vertical", cell: (c) => <strong>{c.getValue()}</strong>, size: 180 },
      {
        accessorKey: "bottomUpForecast35",
        header: "Bottom-Up (5wk)",
        cell: (c) => formatNumber(c.getValue()),
        size: 130,
      },
      {
        accessorKey: "topDownForecast35",
        header: "Top-Down (5wk)",
        cell: (c) => formatNumber(c.getValue()),
        size: 130,
      },
      {
        id: "delta",
        header: "Delta",
        size: 110,
        cell: ({ row }) => {
          const { bottomUpForecast35, topDownForecast35 } = row.original;
          const delta = topDownForecast35 - bottomUpForecast35;
          const pct = bottomUpForecast35 > 0 ? Math.round((delta / bottomUpForecast35) * 100) : 0;
          const color = Math.abs(pct) <= 10 ? "text.secondary" : delta > 0 ? "#22c55e" : "#ef4444";
          return (
            <Typography variant="body2" sx={{ fontWeight: 700, color }}>
              {delta >= 0 ? "+" : ""}
              {formatNumber(delta)} ({pct >= 0 ? "+" : ""}
              {pct}%)
            </Typography>
          );
        },
      },
      {
        id: "nextWeek",
        header: "Next Week (Top-Down)",
        size: 180,
        cell: ({ row }) => {
          const wk = row.original.forecast[0];
          if (!wk) return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
          return (
            <Typography variant="body2">
              {formatNumber(wk.forecastQty)} units
              {wk.event && (
                <Typography component="span" variant="caption" sx={{ color: "#f97316", ml: 0.75 }}>
                  {wk.event}
                </Typography>
              )}
            </Typography>
          );
        },
      },
    ],
    [],
  );

  return (
    <DataTable
      title="Bottom-Up vs Top-Down Forecast"
      data={items}
      columns={columns}
      initialPageSize={10}
      globalSearchPlaceholder="Search vertical…"
    />
  );
}

export default function VerticalPerformance() {
  const { data, isLoading, isError, refetch } = useVerticalRollup();
  const { data: topDown } = useVerticalTopDownForecast();

  const columns = useMemo<ColumnDef<VerticalRollupRow, any>[]>(
    () => [
      { accessorKey: "vertical", header: "Vertical", cell: (c) => <strong>{c.getValue()}</strong>, size: 180 },
      { accessorKey: "designCount", header: "Designs", cell: (c) => formatNumber(c.getValue()), size: 90 },
      { accessorKey: "newDesignCount", header: "New (90d)", cell: (c) => formatNumber(c.getValue()), size: 95 },
      { accessorKey: "skuCount", header: "SKUs", cell: (c) => formatNumber(c.getValue()), size: 90 },
      { accessorKey: "forecast7", header: "Forecast (1wk)", cell: (c) => formatNumber(c.getValue()), size: 115 },
      { accessorKey: "forecast35", header: "Forecast (5wk/35d)", cell: (c) => formatNumber(c.getValue()), size: 130 },
      { accessorKey: "inventoryQty", header: "Inventory", cell: (c) => formatNumber(c.getValue()), size: 100 },
      { accessorKey: "wipQty", header: "WIP", cell: (c) => formatNumber(c.getValue()), size: 85 },
      { accessorKey: "availableQty", header: "Available", cell: (c) => formatNumber(c.getValue()), size: 100 },
      {
        accessorKey: "calculatedProductionSuggestion",
        header: "Produce Now",
        size: 110,
        cell: (c) => {
          const v = c.getValue() as number;
          return v > 0 ? (
            <Box component="span" sx={{ fontWeight: 700, color: "#f97316" }}>
              {formatNumber(v)}
            </Box>
          ) : (
            <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>
          );
        },
      },
    ],
    [],
  );

  if (isLoading) return <LoadingSkeleton variant="page" />;
  if (isError || !data) return <ErrorState onRetry={() => refetch()} />;

  const items = data.items;

  return (
    <Box>
      <PageHeader
        title="Vertical Performance"
        subtitle="Forecast, inventory & production rolled up by product vertical (grouped from Design Group)"
      />
      <DataTable
        title="Verticals"
        data={items}
        columns={columns}
        initialPageSize={10}
        globalSearchPlaceholder="Search vertical…"
        toolbarActions={<ExportButton rows={items as unknown as Record<string, unknown>[]} filename="vertical_performance" />}
      />

      {topDown && (
        <Stack sx={{ mt: 3 }}>
          <Typography variant="caption" sx={{ color: "text.secondary", mb: 1 }}>
            The top-down series independently forecasts each vertical's TOTAL demand from its aggregated
            (much less noisy) weekly sales history, rather than summing individual SKU forecasts. It's shown
            here for comparison only — it does not feed into or replace the per-SKU forecasts, safety stock,
            or production suggestions above.
          </Typography>
          <TopDownTable items={topDown.items} />
        </Stack>
      )}
    </Box>
  );
}
