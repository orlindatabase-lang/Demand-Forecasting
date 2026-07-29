import { useMemo, useState } from "react";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import type { ColumnDef } from "@tanstack/react-table";
import PageHeader from "@/components/common/PageHeader";
import DataTable from "@/components/tables/DataTable";
import ExportButton from "@/components/common/ExportButton";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { usePlanningTables, usePlanSearch } from "@/hooks/useDashboardData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { PlanningRow, TopRegionRow } from "@/types";
import { formatNumber } from "@/utils/format";
import PlanDetailDrawer from "@/components/common/PlanDetailDrawer";
import NewDesignsDialog from "@/components/common/NewDesignsDialog";
import FestivalSpikeBadge from "@/components/common/FestivalSpikeBadge";

const STATUS_COLOR: Record<string, string> = {
  "In Stock": "#22c55e",
  Reorder: "#ef4444",
};

function TopTable({ title, rows }: { title: string; rows: TopRegionRow[] }) {
  const columns = useMemo<ColumnDef<TopRegionRow, any>[]>(
    () => [
      { accessorKey: "name", header: title, size: 160 },
      { accessorKey: "units30", header: "Units (30d)", cell: (c) => formatNumber(c.getValue()), size: 100 },
      {
        accessorKey: "revenue30",
        header: "Revenue (30d)",
        cell: (c) => `₹${formatNumber(c.getValue())}`,
        size: 120,
      },
    ],
    [title],
  );
  return (
    <DataTable
      title={title}
      data={rows}
      columns={columns}
      initialPageSize={5}
      globalSearchPlaceholder={`Search ${title.toLowerCase()}…`}
    />
  );
}

const NEW_DESIGN_MAX_AGE_DAYS = 90;

export default function Dashboard() {
  const { data, isLoading, isError, refetch, isFetching } = usePlanningTables();
  const [selectedSku, setSelectedSku] = useState<string | null>(null);
  const [newDesignsOpen, setNewDesignsOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const debouncedSearch = useDebouncedValue(searchText, 300);
  const { data: searchResults, isFetching: isSearching } = usePlanSearch(debouncedSearch);

  const columns = useMemo<ColumnDef<PlanningRow, any>[]>(
    () => [
      {
        accessorKey: "skuCode",
        header: "SKU",
        cell: ({ row }) => (
          <Typography
            variant="body2"
            sx={{ fontWeight: 700, cursor: "pointer", color: "primary.main" }}
            onClick={() => setSelectedSku(row.original.skuCode)}
          >
            {row.original.skuCode}
          </Typography>
        ),
        size: 130,
      },
      { accessorKey: "designNo", header: "Design No", size: 110 },
      {
        accessorKey: "vertical",
        header: "Vertical",
        cell: (c) => {
          const v = c.getValue() as string;
          if (!v || v === "Unclassified") {
            return <Typography variant="body2" sx={{ color: "text.disabled" }}>Unclassified</Typography>;
          }
          return <Typography variant="body2">{v}</Typography>;
        },
        size: 150,
      },
      {
        accessorKey: "forecast35",
        header: "Forecast (5wk / 35d)",
        cell: ({ row }) => (
          <Typography
            variant="body2"
            sx={{
              fontWeight: 700,
              cursor: "pointer",
              color: "primary.main",
              textDecoration: "underline",
              textDecorationStyle: "dotted",
            }}
            onClick={() => setSelectedSku(row.original.skuCode)}
          >
            {formatNumber(row.original.forecast35)}
          </Typography>
        ),
        size: 130,
      },
      {
        id: "festivalSpike",
        header: "Predicted Festival Spike",
        cell: ({ row }) => {
          const r = row.original;
          if (!r.festivalEvent) {
            return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
          }
          return (
            <FestivalSpikeBadge
              event={r.festivalEvent}
              eventStart={r.festivalEventStart}
              eventEnd={r.festivalEventEnd}
              qty={r.festivalQty}
              upliftPct={r.festivalUpliftPct}
            />
          );
        },
        size: 220,
      },
      {
        accessorKey: "currentDrr",
        header: "DRR (units/day)",
        cell: (c) => (c.getValue() as number).toFixed(2),
        size: 120,
      },
      {
        accessorKey: "inventoryQty",
        header: "Inventory",
        cell: (c) => formatNumber(c.getValue()),
        size: 95,
      },
      { accessorKey: "wipQty", header: "WIP", cell: (c) => formatNumber(c.getValue()), size: 85 },
      {
        accessorKey: "availableQty",
        header: "Available",
        cell: (c) => formatNumber(c.getValue()),
        size: 95,
      },
      {
        accessorKey: "stockStatus",
        header: "Status",
        cell: (c) => {
          const v = c.getValue() as string;
          const color = STATUS_COLOR[v] ?? "#94a3b8";
          return (
            <Box
              component="span"
              sx={{
                fontSize: "0.72rem",
                fontWeight: 700,
                color,
                bgcolor: color + "1e",
                border: `1px solid ${color}55`,
                borderRadius: 1,
                px: 1,
                py: 0.25,
              }}
            >
              {v}
            </Box>
          );
        },
        size: 95,
      },
      {
        accessorKey: "calculatedProductionSuggestion",
        header: "Produce Now",
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
        size: 105,
      },
      { accessorKey: "lifecycleStage", header: "Lifecycle", size: 100 },
      {
        accessorKey: "launchDate",
        header: "Launch Date",
        cell: (c) => {
          const v = c.getValue() as string;
          if (!v) return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
          const { daysSinceLaunch } = c.row.original;
          return (
            <Box>
              <Typography variant="body2">{v}</Typography>
              {daysSinceLaunch >= 0 && (
                <Typography variant="caption" sx={{ color: "text.secondary" }}>
                  {daysSinceLaunch}d ago
                </Typography>
              )}
            </Box>
          );
        },
        size: 110,
      },
      {
        accessorKey: "healthScore",
        header: "Health",
        cell: (c) => {
          const v = c.getValue() as number;
          const color = v >= 70 ? "#22c55e" : v >= 40 ? "#f97316" : "#ef4444";
          return (
            <Box component="span" sx={{ fontWeight: 700, color }}>
              {v}
            </Box>
          );
        },
        size: 80,
      },
    ],
    [],
  );

  if (isLoading) return <LoadingSkeleton variant="page" />;
  if (isError || !data) return <ErrorState onRetry={() => refetch()} />;

  // Below the search threshold, use the default top-1000-by-forecast page;
  // once the user searches, query the full plan server-side so low-forecast
  // rows (e.g. newly-launched designs) are findable too, not just what's
  // already been fetched.
  const rows = debouncedSearch.trim() ? (searchResults ?? []) : data.planningRows;

  return (
    <Box>
      <PageHeader
        title="Demand Forecasting"
        subtitle="SKU production plan · top selling state, city & warehouse"
        actions={
          <Tooltip title="Refresh now">
            <span>
              <IconButton size="small" onClick={() => refetch()} disabled={isFetching}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        }
      />

      <Box
        onClick={() => setNewDesignsOpen(true)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 1.5,
          px: 2,
          py: 1.25,
          mb: 2,
          borderRadius: 1.5,
          border: "1px solid",
          borderColor: "divider",
          cursor: "pointer",
          transition: "border-color 0.15s, background-color 0.15s",
          "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
        }}
      >
        <Typography variant="h5" sx={{ fontWeight: 700, color: "primary.main" }}>
          {data.newDesignCount}
        </Typography>
        <Box>
          <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
            Newly Launched Designs
          </Typography>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            Launched in the last {NEW_DESIGN_MAX_AGE_DAYS} days · click to view
          </Typography>
        </Box>
      </Box>

      <DataTable
        title="SKU Production Plan"
        data={rows}
        columns={columns}
        initialPageSize={20}
        globalSearchPlaceholder="Search SKU or design (full plan)…"
        onSearchChange={setSearchText}
        toolbarActions={
          <>
            {debouncedSearch.trim() && (
              <Typography variant="caption" sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>
                {isSearching ? "Searching…" : `${rows.length} match${rows.length === 1 ? "" : "es"}`}
              </Typography>
            )}
            <ExportButton rows={rows as unknown as Record<string, unknown>[]} filename="sku_production_plan" />
          </>
        }
      />

      <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mt: 3 }}>
        <Box sx={{ flex: 1 }}>
          <TopTable title="Top States" rows={data.topStates} />
        </Box>
        <Box sx={{ flex: 1 }}>
          <TopTable title="Top Cities" rows={data.topCities} />
        </Box>
        <Box sx={{ flex: 1 }}>
          <TopTable title="Top Warehouses" rows={data.topWarehouses} />
        </Box>
      </Stack>

      <PlanDetailDrawer
        sku={selectedSku}
        row={rows.find((r) => r.skuCode === selectedSku) ?? null}
        onClose={() => setSelectedSku(null)}
      />

      <NewDesignsDialog
        open={newDesignsOpen}
        onClose={() => setNewDesignsOpen(false)}
        maxAgeDays={NEW_DESIGN_MAX_AGE_DAYS}
      />
    </Box>
  );
}
