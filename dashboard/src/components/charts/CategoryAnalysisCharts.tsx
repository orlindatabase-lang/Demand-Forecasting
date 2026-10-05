import { useMemo, useState } from "react";
import { Box, FormControl, MenuItem, Paper, Select, Stack, Typography } from "@mui/material";
import { ChartsContainer } from "@mui/x-charts/ChartsContainer";
import { BarPlot } from "@mui/x-charts/BarChart";
import { LinePlot, MarkPlot, lineClasses } from "@mui/x-charts/LineChart";
import { ChartsAxis, axisClasses } from "@mui/x-charts/ChartsAxis";
import { ChartsGrid, chartsGridClasses } from "@mui/x-charts/ChartsGrid";
import { ChartsTooltip } from "@mui/x-charts/ChartsTooltip";
import { ChartsAxisHighlight } from "@mui/x-charts/ChartsAxisHighlight";
import { ChartsReferenceLine } from "@mui/x-charts/ChartsReferenceLine";
import { formatNumber } from "@/utils/format";

// Indigo steps for the bars (the dashboard's brand hue; one hue = one measure)
// + teal for the spike line. Bars/line and best-month/line pairs validated
// (CVD ΔE >= 13.9, contrast >= 3:1 on white).
const BAR_COLOR = "#8a83d2";
const BAR_PEAK_COLOR = "#4b42a3"; // the best complete month
const BAR_PARTIAL_COLOR = "#dcd9f3"; // current month, not complete yet
const SPIKE_COLOR = "#0f9d8f";
const GRID_COLOR = "#eceef3";
const UP_TEXT = "#15803d";
const DOWN_TEXT = "#dc2626";
const MUTED = "#64748b";
const TOTAL_KEY = "__total__";
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export interface ChartCell {
  units: number;
  prevUnits: number;
  changePct: number | null;
}

export interface ChartGroup {
  group: string;
  months: Record<string, ChartCell>;
}

function axisLabel(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return `${MONTH_SHORT[m - 1]} ${String(y).slice(2)}`;
}

const MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-10" -> "October 2026" */
function monthYear(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return `${MONTH_FULL[m - 1]} ${y}`;
}

/** Compact Indian-style axis numbers: 950, 8k, 12k, 1.2L. */
function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 100000) return `${(v / 100000).toFixed(a >= 1000000 ? 0 : 1).replace(/\.0$/, "")}L`;
  if (a >= 1000) return `${(v / 1000).toFixed(a >= 10000 ? 0 : 1).replace(/\.0$/, "")}k`;
  return String(v);
}

function signed(pct: number): string {
  return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

/** One headline figure in the stat strip above the chart. */
function Stat({ label, value, sub, subColor }: { label: string; value: string; sub?: string; subColor?: string }) {
  return (
    <Box sx={{ px: 1.5, py: 0.75, borderLeft: 3, borderColor: "divider", minWidth: 120 }}>
      <Typography variant="caption" sx={{ color: MUTED, fontWeight: 700, letterSpacing: 0.3, display: "block", lineHeight: 1.3 }}>
        {label.toUpperCase()}
      </Typography>
      <Typography variant="subtitle1" sx={{ fontWeight: 800, lineHeight: 1.25, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {sub && (
        <Typography variant="caption" sx={{ color: subColor ?? MUTED, fontWeight: 600, display: "block", lineHeight: 1.3 }}>
          {sub}
        </Typography>
      )}
    </Box>
  );
}

function LegendItem({ color, label, line }: { color: string; label: string; line?: boolean }) {
  return (
    <Stack direction="row" spacing={0.6} alignItems="center">
      <Box sx={line ? { width: 16, height: 3, borderRadius: 2, bgcolor: color } : { width: 10, height: 10, borderRadius: "2px", bgcolor: color }} />
      <Typography variant="caption" sx={{ color: MUTED, whiteSpace: "nowrap" }}>{label}</Typography>
    </Stack>
  );
}

interface Props {
  months: string[]; // oldest -> newest
  groups: ChartGroup[]; // sorted, biggest first
  totals: Record<string, ChartCell>;
  partialMonth: string;
  groupName: string;
}

/** Compact combo chart below the Category Analysis table, for one Sub
 * Category / Category or the total: headline figures, orders per month as
 * units sold per month as bars (left axis) and the month-on-month spike % as a line (right
 * axis, 0% guide line). */
export default function CategoryAnalysisCharts({ months, groups, totals, partialMonth, groupName }: Props) {
  const [key, setKey] = useState(TOTAL_KEY);
  const byName = useMemo(() => new Map(groups.map((g) => [g.group, g.months])), [groups]);
  const cells = useMemo(() => (key === TOTAL_KEY ? totals : byName.get(key) ?? {}), [key, totals, byName]);

  const labels = months.map((m) => (m === partialMonth ? `${axisLabel(m)}*` : axisLabel(m)));
  const units = months.map((m) => cells[m]?.units ?? 0);
  const spike = months.map((m) => cells[m]?.changePct ?? null);

  // Headline figures over complete months only.
  const stats = useMemo(() => {
    const full = months.filter((m) => m !== partialMonth && cells[m]);
    if (full.length === 0) return null;
    const peak = full.reduce((a, b) => ((cells[b]?.units ?? 0) > (cells[a]?.units ?? 0) ? b : a));
    // The last two complete months, oldest first.
    return { recent: full.slice(-2), peak };
  }, [months, partialMonth, cells]);

  const barColors = months.map((m) =>
    m === partialMonth ? BAR_PARTIAL_COLOR : stats && m === stats.peak ? BAR_PEAK_COLOR : BAR_COLOR,
  );
  const partialCell = partialMonth ? cells[partialMonth] : undefined;

  return (
    <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, bgcolor: "#ffffff" }}>
      {/* Header: title + legend on the left, selector on the right */}
      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "flex-start" }} justifyContent="space-between">
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 800, lineHeight: 1.3 }}>Monthly units sold &amp; spike</Typography>
          <Stack direction="row" spacing={2} sx={{ mt: 0.5, flexWrap: "wrap", rowGap: 0.5 }}>
            <LegendItem color={BAR_COLOR} label="Units sold (left)" />
            <LegendItem color={BAR_PEAK_COLOR} label="Best month" />
            <LegendItem color={SPIKE_COLOR} label="Spike vs prev. month (right)" line />
            {partialMonth && <LegendItem color={BAR_PARTIAL_COLOR} label={monthYear(partialMonth)} />}
          </Stack>
        </Box>
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <Select value={key} onChange={(e) => setKey(e.target.value)} displayEmpty inputProps={{ "aria-label": groupName }}>
            <MenuItem value={TOTAL_KEY}>All {groupName === "Category" ? "categories" : "sub categories"} (total)</MenuItem>
            {groups.map((g) => (
              <MenuItem key={g.group} value={g.group}>{g.group}</MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      {/* Headline figures */}
      {stats && (
        <Stack direction="row" sx={{ mt: 1.5, flexWrap: "wrap", rowGap: 1 }}>
          {stats.recent.map((m) => {
            const change = cells[m]?.changePct ?? null;
            return (
              <Stat
                key={m}
                label={axisLabel(m)}
                value={formatNumber(cells[m]?.units ?? 0)}
                sub={change == null ? "units" : `${change >= 0 ? "▲" : "▼"} ${signed(change)} vs prev.`}
                subColor={change == null ? undefined : change >= 0 ? UP_TEXT : DOWN_TEXT}
              />
            );
          })}
          {partialMonth && partialCell && (
            <Stat
              label={`${axisLabel(partialMonth)} so far`}
              value={formatNumber(partialCell.units)}
              sub={partialCell.changePct == null ? "units" : `${partialCell.changePct >= 0 ? "▲" : "▼"} ${signed(partialCell.changePct)} vs same days`}
              subColor={partialCell.changePct == null ? undefined : partialCell.changePct >= 0 ? UP_TEXT : DOWN_TEXT}
            />
          )}
        </Stack>
      )}

      <ChartsContainer
        height={260}
        sx={{
          [`& .${chartsGridClasses.line}`]: { stroke: GRID_COLOR, strokeDasharray: "3 3" },
          [`& .${lineClasses.line}`]: { strokeWidth: 2.5 },
          [`& .${lineClasses.mark}`]: { fill: "#ffffff", strokeWidth: 2 },
          [`& .${axisClasses.tickLabel}`]: { fontWeight: 500 },
        }}
        margin={{ left: 0, right: 0, top: 12, bottom: 0 }}
        xAxis={[
          {
            id: "month",
            scaleType: "band",
            data: labels,
            categoryGapRatio: 0.35,
            colorMap: { type: "ordinal", values: labels, colors: barColors },
            tickLabelStyle: { fontSize: 11 },
            disableTicks: true,
          },
          // Same months for the line, without the bar colour rule (a colorMap on
          // the line's axis would recolour the line). Hidden: same positions.
          { id: "monthLine", scaleType: "band", data: labels, position: "none" },
        ]}
        yAxis={[
          { id: "units", position: "left", width: 44, valueFormatter: (v: number) => compact(v), tickLabelStyle: { fontSize: 11, fill: MUTED }, disableLine: true, disableTicks: true },
          { id: "spike", position: "right", width: 48, valueFormatter: (v: number) => `${v}%`, tickLabelStyle: { fontSize: 11, fill: SPIKE_COLOR }, disableLine: true, disableTicks: true },
        ]}
        series={[
          {
            type: "bar",
            xAxisId: "month",
            yAxisId: "units",
            label: "Units sold",
            color: BAR_COLOR,
            data: units,
            valueFormatter: (v: number | null) => (v == null ? "—" : `${formatNumber(v)} units`),
          },
          {
            type: "line",
            xAxisId: "monthLine",
            yAxisId: "spike",
            label: "Spike vs previous month",
            color: SPIKE_COLOR,
            data: spike,
            curve: "linear",
            showMark: true,
            valueFormatter: (v: number | null) => (v == null ? "no orders the month before" : signed(v)),
          },
        ]}
      >
        <ChartsGrid horizontal />
        <BarPlot borderRadius={3} />
        <ChartsReferenceLine y={0} axisId="spike" lineStyle={{ stroke: SPIKE_COLOR, strokeDasharray: "4 4", strokeOpacity: 0.55 }} />
        <LinePlot />
        <MarkPlot />
        <ChartsAxis />
        <ChartsAxisHighlight x="band" />
        <ChartsTooltip trigger="axis" />
      </ChartsContainer>

    </Paper>
  );
}
