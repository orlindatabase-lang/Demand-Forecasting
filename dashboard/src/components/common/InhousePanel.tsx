import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Chip,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  type SelectChangeEvent,
  Stack,
  Typography,
} from "@mui/material";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "@/components/tables/DataTable";
import ExportButton from "@/components/common/ExportButton";
import { useInhouseLots } from "@/hooks/useDashboardData";
import type { IHFRow } from "@/services/dataService";
import { dateSortingFn, formatNumber } from "@/utils/format";

const PROCESS_COLOR: Record<string, string> = {
  Cutting: "#6366f1",
  Stitching: "#0d9488",
  "Thread Cutting Store": "#f59e0b",
  "General Store Out": "#8b5cf6",
  "Final Barcode Generator": "#22c55e",
};

const BAND_COLOR: Record<string, string> = { High: "#ef4444", Medium: "#f97316", Low: "#22c55e" };

function DelayRiskCell({ prob, band, alreadyLate }: { prob: number | null; band: string | null; alreadyLate: boolean }) {
  if (prob == null || band == null) {
    return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
  }
  const pct = Math.round(prob * 100);
  const color = BAND_COLOR[band] ?? "#94a3b8";
  return (
    <Box sx={{ minWidth: 90 }}>
      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 0.25 }}>
        <Box sx={{ flex: 1, height: 5, bgcolor: "action.hover", borderRadius: 1, overflow: "hidden" }}>
          <Box sx={{ width: `${pct}%`, height: "100%", bgcolor: color, borderRadius: 1 }} />
        </Box>
        <Typography variant="caption" sx={{ color, fontWeight: 700, minWidth: 30, textAlign: "right" }}>
          {pct}%
        </Typography>
      </Stack>
      <Box component="span" sx={{ fontSize: "0.68rem", fontWeight: 700, color, bgcolor: color + "18", px: 0.75, py: 0.1, borderRadius: 0.75, border: `1px solid ${color}44` }}>
        {alreadyLate ? "Late" : band}
      </Box>
    </Box>
  );
}

const ALL = "All";
// These processes have no downstream "receive" voucher in the ERP — Receive
// Qty/Date are always empty for them, so hide those two columns instead of
// showing dead cells, and "Issue Qty" is relabelled "Bal. Qty" since nothing
// is actually issued onward from them.
const NO_RECEIVE_PROCESSES = new Set(["Final Barcode Generator", "Thread Cutting Store"]);

const BASE_COLUMNS: ColumnDef<IHFRow, any>[] = [
  {
    id: "delayRisk",
    header: "AI Delay Risk",
    accessorFn: (r) => r.delayProb ?? -1,
    cell: ({ row }) => (
      <DelayRiskCell prob={row.original.delayProb} band={row.original.riskBand} alreadyLate={row.original.alreadyLate} />
    ),
    size: 115,
  },
  { accessorKey: "design", header: "Design No", cell: (c) => <strong>{c.getValue() as string}</strong>, size: 145 },
  {
    accessorKey: "lotNo",
    header: "Lot No",
    cell: (c) => {
      const v = c.getValue() as string;
      return v ? <Typography variant="caption" sx={{ fontFamily: "monospace" }}>{v}</Typography> : <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
    },
    size: 120,
  },
  {
    accessorKey: "section",
    header: "Section",
    size: 85,
    cell: (c) => {
      const s = c.getValue() as string;
      if (!s) return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
      const color = s === "Top" ? "#6366f1" : s === "Bottom" ? "#0d9488" : "#f59e0b";
      return <Box component="span" sx={{ color, fontWeight: 600, fontSize: "0.8rem" }}>{s}</Box>;
    },
  },
  {
    accessorKey: "process",
    header: "Process",
    size: 190,
    cell: (c) => {
      const v = c.getValue() as string;
      return <Box component="span" sx={{ color: PROCESS_COLOR[v] ?? "text.primary", fontWeight: 600, fontSize: "0.78rem" }}>{v}</Box>;
    },
  },
  {
    accessorKey: "issueQty",
    header: "Issue Qty",
    size: 95,
    cell: (c) => {
      const v = c.getValue() as number;
      return v > 0 ? formatNumber(v) : <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
    },
  },
  { accessorKey: "issueDate", header: "Issue Date", size: 110, sortingFn: dateSortingFn },
  {
    accessorKey: "receiveQty",
    header: "Receive Qty",
    size: 100,
    cell: (c) => {
      const v = c.getValue() as number;
      return v > 0 ? formatNumber(v) : <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
    },
  },
  {
    accessorKey: "receiveDate",
    header: "Receive Date",
    size: 110,
    sortingFn: dateSortingFn,
    cell: (c) => (c.getValue() as string) || <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>,
  },
  {
    accessorKey: "ageDays",
    header: "Age / Exp.",
    size: 105,
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

const RECEIVE_COL_KEYS = new Set(["receiveQty", "receiveDate"]);

interface InhousePanelProps {
  /** Pre-select a process filter, e.g. when arriving from a Bottleneck Detection link. */
  initialProcess?: string;
}

export default function InhousePanel({ initialProcess }: InhousePanelProps = {}) {
  const [activeProcess, setActiveProcess] = useState<string>(initialProcess ?? ALL);
  // Re-apply if the link changes while this page is already mounted (client-side
  // nav to the same route doesn't remount, so a plain useState initial value alone
  // would miss a second click from Bottleneck Detection).
  useEffect(() => {
    if (initialProcess) setActiveProcess(initialProcess);
  }, [initialProcess]);
  const { data } = useInhouseLots(activeProcess === ALL ? undefined : activeProcess);

  const columns = useMemo(() => {
    if (!NO_RECEIVE_PROCESSES.has(activeProcess)) return BASE_COLUMNS;
    // These two processes' "AI Delay Risk" is a plain age-vs-expected ramp,
    // not a real model prediction (too little history to train on) — hide
    // the column rather than label a heuristic as "AI".
    return BASE_COLUMNS.filter((c) => c.id !== "delayRisk")
      .filter((c) => !("accessorKey" in c && RECEIVE_COL_KEYS.has(c.accessorKey as string)))
      .map((c) => {
        if (!("accessorKey" in c)) return c;
        if (c.accessorKey === "issueQty") return { ...c, header: "Bal. Qty" };
        if (c.accessorKey === "issueDate") return { ...c, header: "Date" };
        return c;
      });
  }, [activeProcess]);

  if (!data) return null;

  const items = (data.items ?? []).filter((r) => r.lotStatus === "Open");
  const processes = data.processes ?? [];
  const counts = data.counts ?? {};
  const delayed = items.filter((r) => r.riskLevel === "Delayed").length;
  const atRisk = items.filter((r) => r.riskLevel === "At Risk").length;
  const onTrack = items.filter((r) => r.riskLevel === "On Track").length;

  return (
    <Box sx={{ mt: 2.5 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        {delayed > 0 && <Chip label={`Delayed: ${delayed}`} size="small" sx={{ bgcolor: "#ef444422", color: "#ef4444", border: "1px solid #ef444455", fontWeight: 700 }} />}
        {atRisk > 0 && <Chip label={`At Risk: ${atRisk}`} size="small" sx={{ bgcolor: "#f9731622", color: "#f97316", border: "1px solid #f9731655", fontWeight: 700 }} />}
        {onTrack > 0 && <Chip label={`On Track: ${onTrack}`} size="small" sx={{ bgcolor: "#22c55e22", color: "#22c55e", border: "1px solid #22c55e55", fontWeight: 700 }} />}
        <Typography variant="caption" sx={{ color: "text.secondary", ml: "auto" }}>
          Showing active lots only
        </Typography>
      </Stack>

      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 1.5 }}>
        <FormControl size="small" sx={{ minWidth: 260 }}>
          <InputLabel id="inhouse-process-label">Process</InputLabel>
          <Select
            labelId="inhouse-process-label"
            value={activeProcess}
            label="Process"
            onChange={(e: SelectChangeEvent) => setActiveProcess(e.target.value)}
          >
            <MenuItem value={ALL}>
              <Stack direction="row" alignItems="center" justifyContent="space-between" width="100%">
                <span>All Processes</span>
                <Chip label={data.total ?? 0} size="small" sx={{ ml: 1, height: 18, fontSize: "0.68rem", fontWeight: 700 }} />
              </Stack>
            </MenuItem>
            {processes.map((proc) => (
              <MenuItem key={proc} value={proc}>
                <Stack direction="row" alignItems="center" justifyContent="space-between" width="100%">
                  <Box component="span" sx={{ color: PROCESS_COLOR[proc] ?? "text.primary", fontWeight: 600 }}>{proc}</Box>
                  <Chip
                    label={counts[proc] ?? 0}
                    size="small"
                    sx={{ ml: 1, height: 18, fontSize: "0.68rem", fontWeight: 700, bgcolor: (PROCESS_COLOR[proc] ?? "#888") + "22", color: PROCESS_COLOR[proc] ?? "text.secondary", border: `1px solid ${(PROCESS_COLOR[proc] ?? "#888") + "55"}` }}
                  />
                </Stack>
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      <DataTable
        title="Inhouse — Process Tracker"
        data={items}
        columns={columns}
        initialPageSize={10}
        globalSearchPlaceholder="Search design, lot, section, vendor…"
        toolbarActions={<ExportButton rows={items as unknown as Record<string, unknown>[]} filename={`inhouse_${activeProcess.toLowerCase().replace(/ /g, "_")}`} />}
      />
      <Typography variant="caption" sx={{ color: "text.secondary", mt: 0.5, display: "block" }}>
        {`${items.length} active lots shown (of ${data.total ?? 0} total incl. completed, last 365 days)`}
      </Typography>
    </Box>
  );
}
