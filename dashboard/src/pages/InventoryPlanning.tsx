import { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import type { ColumnDef } from "@tanstack/react-table";
import PageHeader from "@/components/common/PageHeader";
import DataTable from "@/components/tables/DataTable";
import ExportButton from "@/components/common/ExportButton";
import { LoadingSkeleton } from "@/components/common/StateViews";
import { useInventoryPlanning } from "@/hooks/useDashboardData";
import type { InventoryPlanningRow } from "@/services/dataService";
import { formatNumber } from "@/utils/format";

export default function InventoryPlanning() {
  const { data, isLoading } = useInventoryPlanning();

  const columns = useMemo<ColumnDef<InventoryPlanningRow, any>[]>(
    () => [
      { accessorKey: "design", header: "Design", cell: (c) => <strong>{c.getValue()}</strong>, size: 130 },
      { accessorKey: "size", header: "Size", size: 80 },
      { accessorKey: "currentStock", header: "Current Stock", cell: (c) => formatNumber(c.getValue()), size: 120 },
      { accessorKey: "dailyRunRate", header: "Daily Run Rate", cell: (c) => (c.getValue() as number).toFixed(1), size: 120 },
      {
        accessorKey: "daysToFinish",
        header: "Days to Finish",
        size: 130,
        cell: (c) => {
          const v = c.getValue() as number;
          if (v <= 0) return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
          const color = v < 14 ? "#ef4444" : v < 30 ? "#f97316" : "#22c55e";
          return <Box component="span" sx={{ fontWeight: 700, color }}>{v}d</Box>;
        },
      },
    ],
    [],
  );

  if (isLoading) return <LoadingSkeleton variant="page" />;

  const items = data?.items ?? [];

  return (
    <Box>
      <PageHeader
        title="Inventory Planning"
        subtitle="Finished-goods stock by design/size — days-to-finish based on actual sales run-rate"
      />
      <DataTable
        title="Design-wise Inventory"
        data={items}
        columns={columns}
        initialPageSize={20}
        globalSearchPlaceholder="Search design…"
        toolbarActions={<ExportButton rows={items as unknown as Record<string, unknown>[]} filename="inventory_planning" />}
      />
    </Box>
  );
}
