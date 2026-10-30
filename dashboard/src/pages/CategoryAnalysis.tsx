import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Box,
  Button,
  Chip,
  ToggleButton,
  ToggleButtonGroup,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import PageHeader from "@/components/common/PageHeader";
import CategoryAnalysisCharts from "@/components/charts/CategoryAnalysisCharts";
import TierSpikeChart from "@/components/charts/TierSpikeChart";
import CategoryStylesDialog, { type StylesCellSelection } from "@/components/common/CategoryStylesDialog";
import StyleFilterBar, { NO_STYLE_FILTERS, type StyleFilters } from "@/components/common/StyleFilterBar";
import { rowsToCsv, downloadCsv } from "@/components/common/ExportButton";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { useCatalogStyleTiers, useCategoryAnalysis, useCategoryAnalysisChannels } from "@/hooks/useDashboardData";
import { formatNumber } from "@/utils/format";
import type { CategoryFilter } from "@/services/dataService";
import type { CategoryAnalysisRow } from "@/types";
import { COLORS, REPORT, REPORT_HEAD_SX, REPORT_GROUP_HEAD_SX, REPORT_TOTAL_SX, STICKY_EDGE_SX } from "@/theme/colors";

type Level = "category" | "subCategory" | "style" | "tier";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
// Weekly Sales Report look (theme/colors.ts REPORT)
const MONTH_BG = [COLORS.bgWhite, COLORS.primaryBgSubtle]; // alternating month column groups, like the Weekly Sales Report
const STYLE_COLOR = COLORS.textSecondary;
const ORDER_COLOR = REPORT.actual;
const UNIT_COLOR = REPORT.forecastText;
const UP_COLOR = REPORT.good;
const DOWN_COLOR = REPORT.bad;
const GROUP_COL_WIDTH = 220;
const NUM_COL_WIDTH = 84;
const ROW_H1 = 38; // month header row height (the second header row sticks below it)
const STYLE_PAGE_SIZE = 100; // style level: ~1,500 rows, rendered one page at a time

const METRICS = [
  { key: "styles", label: "STYLES", color: STYLE_COLOR },
  { key: "orders", label: "ORDERS", color: ORDER_COLOR },
  // The "(per style)" average in brackets was removed (2026-10-29, user-requested).
  { key: "units", label: "UNITS", color: UNIT_COLOR },
  { key: "spike", label: "SPIKE", color: STYLE_COLOR },
] as const;

/** Units ÷ Styles - average pieces per style that sold that month (null when no style sold). */
function unitsPerStyle(c: { styles: number; units: number } | undefined): number | null {
  return c && c.styles > 0 ? c.units / c.styles : null;
}

interface Cell {
  styles: number;
  orders: number;
  units: number;
  prevUnits: number;
  changePct: number | null; // total units vs the previous month
  // Same-style spike parts (see the API's _same_style_figures).
  stylesBoth?: number;
  sameUnits?: number;
  samePrevUnits?: number;
  sameChangePct?: number | null;
  newStyles?: number;
  newUnits?: number;
  stoppedStyles?: number;
}

/** The spike the table shows: same styles in both months when known, else total units. */
function spikeOf(c: Cell): number | null {
  return c.sameChangePct !== undefined ? c.sameChangePct : c.changePct;
}

interface GroupRow {
  group: string;
  months: Record<string, Cell>;
  latestOrders: number;
}

function monthTitle(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return `${MONTH_NAMES[m - 1].toUpperCase()} ${y}`;
}

function shortMonth(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return `${MONTH_NAMES[m - 1].slice(0, 3)} ${y}`;
}

function dayLabel(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTH_NAMES[m - 1].slice(0, 3)}`;
}

function prevMonth(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
}

function pct(orders: number, prev: number): number | null {
  return prev > 0 ? Math.round(((orders - prev) / prev) * 1000) / 10 : null;
}

const signedPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

/** Spike vs the previous month (2026-10-13, user-requested): the SAME-STYLE
 * spike - only styles that sold in both months, so styles being added or
 * dropped do not distort it. Hover: the full breakdown incl. the total. */
function Spike({ cell, month, partial, dataThrough }: { cell: Cell | undefined; month: string; partial: boolean; dataThrough: string }) {
  if (!cell) return <>—</>;
  const compared = partial
    ? `${shortMonth(prevMonth(month))} 1–${Number(dataThrough.split("-")[2])} (same days)`
    : shortMonth(prevMonth(month));
  const spike = spikeOf(cell);
  const hasSame = cell.sameChangePct !== undefined;
  const tip = (
    <Box sx={{ py: 0.25 }}>
      {hasSame && (
        <>
          <Typography variant="caption" sx={{ display: "block", fontWeight: 700 }}>
            Same {formatNumber(cell.stylesBoth ?? 0)} styles sold in both {partial ? "periods" : "months"}:
          </Typography>
          <Typography variant="caption" sx={{ display: "block" }}>
            {formatNumber(cell.samePrevUnits ?? 0)} → {formatNumber(cell.sameUnits ?? 0)} units
            {cell.sameChangePct != null ? ` = ${signedPct(cell.sameChangePct)}` : ""}
          </Typography>
        </>
      )}
      <Typography variant="caption" sx={{ display: "block", mt: hasSame ? 0.5 : 0 }}>
        Total units: {formatNumber(cell.prevUnits)} → {formatNumber(cell.units)}
        {cell.changePct != null ? ` = ${signedPct(cell.changePct)}` : ""}
      </Typography>
      {hasSame && (
        <>
          <Typography variant="caption" sx={{ display: "block" }}>
            New styles vs {shortMonth(prevMonth(month))}: {formatNumber(cell.newStyles ?? 0)}, sold {formatNumber(cell.newUnits ?? 0)} units
          </Typography>
          <Typography variant="caption" sx={{ display: "block" }}>
            Stopped vs {shortMonth(prevMonth(month))}: {formatNumber(cell.stoppedStyles ?? 0)} styles
          </Typography>
        </>
      )}
      <Typography variant="caption" sx={{ display: "block", mt: 0.5, opacity: 0.8 }}>Compared with {compared}</Typography>
    </Box>
  );
  if (spike === null) {
    return (
      <Tooltip title={hasSame ? tip : `No orders in ${compared} to compare with`}>
        <Typography component="span" variant="body2" sx={{ color: "text.disabled" }}>new</Typography>
      </Tooltip>
    );
  }
  const up = spike >= 0;
  return (
    <Tooltip title={tip}>
      <Box component="span" sx={{ fontWeight: 700, color: up ? UP_COLOR : DOWN_COLOR }}>
        {up ? "▲ +" : "▼ "}
        {spike.toFixed(1)}%
      </Box>
    </Tooltip>
  );
}

const numCell = {
  px: 1, py: 0.75, textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
  width: NUM_COL_WIDTH, minWidth: NUM_COL_WIDTH,
} as const;
// Narrower pinned name column on phones (2026-10-29, responsive layout)
const GROUP_COL = { xs: 140, md: GROUP_COL_WIDTH } as const;
const groupCell = { width: GROUP_COL, minWidth: GROUP_COL, maxWidth: GROUP_COL } as const;
const CHANNEL_BG = REPORT.rowAltBg; // an expanded row's marketplace sub-rows
const STYLE_ROW_BG = COLORS.primaryBgSubtle; // an expanded tier's style rows

/** main = a table row, child = a style inside an opened tier, channel = a marketplace row. */
type RowVariant = "main" | "child" | "channel";
/** Which row a cell belongs to - the STYLES cell opens that row's styles. */
type RowCtx = { group: string; channel?: string };
type RenderCells = (cells: Record<string, Cell>, bold: boolean, variant?: RowVariant, ctx?: RowCtx) => ReactNode;

/** Natural tier order: T0, T1, T2, T3, T11, T12, ... then Unclassified. */
function tierRank(tier: string): number {
  const n = Number(tier.replace(/\D/g, ""));
  return /^T\d+$/i.test(tier) && Number.isFinite(n) ? n : 10_000;
}

/** Pivot flat (month, group) rows into one row per group, biggest latest-month
 * orders first - shared by the main table and a row's channel drill-down. */
function pivotRows(rows: CategoryAnalysisRow[]): { months: string[]; groups: GroupRow[] } {
  const monthSet = new Set<string>();
  const byGroup = new Map<string, GroupRow>();
  for (const r of rows) {
    monthSet.add(r.month);
    let g = byGroup.get(r.group);
    if (!g) {
      g = { group: r.group, months: {}, latestOrders: 0 };
      byGroup.set(r.group, g);
    }
    g.months[r.month] = {
      styles: r.styles, orders: r.orders, units: r.units, prevUnits: r.prevUnits, changePct: r.changePct,
      stylesBoth: r.stylesBoth, sameUnits: r.sameUnits, samePrevUnits: r.samePrevUnits, sameChangePct: r.sameChangePct,
      newStyles: r.newStyles, newUnits: r.newUnits, stoppedStyles: r.stoppedStyles,
    };
  }
  const monthList = [...monthSet].sort();
  const latest = monthList[monthList.length - 1];
  for (const g of byGroup.values()) g.latestOrders = latest ? g.months[latest]?.orders ?? 0 : 0;
  const groupList = [...byGroup.values()].sort((a, b) => b.latestOrders - a.latestOrders || a.group.localeCompare(b.group));
  return { months: monthList, groups: groupList };
}

/** An expanded row's marketplace breakdown (Amazon, Flipkart, ...) - same
 * month columns, fetched when the row is opened. */
function ChannelRows({
  level,
  group,
  colSpan,
  renderCells,
  indent = 4.5,
  filter,
}: {
  level: Level;
  group: string;
  colSpan: number;
  renderCells: RenderCells;
  indent?: number;
  filter?: CategoryFilter;
}) {
  const { data, isLoading, isError } = useCategoryAnalysisChannels(level, group, filter);
  const channels = useMemo(() => pivotRows(data?.rows ?? []).groups, [data]);
  const stickyCell = {
    ...groupCell, position: "sticky", left: 0, zIndex: 1, bgcolor: CHANNEL_BG, ...STICKY_EDGE_SX, pl: indent,
  } as const;
  if (isLoading || isError || channels.length === 0) {
    return (
      <TableRow>
        <TableCell sx={stickyCell}>
          <Typography variant="caption" sx={{ color: isError ? DOWN_COLOR : "text.secondary" }}>
            {isLoading ? "Loading channels…" : isError ? "Couldn't load channels" : "No channel data"}
          </Typography>
        </TableCell>
        <TableCell colSpan={colSpan} sx={{ bgcolor: CHANNEL_BG }} />
      </TableRow>
    );
  }
  return (
    <>
      {channels.map((c) => (
        <TableRow key={c.group}>
          <TableCell sx={stickyCell}>
            <Typography variant="body2" sx={{ color: "text.secondary", fontWeight: 600 }}>{c.group}</Typography>
          </TableCell>
          {renderCells(c.months, false, "channel", { group, channel: c.group })}
        </TableRow>
      ))}
    </>
  );
}

/** Category Analysis page: one row per Category / Sub Category, one column
 * group per month (April 2025 to now) - styles sold, orders, units and the
 * month-on-month spike in units sold. */
/** One page per level: the app's SUB CATEGORY, CATEGORY and TIERS SPIKE RATE
 * (one row per style, with tier buttons) tabs. */
export default function CategoryAnalysis({ level }: { level: Level }) {
  const [tier, setTier] = useState("all");
  const [page, setPage] = useState(0);
  // Rows opened to show their channel (marketplace) breakdown.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  // The STYLES cell whose styles are shown in the popup (2026-10-29, user-requested).
  const [stylesSel, setStylesSel] = useState<StylesCellSelection | null>(null);
  const toggleExpanded = (group: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  // Brand / Tier / Category / Sub Category / Style filters on the table, its
  // platform rows, the styles popup and the chart - every tab (2026-10-30,
  // user-requested; before, only the Tiers tab had Category / Sub Category).
  const [styleFilters, setStyleFilters] = useState<StyleFilters>(NO_STYLE_FILTERS);
  // Tier as: the style's current tier for every month, or the tier it had at
  // the time of each sale (Cloud SQL tier history, 2026-10-13, user-requested).
  const [tierMode, setTierMode] = useState<"current" | "sale">("current");
  const tableFilter = useMemo<CategoryFilter>(
    () => ({ ...styleFilters, tierMode: level === "tier" ? tierMode : "current" }),
    [level, styleFilters, tierMode],
  );
  const { data, isLoading, isError, refetch } = useCategoryAnalysis(level, tableFilter);
  const isStyle = level === "style";
  // Tiers Spike Rate (2026-10-09, user-requested): one row per tier, each
  // opening into its platform-wise (marketplace) split like the other tabs
  // (2026-10-13, user-requested - replaces the tier -> styles drill-down).
  const isTier = level === "tier";
  const groupName = level === "category" ? "Category" : level === "style" ? "Style" : isTier ? "Tier" : "Sub Category";
  const metrics = isStyle
    ? METRICS.filter((mt) => mt.key !== "styles")
    : METRICS;

  // Catalog tier per style (Cloud SQL CatalogStyle.forecastStatus) - the same
  // tier lists as the Weekly Sales Report and Production Log.
  const catalogQuery = useCatalogStyleTiers();
  const tierOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const g of catalogQuery.data?.tiers ?? []) for (const st of g.styles) map.set(st.name, g.tier);
    return map;
  }, [catalogQuery.data]);
  const dataThrough = data?.dataThrough ?? "";
  const partialMonth = data?.partialMonth ?? "";

  // Pivot the flat (month, group) rows: one row per group, months oldest -> newest.
  const { months, groups } = useMemo(() => {
    const pivot = pivotRows((data?.rows ?? []) as CategoryAnalysisRow[]);
    if (isTier) pivot.groups.sort((a, b) => tierRank(a.group) - tierRank(b.group) || a.group.localeCompare(b.group));
    return pivot;
  }, [data, isTier]);

  // Tiers tab: how many styles each tier has (the chip on its row).
  const stylesPerTier = useMemo(() => {
    const out = new Map<string, number>();
    for (const t of Object.values(data?.tierOf ?? {})) out.set(t, (out.get(t) ?? 0) + 1);
    return out;
  }, [data]);

  const filtered = useMemo(
    () => groups.filter(
      (g) => (!isStyle || tier === "all" || tierOf.get(g.group) === tier),
    ),
    [groups, isStyle, tier, tierOf],
  );
  // Tier buttons: number of styles per tier that appear in this table.
  const tierCounts = useMemo(() => {
    if (!isStyle) return [];
    const counts = new Map<string, number>();
    for (const g of groups) {
      const t = tierOf.get(g.group);
      if (t) counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    return (catalogQuery.data?.tiers ?? []).map((g) => ({ tier: g.tier, count: counts.get(g.tier) ?? 0 })).filter((g) => g.count > 0);
  }, [isStyle, groups, tierOf, catalogQuery.data]);
  const pageRows = isStyle ? filtered.slice(page * STYLE_PAGE_SIZE, (page + 1) * STYLE_PAGE_SIZE) : filtered;

  // TOTAL row across the shown groups; its spike is recomputed from summed orders.
  const totals = useMemo(() => {
    const out: Record<string, Cell> = {};
    for (const m of months) {
      const t: Cell = { styles: 0, orders: 0, units: 0, prevUnits: 0, changePct: null };
      let hasSame = false;
      const same = { stylesBoth: 0, sameUnits: 0, samePrevUnits: 0, newStyles: 0, newUnits: 0, stoppedStyles: 0 };
      for (const g of filtered) {
        const c = g.months[m];
        if (!c) continue;
        t.styles += c.styles;
        t.orders += c.orders;
        t.units += c.units;
        t.prevUnits += c.prevUnits;
        if (c.sameChangePct !== undefined) {
          hasSame = true;
          same.stylesBoth += c.stylesBoth ?? 0;
          same.sameUnits += c.sameUnits ?? 0;
          same.samePrevUnits += c.samePrevUnits ?? 0;
          same.newStyles += c.newStyles ?? 0;
          same.newUnits += c.newUnits ?? 0;
          same.stoppedStyles += c.stoppedStyles ?? 0;
        }
      }
      t.changePct = pct(t.units, t.prevUnits);
      if (hasSame) Object.assign(t, same, { sameChangePct: pct(same.sameUnits, same.samePrevUnits) });
      out[m] = t;
    }
    return out;
  }, [months, filtered]);

  // Open scrolled to the latest month (right end), like reading the newest data first.
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
  }, [months.length, level]);

  const handleExport = () => {
    const rows = [...filtered.map((g) => ({ name: g.group, months: g.months })), { name: "TOTAL", months: totals }].map(({ name, months: cells }) => {
      const out: Record<string, unknown> = { [groupName]: name };
      if (isStyle) {
        out.Tier = tierOf.get(name) ?? "";
        out["Sub Category"] = data?.subCategoryOf?.[name] ?? "";
      }
      for (const m of months) {
        const c = cells[m];
        const label = shortMonth(m) + (m === partialMonth ? ` (to ${dayLabel(dataThrough)})` : "");
        if (!isStyle) out[`${label} Styles`] = c?.styles ?? "";
        out[`${label} Orders`] = c?.orders ?? "";
        out[`${label} Units`] = c?.units ?? "";
        if (!isStyle) {
          const ups = unitsPerStyle(c);
          out[`${label} Units/Style`] = ups === null ? "" : Math.round(ups * 10) / 10;
        }
        out[`${label} Spike % (same styles)`] = c ? spikeOf(c) ?? "" : "";
        out[`${label} Spike % (total units)`] = c?.changePct ?? "";
        if (!isStyle) {
          out[`${label} New styles`] = c?.newStyles ?? "";
          out[`${label} Stopped styles`] = c?.stoppedStyles ?? "";
        }
      }
      return out;
    });
    downloadCsv(rowsToCsv(rows), isStyle || isTier ? "tiers_spike_rate" : `category_analysis_${level}`);
  };

  const renderCells: RenderCells = (cells, bold, variant = "main", ctx) =>
    months.map((m, i) => {
      const c = cells[m];
      const bg = variant === "channel" ? CHANNEL_BG : variant === "child" ? STYLE_ROW_BG : MONTH_BG[i % 2];
      const base = {
        ...numCell, ...(bold ? REPORT_TOTAL_SX : { bgcolor: bg }), fontWeight: bold ? 700 : 400,
        ...(variant === "main" ? {} : { fontSize: "0.8rem" }),
      };
      return (
        <Fragment key={m}>
          {!isStyle && (
            <TableCell sx={{ ...base, color: STYLE_COLOR }}>
              {!c ? "—" : (
                <Tooltip
                  title={c.newStyles !== undefined
                    ? `${shortMonth(prevMonth(m))}: ${formatNumber(c.styles - c.newStyles + (c.stoppedStyles ?? 0))} styles → ${shortMonth(m)}${m === partialMonth ? " so far" : ""}: ${formatNumber(c.styles)} · ${formatNumber(c.newStyles)} new (did not sell in ${shortMonth(prevMonth(m))}) · ${formatNumber(c.stoppedStyles ?? 0)} stopped (sold in ${shortMonth(prevMonth(m))}, not ${m === partialMonth ? "yet " : ""}in ${shortMonth(m)})`
                    + " · click to see the styles"
                    : "Click to see the styles"}
                >
                  <Box
                    component="button"
                    type="button"
                    onClick={() => ctx && setStylesSel({ group: ctx.group, channel: ctx.channel, month: m })}
                    sx={{
                      all: "unset", cursor: "pointer", borderRadius: 0.5, px: 0.25,
                      textDecoration: "underline dotted", textUnderlineOffset: 3,
                      "&:hover": { color: REPORT.actual, bgcolor: COLORS.primaryBgLight, textDecorationStyle: "solid" },
                      "&:focus-visible": { outline: 2, outlineColor: REPORT.actual },
                    }}
                  >
                    {formatNumber(c.styles)}
                    {c.newStyles !== undefined && (c.newStyles > 0 || (c.stoppedStyles ?? 0) > 0) && (
                      <Box component="span" sx={{ color: "text.secondary", fontWeight: 400, fontSize: "0.85em", ml: 0.5 }}>
                        (+{formatNumber(c.newStyles)} / −{formatNumber(c.stoppedStyles ?? 0)})
                      </Box>
                    )}
                  </Box>
                </Tooltip>
              )}
            </TableCell>
          )}
          <TableCell sx={{ ...base, color: ORDER_COLOR, fontWeight: bold ? 800 : 600 }}>{c ? formatNumber(c.orders) : "—"}</TableCell>
          <TableCell sx={{ ...base, color: UNIT_COLOR }}>{c ? formatNumber(c.units) : "—"}</TableCell>
          <TableCell sx={{ ...base, borderRight: 1, borderRightColor: "divider" }}>
            <Spike cell={c} month={m} partial={m === partialMonth} dataThrough={dataThrough} />
          </TableCell>
        </Fragment>
      );
    });

  return (
    <Box>
      <PageHeader
        title={isStyle || isTier ? "Tiers Spike Rate" : `${groupName} Analysis`}
      />
      {isLoading ? (
        <LoadingSkeleton />
      ) : isError ? (
        <ErrorState message="Couldn't load the category analysis." onRetry={() => refetch()} />
      ) : (
        <>
          {/* No search box or Style filter on Category / Sub Category (2026-10-30, user-requested) */}
          <StyleFilterBar
            showStyle={isTier}
            value={styleFilters}
            onChange={(v) => {
              setStyleFilters(v);
              setPage(0);
            }}
            actions={
              <>
                {isTier && (
                  <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
                    <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700 }}>TIER AS:</Typography>
                    <ToggleButtonGroup
                      exclusive
                      size="small"
                      value={tierMode}
                      onChange={(_e, v) => v && setTierMode(v)}
                      sx={{ "& .MuiToggleButton-root": { textTransform: "none", fontWeight: 700, px: 1.25, py: 0.4 } }}
                    >
                      <ToggleButton value="current">Current tier</ToggleButton>
                      <ToggleButton value="sale">Tier Log</ToggleButton>
                    </ToggleButtonGroup>
                    {tierMode === "sale" && (
                      <Tooltip title="Tier history starts 1 Jun 2026: from then, each sale counts under the tier the style had that day. Earlier sales use the style's current tier.">
                        <Typography variant="caption" sx={{ color: "text.secondary" }}>from Jun 2026 ⓘ</Typography>
                      </Tooltip>
                    )}
                  </Stack>
                )}
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<DownloadIcon fontSize="small" />}
                  onClick={handleExport}
                  disabled={filtered.length === 0}
                >
                  Export CSV
                </Button>
              </>
            }
          />

          {isStyle && tierCounts.length > 0 && (
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center", mb: 2 }}>
              <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, mr: 0.5 }}>
                TIER:
              </Typography>
              {[{ tier: "all", count: groups.length }, ...tierCounts].map((o) => {
                const active = tier === o.tier;
                return (
                  <Button
                    key={o.tier}
                    onClick={() => {
                      setTier(o.tier);
                      setPage(0);
                    }}
                    variant={active ? "contained" : "outlined"}
                    size="small"
                    sx={{
                      borderRadius: 999, textTransform: "none", fontWeight: 700, px: 1.75,
                      ...(active ? { bgcolor: ORDER_COLOR, "&:hover": { bgcolor: ORDER_COLOR } } : { color: ORDER_COLOR, borderColor: ORDER_COLOR }),
                    }}
                  >
                    {o.tier === "all" ? "All Tiers" : o.tier}
                    <Chip
                      label={formatNumber(o.count)}
                      size="small"
                      sx={{
                        ml: 0.75, height: 18, fontSize: "0.68rem", fontWeight: 700,
                        bgcolor: active ? "rgba(255,255,255,0.25)" : "rgba(49,44,92,0.1)",
                        color: active ? "#fff" : ORDER_COLOR,
                      }}
                    />
                  </Button>
                );
              })}
            </Box>
          )}

          <Paper variant="outlined">
            <TableContainer ref={scrollRef} sx={{ maxHeight: "72vh" }}>
              <Table size="small" stickyHeader sx={{ width: "max-content", "& td, & th": { borderColor: "divider" } }}>
                <TableHead>
                  <TableRow>
                    <TableCell
                      rowSpan={2}
                      sx={{ ...groupCell, left: 0, zIndex: 4, ...REPORT_HEAD_SX, ...STICKY_EDGE_SX, verticalAlign: "bottom" }}
                    >
                      {groupName.toUpperCase()}
                    </TableCell>
                    {months.map((m) => (
                      <TableCell
                        key={m}
                        colSpan={metrics.length}
                        align="center"
                        sx={{ ...REPORT_GROUP_HEAD_SX, borderRight: 1, borderRightColor: "divider", height: ROW_H1 }}
                      >
                        {monthTitle(m)}
                        {m === partialMonth && (
                          <Typography component="span" variant="caption" sx={{ ml: 0.75, color: REPORT.forecastText, fontWeight: 700 }}>
                            (to {dayLabel(dataThrough)})
                          </Typography>
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                  <TableRow>
                    {months.map((m) => (
                      <Fragment key={m}>
                        {metrics.map((mt) => (
                          <TableCell
                            key={mt.key}
                            sx={{
                              ...numCell, top: ROW_H1, ...REPORT_HEAD_SX, fontSize: "0.68rem", color: mt.color,
                              ...(mt.key === "spike" ? { borderRight: 1, borderRightColor: "divider" } : {}),
                            }}
                          >
                            {mt.label}
                          </TableCell>
                        ))}
                      </Fragment>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {pageRows.map((g) => {
                    const open = expanded.has(g.group);
                    return (
                      <Fragment key={g.group}>
                        <TableRow hover>
                          <TableCell
                            onClick={() => toggleExpanded(g.group)}
                            title={open ? "Hide platform-wise sales" : "Show platform-wise sales"}
                            sx={{
                              ...groupCell, position: "sticky", left: 0, bgcolor: "background.paper", zIndex: 1, ...STICKY_EDGE_SX,
                              cursor: "pointer", userSelect: "none", "&:hover": { bgcolor: COLORS.primaryBgLight },
                            }}
                          >
                            <Stack direction="row" sx={{ alignItems: "center", flexWrap: "wrap", columnGap: 0.5, rowGap: 0.25 }}>
                              {open ? (
                                <KeyboardArrowDownIcon fontSize="small" sx={{ color: ORDER_COLOR, ml: -0.75 }} />
                              ) : (
                                <KeyboardArrowRightIcon fontSize="small" sx={{ color: "text.secondary", ml: -0.75 }} />
                              )}
                              <Typography variant="body2" sx={{ fontWeight: 700 }}>{g.group}</Typography>
                              {isTier && stylesPerTier.has(g.group) && (
                                <Chip
                                  label={`${formatNumber(stylesPerTier.get(g.group) ?? 0)} styles`}
                                  size="small"
                                  sx={{ height: 18, fontSize: "0.65rem", fontWeight: 700, bgcolor: "rgba(49,44,92,0.1)", color: ORDER_COLOR }}
                                />
                              )}
                              {isStyle && tierOf.get(g.group) && (
                                <Chip
                                  label={tierOf.get(g.group)}
                                  size="small"
                                  sx={{ height: 18, fontSize: "0.65rem", fontWeight: 700, bgcolor: "rgba(49,44,92,0.1)", color: ORDER_COLOR }}
                                />
                              )}
                            </Stack>
                            {isStyle && data?.subCategoryOf?.[g.group] && (
                              <Typography variant="caption" sx={{ color: "text.secondary", pl: 2.25 }}>{data.subCategoryOf[g.group]}</Typography>
                            )}
                          </TableCell>
                          {renderCells(g.months, false, "main", { group: g.group })}
                        </TableRow>
                        {open && (
                          <ChannelRows level={level} group={g.group} colSpan={months.length * metrics.length} renderCells={renderCells} filter={tableFilter} />
                        )}
                      </Fragment>
                    );
                  })}
                  <TableRow sx={{ "& td": { position: "sticky", bottom: 0 } }}>
                    <TableCell sx={{ ...groupCell, left: 0, zIndex: 3, ...REPORT_TOTAL_SX, ...STICKY_EDGE_SX, fontWeight: 700 }}>
                      TOTAL
                      <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                        {formatNumber(filtered.length)} {level === "category" ? "categories" : isStyle ? "styles" : isTier ? "tiers" : "sub categories"}
                      </Typography>
                    </TableCell>
                    {renderCells(totals, true, "main", { group: "" })}
                  </TableRow>
                </TableBody>
              </Table>
            </TableContainer>
            {isStyle && (
              <TablePagination
                component="div"
                count={filtered.length}
                page={Math.min(page, Math.max(0, Math.ceil(filtered.length / STYLE_PAGE_SIZE) - 1))}
                onPageChange={(_e, p) => setPage(p)}
                rowsPerPage={STYLE_PAGE_SIZE}
                rowsPerPageOptions={[STYLE_PAGE_SIZE]}
              />
            )}
          </Paper>

          <CategoryStylesDialog level={level} sel={stylesSel} filter={tableFilter} onClose={() => setStylesSel(null)} />

          <Box sx={{ mt: 3 }}>
            {isTier ? (
              <TierSpikeChart months={months} partialMonth={partialMonth} filters={styleFilters} />
            ) : (
              <CategoryAnalysisCharts
                key={`${level}-${tier}`}
                months={months}
                groups={filtered}
                totals={totals}
                partialMonth={partialMonth}
                groupName={groupName}
              />
            )}
          </Box>
        </>
      )}
    </Box>
  );
}
