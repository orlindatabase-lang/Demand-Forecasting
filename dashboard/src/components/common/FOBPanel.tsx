import { Box, Chip, Stack, Typography } from "@mui/material";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "@/components/tables/DataTable";
import ExportButton from "@/components/common/ExportButton";
import { useFobLots } from "@/hooks/useDashboardData";
import type { IHFRow } from "@/services/dataService";
import { dateSortingFn, formatNumber } from "@/utils/format";

const RISK_COLOR: Record<string, string> = { Delayed: "#ef4444", "At Risk": "#f97316", "On Track": "#22c55e", Completed: "#6366f1" };
const BAND_COLOR: Record<string, string> = { High: "#ef4444", Medium: "#f97316", Low: "#22c55e" };
const FOB_EXPECTED_DAYS = 21;

function RiskBadge({ level }: { level: string }) {
  const c = RISK_COLOR[level] ?? "#94a3b8";
  return (
    <Box component="span" sx={{ display: "inline-block", px: 1, py: 0.25, borderRadius: 1, bgcolor: c + "22", color: c, border: `1px solid ${c}55`, fontSize: "0.72rem", fontWeight: 700, whiteSpace: "nowrap" }}>
      {level}
    </Box>
  );
}

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

const COLUMNS: ColumnDef<IHFRow, any>[] = [
  {
    id: "delayRisk",
    header: "AI Delay Risk",
    accessorFn: (r) => r.delayProb ?? -1,
    cell: ({ row }) => <DelayRiskCell prob={row.original.delayProb} band={row.original.riskBand} alreadyLate={row.original.alreadyLate} />,
    size: 115,
  },
  { accessorKey: "riskLevel", header: "Risk", cell: (c) => <RiskBadge level={c.getValue() as string} />, size: 95 },
  { accessorKey: "lotNo", header: "Lot No", cell: (c) => <strong>{c.getValue()}</strong>, size: 110 },
  { accessorKey: "design", header: "Design No", size: 120 },
  { accessorKey: "vendor", header: "Vendor", size: 160 },
  {
    accessorKey: "issueQty",
    header: "Issue Qty",
    size: 95,
    cell: (c) => (c.getValue() as number) > 0 ? formatNumber(c.getValue()) : <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>,
  },
  { accessorKey: "issueDate", header: "Issue Date", size: 110, sortingFn: dateSortingFn },
  {
    accessorKey: "receiveQty",
    header: "Receive Qty",
    size: 100,
    cell: (c) => {
      const v = c.getValue() as number;
      return v > 0 ? <Box component="span" sx={{ fontWeight: 600, color: "#22c55e" }}>{formatNumber(v)}</Box> : <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
    },
  },
  {
    accessorKey: "receiveDate",
    header: "Receive Date",
    size: 110,
    sortingFn: dateSortingFn,
    cell: (c) => {
      const v = c.getValue() as string;
      return v ? <Box component="span" sx={{ color: "#22c55e", fontWeight: 600 }}>{v}</Box> : <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
    },
  },
  {
    accessorKey: "ageDays",
    header: `Age / ${FOB_EXPECTED_DAYS}d`,
    size: 110,
    cell: ({ row }) => {
      const age = row.original.ageDays;
      const exp = row.original.expectedDays;
      const risk = row.original.riskLevel;
      const color = risk === "Delayed" ? "#ef4444" : risk === "At Risk" ? "#f97316" : "text.primary";
      return (
        <Box>
          <Box component="span" sx={{ fontWeight: 700, color }}>{age}d</Box>
          <Typography variant="caption" sx={{ color: "text.secondary", ml: 0.5 }}>/ {exp}d</Typography>
        </Box>
      );
    },
  },
];

export default function FOBPanel() {
  const { data } = useFobLots();

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
        title="FOB — Issue Tracker"
        data={items}
        columns={COLUMNS}
        initialPageSize={10}
        globalSearchPlaceholder="Search lot, design, vendor…"
        toolbarActions={<ExportButton rows={items as unknown as Record<string, unknown>[]} filename="fob_issue" />}
      />
      <Typography variant="caption" sx={{ color: "text.secondary", mt: 0.5, display: "block" }}>
        {`${items.length} active FOB lots (of ${data.total ?? 0} total incl. completed) · expected turnaround ${FOB_EXPECTED_DAYS} days`}
      </Typography>
    </Box>
  );
}
