import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Chip, Stack, TextField, Typography } from "@mui/material";
import type { ColumnDef } from "@tanstack/react-table";
import dayjs from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat";
import DataTable from "@/components/tables/DataTable";
import ExportButton from "@/components/common/ExportButton";
import { useBottlenecks } from "@/hooks/useDashboardData";
import type { BottleneckLot } from "@/services/dataService";
import { formatNumber } from "@/utils/format";

dayjs.extend(customParseFormat);

const SEVERITY_COLOR: Record<string, string> = { High: "#ef4444", Medium: "#f97316", Low: "#22c55e" };

// Combined Inhouse + Job Work process palette (Bottleneck spans both trackers).
const PROCESS_COLOR: Record<string, string> = {
  Cutting: "#6366f1",
  Stitching: "#0d9488",
  "Thread Cutting Store": "#f59e0b",
  "General Store Out": "#8b5cf6",
  "Final Barcode Generator": "#22c55e",
  "Cut to Pack Issue": "#6366f1",
  "Cut to Stitching Issue": "#0d9488",
  "Only Stitching Issue": "#f59e0b",
  "Job QC Process": "#22c55e",
};

// Mirrors api/inhouse.py's ALL_PROCESSES — used only to route a process name
// to the right Production Trackers tab, since the two trackers use disjoint
// process names (see get_bottlenecks()'s docstring).
const INHOUSE_PROCESSES = new Set([
  "Cutting", "Stitching", "Thread Cutting Store", "General Store Out", "Final Barcode Generator",
]);

function trackerLink(process: string): string {
  const tab = INHOUSE_PROCESSES.has(process) ? "Inhouse" : "Job Work";
  return `/production?tab=${encodeURIComponent(tab)}&process=${encodeURIComponent(process)}`;
}

function formatVendor(v: string): string | null {
  return v && v.toLowerCase() !== "nan" ? v : null;
}

function FocusStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Box sx={{ px: 1.5, py: 0.75, borderRadius: 1.5, border: "1px solid", borderColor: "divider", minWidth: 96 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, color: color ?? "text.primary" }}>
        {value}
      </Typography>
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        {label}
      </Typography>
    </Box>
  );
}

// Composite searchable text for a lot — used both as the table column's
// accessor (so the existing search box can actually match these rows; the
// column has no plain accessorKey, it's a rich JSX cell) and, identically,
// to detect a single-lot match for the "focus" view below.
function searchText(r: BottleneckLot): string {
  return [r.lotNo, r.design, r.vendor, r.process, r.section].filter(Boolean).join(" ").toLowerCase();
}

export default function BottleneckPanel() {
  const navigate = useNavigate();
  const { data } = useBottlenecks();
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");

  const lots = data?.lots ?? [];

  const filtered = useMemo(() => {
    if (!dateFrom && !dateTo) return lots;
    return lots.filter((r) => {
      if (!r.issueDate) return false;
      const d = dayjs(r.issueDate, "D MMM YYYY");
      if (!d.isValid()) return false;
      if (dateFrom && d.isBefore(dayjs(dateFrom))) return false;
      if (dateTo && d.isAfter(dayjs(dateTo))) return false;
      return true;
    });
  }, [lots, dateFrom, dateTo]);

  // Same substring match the table's own search box performs (via the "line"
  // column's accessorFn above) — mirrored here just to detect when a search
  // has narrowed things down to one specific lot, so we can surface a focused
  // "what's the bottleneck for THIS lot" view instead of only a filtered list.
  const searchMatches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return [];
    return filtered.filter((r) => searchText(r).includes(q));
  }, [filtered, search]);
  const focusLot = searchMatches.length === 1 ? searchMatches[0] : null;
  const focusProcess = focusLot ? data?.processes.find((p) => p.process === focusLot.process) ?? null : null;

  const columns = useMemo<ColumnDef<BottleneckLot, any>[]>(
    () => [
      {
        id: "line",
        header: "Stuck Lot",
        accessorFn: (r) => searchText(r),
        cell: ({ row }) => {
          const r = row.original;
          const vendor = formatVendor(r.vendor);
          const overrun = r.overrunDays > 0;
          return (
            <Box sx={{ py: 0.5 }}>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.375, flexWrap: "wrap", rowGap: 0.375 }}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>{r.design}</Typography>
                <Typography variant="caption" sx={{ fontFamily: "monospace", color: "text.secondary" }}>{r.lotNo}</Typography>
                <Box
                  component="span"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(trackerLink(r.process));
                  }}
                  sx={{
                    color: PROCESS_COLOR[r.process] ?? "text.primary",
                    fontWeight: 700,
                    fontSize: "0.7rem",
                    cursor: "pointer",
                    textDecoration: "underline",
                    textDecorationStyle: "dotted",
                  }}
                >
                  {r.process}
                </Box>
                {r.delayProb != null && r.riskBand && (
                  <Box
                    component="span"
                    sx={{
                      fontSize: "0.65rem",
                      fontWeight: 700,
                      color: SEVERITY_COLOR[r.riskBand] ?? "#94a3b8",
                      bgcolor: (SEVERITY_COLOR[r.riskBand] ?? "#94a3b8") + "18",
                      border: `1px solid ${(SEVERITY_COLOR[r.riskBand] ?? "#94a3b8")}44`,
                      px: 0.75,
                      py: 0.1,
                      borderRadius: 0.75,
                    }}
                  >
                    AI {Math.round(r.delayProb * 100)}%
                  </Box>
                )}
              </Stack>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {r.ageDays}d old
                {overrun ? (
                  <Box component="span" sx={{ color: "#ef4444", fontWeight: 700 }}>
                    {" "}· +{r.overrunDays}d over its {r.expectedDays}d expected window
                  </Box>
                ) : (
                  ` · ${r.expectedDays}d expected`
                )}
                {` · ${formatNumber(r.issueQty)} pcs issued`}
                {r.pendingPieces > 0 && `, ${formatNumber(r.pendingPieces)} pending`}
                {r.designDrr > 0 && `, design DRR ${r.designDrr.toFixed(1)}/day`}
                {vendor && `, vendor ${vendor}`}
                {r.issueDate && `, issued ${r.issueDate}`}
              </Typography>
            </Box>
          );
        },
      },
    ],
    [navigate],
  );

  if (!data) return null;

  const worst = data.processes.find((p) => p.process === data.worstProcess);

  return (
    <Box sx={{ mt: 2.5 }}>
      {worst && (
        <Box
          sx={{
            mb: 3,
            p: 2,
            borderRadius: 2,
            display: "flex",
            alignItems: "center",
            gap: 2,
            bgcolor: (SEVERITY_COLOR[worst.severity] ?? "#888") + "10",
            border: "1px solid",
            borderColor: (SEVERITY_COLOR[worst.severity] ?? "#888") + "40",
          }}
        >
          <Box sx={{ fontSize: "1.6rem", lineHeight: 1 }}>⚠️</Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
              {worst.process} is the current bottleneck
            </Typography>
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              {worst.openLots} open lots · {worst.delayedLots} delayed · {Math.round(worst.delayRate * 100)}% delay rate · avg{" "}
              {worst.avgOverrunDays > 0 ? `${worst.avgOverrunDays}d over expected` : "on schedule"}
            </Typography>
          </Box>
          <Chip
            label="View in Production Trackers"
            size="small"
            clickable
            onClick={() => navigate(trackerLink(worst.process))}
            sx={{
              fontWeight: 700,
              bgcolor: (SEVERITY_COLOR[worst.severity] ?? "#888") + "22",
              color: SEVERITY_COLOR[worst.severity] ?? "text.primary",
              border: `1px solid ${(SEVERITY_COLOR[worst.severity] ?? "#888")}55`,
            }}
          />
        </Box>
      )}

      {focusLot && (
        <Box
          sx={{
            mb: 3,
            p: 2.5,
            borderRadius: 2,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "primary.main",
            boxShadow: 2,
          }}
        >
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
            <Box>
              <Typography variant="overline" sx={{ color: "primary.main", fontWeight: 700, letterSpacing: 0.5, lineHeight: 1.4 }}>
                Lot Focus
              </Typography>
              <Typography variant="h6" sx={{ fontWeight: 800, lineHeight: 1.2 }}>
                {focusLot.design} — <Box component="span" sx={{ fontFamily: "monospace" }}>{focusLot.lotNo}</Box>
              </Typography>
            </Box>
            <Chip
              label={`View in Production Trackers`}
              size="small"
              clickable
              onClick={() => navigate(trackerLink(focusLot.process))}
              sx={{
                fontWeight: 700,
                bgcolor: (PROCESS_COLOR[focusLot.process] ?? "#888") + "22",
                color: PROCESS_COLOR[focusLot.process] ?? "text.primary",
                border: `1px solid ${(PROCESS_COLOR[focusLot.process] ?? "#888")}55`,
              }}
            />
          </Stack>

          <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
            <FocusStat label="Stuck at" value={focusLot.process} color={PROCESS_COLOR[focusLot.process]} />
            <FocusStat label="Age / Expected" value={`${focusLot.ageDays}d / ${focusLot.expectedDays}d`} />
            <FocusStat
              label="Overrun"
              value={focusLot.overrunDays > 0 ? `+${focusLot.overrunDays}d` : "On schedule"}
              color={focusLot.overrunDays > 0 ? "#ef4444" : "#22c55e"}
            />
            {focusLot.delayProb != null && focusLot.riskBand && (
              <FocusStat
                label="AI Delay Risk"
                value={`${Math.round(focusLot.delayProb * 100)}% (${focusLot.riskBand})`}
                color={SEVERITY_COLOR[focusLot.riskBand]}
              />
            )}
            <FocusStat label="Issue Qty" value={formatNumber(focusLot.issueQty)} />
            {focusLot.pendingPieces > 0 && <FocusStat label="Pending" value={formatNumber(focusLot.pendingPieces)} color="#f97316" />}
            {formatVendor(focusLot.vendor) && <FocusStat label="Vendor" value={formatVendor(focusLot.vendor)!} />}
            {focusLot.issueDate && <FocusStat label="Issued" value={focusLot.issueDate} />}
          </Stack>

          {focusProcess ? (
            <Box
              sx={{
                p: 1.5,
                borderRadius: 1.5,
                bgcolor: (SEVERITY_COLOR[focusProcess.severity] ?? "#888") + "0c",
                border: "1px dashed",
                borderColor: (SEVERITY_COLOR[focusProcess.severity] ?? "#888") + "50",
              }}
            >
              <Typography variant="body2">
                <Box component="span" sx={{ fontWeight: 700 }}>{focusLot.process}</Box> is currently a{" "}
                <Box component="span" sx={{ color: SEVERITY_COLOR[focusProcess.severity] ?? "text.primary", fontWeight: 700 }}>
                  {focusProcess.severity}
                </Box>{" "}
                bottleneck{data?.worstProcess === focusLot.process ? " — the worst in the plant right now" : ""}:{" "}
                {focusProcess.delayedLots} of its {focusProcess.openLots} open lots are delayed (
                {Math.round(focusProcess.delayRate * 100)}% delay rate), averaging {focusProcess.avgOverrunDays}d overrun.{" "}
                {focusLot.overrunDays > focusProcess.avgOverrunDays
                  ? "This lot is running worse than that process's own average."
                  : focusLot.overrunDays > 0
                    ? "This lot's overrun is within the process's typical range."
                    : "This lot itself is still on schedule."}
              </Typography>
            </Box>
          ) : (
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              No aggregate bottleneck stats available for {focusLot.process} right now.
            </Typography>
          )}
        </Box>
      )}

      {!focusLot && searchMatches.length > 1 && (
        <Typography variant="caption" sx={{ color: "text.secondary", mb: 2, display: "block" }}>
          {searchMatches.length} lots match "{search}" — narrow the search to one specific Lot No to see its bottleneck focus.
        </Typography>
      )}

      <Stack direction="row" spacing={2} alignItems="flex-end" sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
        {/* Plain stacked caption instead of a floating MUI label: a native
            type="date" input already renders its own "dd-mm-yyyy" placeholder
            text, and a floating label overlapping that same spot renders as
            garbled double text — this sidesteps it entirely. */}
        <Box>
          <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 0.5 }}>
            Issued from
          </Typography>
          <TextField type="date" size="small" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </Box>
        <Box>
          <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 0.5 }}>
            Issued to
          </Typography>
          <TextField type="date" size="small" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </Box>
        <Typography variant="caption" sx={{ color: "text.secondary", mb: 0.75 }}>
          {filtered.length} of {lots.length} lots shown
        </Typography>
      </Stack>

      <DataTable
        title="Stuck Lots — Inhouse + Job Work"
        data={filtered}
        columns={columns}
        initialPageSize={10}
        globalSearchPlaceholder="Search lot, design, vendor, process…"
        toolbarActions={<ExportButton rows={filtered as unknown as Record<string, unknown>[]} filename="bottleneck_lots" />}
        onSearchChange={setSearch}
      />
    </Box>
  );
}
