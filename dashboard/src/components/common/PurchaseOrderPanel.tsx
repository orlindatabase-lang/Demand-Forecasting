import { useMemo } from "react";
import { Box, Chip, Stack, Typography } from "@mui/material";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "@/components/tables/DataTable";
import ExportButton from "@/components/common/ExportButton";
import { usePurchaseOrderLots } from "@/hooks/useDashboardData";
import type { POLot } from "@/services/dataService";
import { dateSortingFn, formatNumber } from "@/utils/format";

const RISK_COLOR: Record<string, string> = { Delayed: "#ef4444", "At Risk": "#f97316", "On Track": "#22c55e", Completed: "#6366f1" };
const BAND_COLOR: Record<string, string> = { High: "#ef4444", Medium: "#f97316", Low: "#22c55e" };

function DelayRiskCell({ prob, band, alreadyLate }: { prob: number | null; band: string | null; alreadyLate: boolean }) {
  if (prob == null || band == null) return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
  const pct = Math.round(prob * 100);
  const color = BAND_COLOR[band] ?? "#94a3b8";
  return (
    <Box sx={{ minWidth: 90 }}>
      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 0.25 }}>
        <Box sx={{ flex: 1, height: 5, bgcolor: "action.hover", borderRadius: 1, overflow: "hidden" }}>
          <Box sx={{ width: `${pct}%`, height: "100%", bgcolor: color, borderRadius: 1 }} />
        </Box>
        <Typography variant="caption" sx={{ color, fontWeight: 700, minWidth: 30, textAlign: "right" }}>{pct}%</Typography>
      </Stack>
      <Box component="span" sx={{ fontSize: "0.68rem", fontWeight: 700, color, bgcolor: color + "18", px: 0.75, py: 0.1, borderRadius: 0.75, border: `1px solid ${color}44` }}>
        {alreadyLate ? "Late" : band}
      </Box>
    </Box>
  );
}

function RiskBadge({ level }: { level: string }) {
  const c = RISK_COLOR[level] ?? "#94a3b8";
  return (
    <Box component="span" sx={{ display: "inline-block", px: 1, py: 0.25, borderRadius: 1, bgcolor: c + "22", color: c, border: `1px solid ${c}55`, fontSize: "0.72rem", fontWeight: 700, whiteSpace: "nowrap" }}>
      {level}
    </Box>
  );
}

function fcmChip(status: string) {
  const map: Record<string, "success" | "error" | "warning" | "default"> = { Pass: "success", Fail: "error", Partial: "warning", Pending: "default" };
  return <Chip label={status} color={map[status] ?? "default"} size="small" variant="outlined" />;
}

export default function PurchaseOrderPanel() {
  const { data } = usePurchaseOrderLots();

  const columns = useMemo<ColumnDef<POLot, any>[]>(
    () => [
      { accessorKey: "riskLevel", header: "Status", cell: (c) => <RiskBadge level={c.getValue() as string} />, size: 95 },
      {
        id: "delayRisk",
        header: "Delay Risk",
        accessorFn: (r) => r.delayProb ?? -1,
        cell: ({ row }) => <DelayRiskCell prob={row.original.delayProb} band={row.original.riskBand} alreadyLate={row.original.alreadyLate} />,
        size: 115,
      },
      { accessorKey: "lotNo", header: "Lot No", cell: (c) => <strong>{c.getValue()}</strong>, size: 110 },
      { accessorKey: "design", header: "Design No", size: 130 },
      {
        accessorKey: "section",
        header: "Section",
        size: 85,
        cell: (c) => {
          const s = c.getValue() as string;
          const color = s === "Top" ? "#6366f1" : s === "Bottom" ? "#0d9488" : "#f59e0b";
          return <Box component="span" sx={{ color, fontWeight: 600, fontSize: "0.8rem" }}>{s}</Box>;
        },
      },
      {
        accessorKey: "articleGroup",
        header: "Article Group",
        size: 120,
        cell: (c) => (c.getValue() as string) || <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>,
      },
      { accessorKey: "vendor", header: "Vendor", size: 160 },
      { accessorKey: "issueQty", header: "Issue Qty", cell: (c) => formatNumber(c.getValue()), size: 90 },
      { accessorKey: "issueDate", header: "Issue Date", size: 105, sortingFn: dateSortingFn },
      { accessorKey: "estDelivery", header: "Est. Delivery", size: 115, sortingFn: dateSortingFn },
      {
        accessorKey: "receiveQty",
        header: "Receive Qty",
        size: 100,
        cell: (c) => (c.getValue() as number) > 0 ? formatNumber(c.getValue()) : <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>,
      },
      { accessorKey: "receiveDate", header: "Receive Date", size: 110, sortingFn: dateSortingFn, cell: (c) => (c.getValue() as string) || <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography> },
      { accessorKey: "fcmStatus", header: "FCM", cell: (c) => fcmChip(c.getValue() as string), size: 90 },
      {
        id: "allocation",
        header: "Allocation",
        accessorFn: (r) => r.allocQty,
        cell: ({ row }) => {
          const lot = row.original;
          if (!lot.allocQty) return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
          return (
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{formatNumber(lot.allocQty)} pcs</Typography>
              {lot.allocDate && <Typography variant="caption" sx={{ color: "text.secondary" }}>{lot.allocDate}</Typography>}
            </Box>
          );
        },
        size: 115,
      },
      {
        accessorKey: "ageDays",
        header: "Age",
        size: 65,
        cell: (c) => {
          const v = c.getValue() as number;
          return <Box component="span" sx={{ fontWeight: 600, color: v > 30 ? "#ef4444" : v > 14 ? "#f97316" : "text.primary" }}>{v}d</Box>;
        },
      },
    ],
    [],
  );

  if (!data) return null;

  const items = (data.items ?? []).filter((r) => r.lotStatus === "Open");
  const delayed = items.filter((r) => r.riskLevel === "Delayed").length;
  const atRisk = items.filter((r) => r.riskLevel === "At Risk").length;
  const onTrack = items.filter((r) => r.riskLevel === "On Track").length;

  return (
    <Box sx={{ mt: 2.5 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        {delayed > 0 && <Chip label={`Delayed: ${delayed}`} size="small" sx={{ bgcolor: "#ef444422", color: "#ef4444", border: "1px solid #ef444455", fontWeight: 700 }} />}
        {atRisk > 0 && <Chip label={`At Risk: ${atRisk}`} size="small" sx={{ bgcolor: "#f9731622", color: "#f97316", border: "1px solid #f9731655", fontWeight: 700 }} />}
        {onTrack > 0 && <Chip label={`On Track: ${onTrack}`} size="small" sx={{ bgcolor: "#22c55e22", color: "#22c55e", border: "1px solid #22c55e55", fontWeight: 700 }} />}
        <Typography variant="caption" sx={{ color: "text.secondary", ml: "auto" }}>Showing active lots only</Typography>
      </Stack>

      <DataTable
        title="Purchase Order — Delay Tracker"
        data={items}
        columns={columns}
        initialPageSize={10}
        globalSearchPlaceholder="Search lot, design, vendor…"
        toolbarActions={<ExportButton rows={items as unknown as Record<string, unknown>[]} filename="purchase_order_delay" />}
      />
      <Typography variant="caption" sx={{ color: "text.secondary", mt: 0.5, display: "block" }}>
        {`${items.length} active lots (of ${data.total ?? 0} total incl. completed) · risk based on estimated delivery date`}
      </Typography>
    </Box>
  );
}
