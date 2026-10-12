import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Box,
  Button,
  Chip,
  InputAdornment,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import DownloadIcon from "@mui/icons-material/Download";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import PageHeader from "@/components/common/PageHeader";
import CategoryAnalysisCharts from "@/components/charts/CategoryAnalysisCharts";
import { rowsToCsv, downloadCsv } from "@/components/common/ExportButton";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { useCatalogStyleTiers, useCategoryAnalysis, useCategoryAnalysisChannels } from "@/hooks/useDashboardData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { formatNumber } from "@/utils/format";
import type { CategoryAnalysisRow } from "@/types";

type Level = "category" | "subCategory" | "style" | "tier";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const HEADER_BG = "#eef0f5";
const MONTH_BG = ["#ffffff", "#f4f5f9"]; // alternating month column groups, like the Weekly Sales Report
const STYLE_COLOR = "#475569";
const ORDER_COLOR = "#312c5c";
const UNIT_COLOR = "#f97316";
const UP_COLOR = "#15803d";
const DOWN_COLOR = "#dc2626";
const GROUP_COL_WIDTH = 220;
const NUM_COL_WIDTH = 84;
const ROW_H1 = 38; // month header row height (the second header row sticks below it)
const STYLE_PAGE_SIZE = 100; // style level: ~1,500 rows, rendered one page at a time

const METRICS = [
  { key: "styles", label: "STYLES", color: STYLE_COLOR },
  { key: "orders", label: "ORDERS", color: ORDER_COLOR },
  // 2026-10-12, user-requested: the units cell also shows the average pieces
  // per selling style (Units ÷ Styles) in brackets, e.g. "1,39,589 (274)".
  { key: "units", label: "UNITS (PER STYLE)", color: UNIT_COLOR },
  { key: "spike", label: "SPIKE", color: STYLE_COLOR },
] as const;

/** Units ÷ Styles - average pieces per style that sold that month (null when no style sold). */
function unitsPerStyle(c: { styles: number; units: number } | undefined): number | null {
  return c && c.styles > 0 ? c.units / c.styles : null;
}

/** Whole pieces from 10 up; one decimal below, so small averages still differ (e.g. 2.5). */
function formatPerStyle(v: number | null): string {
  if (v === null) return "—";
  return v >= 10 ? formatNumber(v) : (Math.round(v * 10) / 10).toFixed(1);
}

interface Cell {
  styles: number;
  orders: number;
  units: number;
  prevUnits: number;
  changePct: number | null;
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

/** Order spike vs the previous month: green up / red down. */
function Spike({ cell, month, partial, dataThrough }: { cell: Cell | undefined; month: string; partial: boolean; dataThrough: string }) {
  if (!cell) return <>—</>;
  const compared = partial
    ? `${shortMonth(prevMonth(month))} 1–${Number(dataThrough.split("-")[2])} (same days)`
    : shortMonth(prevMonth(month));
  if (cell.changePct === null) {
    return (
      <Tooltip title={`No orders in ${compared} to compare with`}>
        <Typography component="span" variant="body2" sx={{ color: "text.disabled" }}>new</Typography>
      </Tooltip>
    );
  }
  const up = cell.changePct >= 0;
  return (
    <Tooltip title={`${formatNumber(cell.units)} units vs ${formatNumber(cell.prevUnits)} in ${compared}`}>
      <Box component="span" sx={{ fontWeight: 700, color: up ? UP_COLOR : DOWN_COLOR }}>
        {up ? "▲ +" : "▼ "}
        {cell.changePct.toFixed(1)}%
      </Box>
    </Tooltip>
  );
}

const numCell = {
  px: 1, py: 0.75, textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
  width: NUM_COL_WIDTH, minWidth: NUM_COL_WIDTH,
} as const;
const groupCell = { width: GROUP_COL_WIDTH, minWidth: GROUP_COL_WIDTH, maxWidth: GROUP_COL_WIDTH } as const;
const CHANNEL_BG = "#faf9ff"; // an expanded row's marketplace sub-rows
const STYLE_ROW_BG = "#fcfcfe"; // an expanded tier's style rows

/** main = a table row, child = a style inside an opened tier, channel = a marketplace row. */
type RowVariant = "main" | "child" | "channel";

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
    g.months[r.month] = { styles: r.styles, orders: r.orders, units: r.units, prevUnits: r.prevUnits, changePct: r.changePct };
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
}: {
  level: Level;
  group: string;
  colSpan: number;
  renderCells: (cells: Record<string, Cell>, bold: boolean, variant?: RowVariant) => ReactNode;
  indent?: number;
}) {
  const { data, isLoading, isError } = useCategoryAnalysisChannels(level, group);
  const channels = useMemo(() => pivotRows(data?.rows ?? []).groups, [data]);
  const stickyCell = {
    ...groupCell, position: "sticky", left: 0, zIndex: 1, bgcolor: CHANNEL_BG, borderRight: 1, borderRightColor: "divider", pl: indent,
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
          {renderCells(c.months, false, "channel")}
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
  const [searchText, setSearchText] = useState("");
  const search = useDebouncedValue(searchText, 200);
  const [tier, setTier] = useState("all");
  const [page, setPage] = useState(0);
  // Rows opened to show their channel (marketplace) breakdown.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggleExpanded = (group: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  const { data, isLoading, isError, refetch } = useCategoryAnalysis(level);
  const isStyle = level === "style";
  // Tiers Spike Rate (2026-10-09, user-requested): one row per tier, each
  // opening into its styles (the style-level rows), each style into its channels.
  const isTier = level === "tier";
  const styleQuery = useCategoryAnalysis(isTier ? "style" : level);
  const [expandedStyles, setExpandedStyles] = useState<Set<string>>(() => new Set());
  const toggleStyle = (style: string) =>
    setExpandedStyles((prev) => {
      const next = new Set(prev);
      if (next.has(style)) next.delete(style);
      else next.add(style);
      return next;
    });
  const groupName = level === "category" ? "Category" : level === "style" ? "Style" : isTier ? "Tier" : "Sub Category";
  const metrics = isStyle
    ? METRICS.filter((mt) => mt.key !== "styles").map((mt) => (mt.key === "units" ? { ...mt, label: "UNITS" } : mt))
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

  // Tiers tab: each tier's styles (biggest latest-month orders first), matching the search.
  const q = search.trim().toLowerCase();
  const stylesByTier = useMemo(() => {
    const out = new Map<string, GroupRow[]>();
    if (!isTier) return out;
    for (const st of pivotRows((styleQuery.data?.rows ?? []) as CategoryAnalysisRow[]).groups) {
      if (q && !st.group.toLowerCase().includes(q)) continue;
      const t = data?.tierOf?.[st.group] ?? "Unclassified";
      if (!out.has(t)) out.set(t, []);
      out.get(t)!.push(st);
    }
    return out;
  }, [isTier, styleQuery.data, data, q]);

  const filtered = useMemo(() => {
    if (isTier) return q ? groups.filter((g) => stylesByTier.has(g.group)) : groups; // search finds styles
    return groups.filter(
      (g) => (!q || g.group.toLowerCase().includes(q)) && (!isStyle || tier === "all" || tierOf.get(g.group) === tier),
    );
  }, [groups, q, isStyle, isTier, stylesByTier, tier, tierOf]);
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
      for (const g of filtered) {
        const c = g.months[m];
        if (!c) continue;
        t.styles += c.styles;
        t.orders += c.orders;
        t.units += c.units;
        t.prevUnits += c.prevUnits;
      }
      t.changePct = pct(t.units, t.prevUnits);
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
        out[`${label} Spike %`] = c?.changePct ?? "";
      }
      return out;
    });
    if (isTier) {
      for (const g of filtered) {
        for (const st of stylesByTier.get(g.group) ?? []) {
          const out: Record<string, unknown> = { Tier: `${g.group} · ${st.group}` };
          for (const m of months) {
            const c = st.months[m];
            const label = shortMonth(m) + (m === partialMonth ? ` (to ${dayLabel(dataThrough)})` : "");
            out[`${label} Styles`] = c ? 1 : "";
            out[`${label} Orders`] = c?.orders ?? "";
            out[`${label} Units`] = c?.units ?? "";
            out[`${label} Units/Style`] = c?.units ?? "";
            out[`${label} Spike %`] = c?.changePct ?? "";
          }
          rows.push(out);
        }
      }
    }
    downloadCsv(rowsToCsv(rows), isStyle || isTier ? "tiers_spike_rate" : `category_analysis_${level}`);
  };

  const renderCells = (cells: Record<string, Cell>, bold: boolean, variant: RowVariant = "main") =>
    months.map((m, i) => {
      const c = cells[m];
      const bg = variant === "channel" ? CHANNEL_BG : variant === "child" ? STYLE_ROW_BG : MONTH_BG[i % 2];
      const base = {
        ...numCell, bgcolor: bold ? HEADER_BG : bg, fontWeight: bold ? 800 : 400,
        ...(variant === "main" ? {} : { fontSize: "0.8rem" }),
      };
      return (
        <Fragment key={m}>
          {!isStyle && (
            <TableCell sx={{ ...base, color: STYLE_COLOR }}>
              {/* a single style's own "styles" count is always 1 - leave it blank */}
              {c && !(isTier && variant !== "main") ? formatNumber(c.styles) : variant === "main" || !c ? "—" : ""}
            </TableCell>
          )}
          <TableCell sx={{ ...base, color: ORDER_COLOR, fontWeight: bold ? 800 : 600 }}>{c ? formatNumber(c.orders) : "—"}</TableCell>
          <TableCell sx={{ ...base, color: UNIT_COLOR, whiteSpace: "nowrap" }}>
            {!c ? "—" : isStyle || (isTier && variant !== "main") || c.styles <= 0 ? (
              // a single style: units per style would just repeat its units
              formatNumber(c.units)
            ) : (
              <Tooltip
                title={`${formatNumber(c.units)} units ÷ ${formatNumber(c.styles)} styles = ${formatPerStyle(unitsPerStyle(c))} units per style${m === partialMonth ? ` (month so far, to ${dayLabel(dataThrough)})` : ""}`}
              >
                <span>
                  {formatNumber(c.units)}{" "}
                  <Box component="span" sx={{ color: STYLE_COLOR, fontWeight: bold ? 700 : 500 }}>({formatPerStyle(unitsPerStyle(c))})</Box>
                </span>
              </Tooltip>
            )}
          </TableCell>
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
          <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
            <TextField
              size="small"
              placeholder={`Search ${isTier ? "style" : groupName.toLowerCase()}…`}
              value={searchText}
              onChange={(e) => {
                setSearchText(e.target.value);
                setPage(0);
              }}
              InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }}
              sx={{ minWidth: 260 }}
            />
            <Button
              size="small"
              variant="outlined"
              startIcon={<DownloadIcon fontSize="small" />}
              onClick={handleExport}
              disabled={filtered.length === 0}
              sx={{ ml: "auto" }}
            >
              Export CSV
            </Button>
          </Stack>

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
                      sx={{ ...groupCell, left: 0, zIndex: 4, bgcolor: HEADER_BG, fontWeight: 800, borderRight: 1, borderRightColor: "divider" }}
                    >
                      {groupName.toUpperCase()}
                    </TableCell>
                    {months.map((m, i) => (
                      <TableCell
                        key={m}
                        colSpan={metrics.length}
                        align="center"
                        sx={{ bgcolor: i % 2 ? "#e6e9f1" : HEADER_BG, fontWeight: 800, borderRight: 1, borderRightColor: "divider", height: ROW_H1 }}
                      >
                        {monthTitle(m)}
                        {m === partialMonth && (
                          <Typography component="span" variant="caption" sx={{ ml: 0.75, color: "#c2410c", fontWeight: 700 }}>
                            (to {dayLabel(dataThrough)})
                          </Typography>
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                  <TableRow>
                    {months.map((m, i) => (
                      <Fragment key={m}>
                        {metrics.map((mt) => (
                          <TableCell
                            key={mt.key}
                            sx={{
                              ...numCell, top: ROW_H1, bgcolor: i % 2 ? "#e6e9f1" : HEADER_BG, color: mt.color,
                              fontWeight: 700, fontSize: "0.72rem",
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
                    // Searching the Tiers tab opens every tier that has a matching style.
                    const open = expanded.has(g.group) || (isTier && q !== "");
                    const tierStyles = isTier ? stylesByTier.get(g.group) ?? [] : [];
                    return (
                      <Fragment key={g.group}>
                        <TableRow hover>
                          <TableCell
                            onClick={() => toggleExpanded(g.group)}
                            title={isTier ? (open ? "Hide styles" : "Show this tier's styles") : open ? "Hide channel breakdown" : "Show channel breakdown"}
                            sx={{
                              ...groupCell, position: "sticky", left: 0, bgcolor: "background.paper", zIndex: 1, borderRight: 1, borderRightColor: "divider",
                              cursor: "pointer", userSelect: "none", "&:hover": { bgcolor: "#f4f3fb" },
                            }}
                          >
                            <Stack direction="row" spacing={0.5} alignItems="center">
                              {open ? (
                                <KeyboardArrowDownIcon fontSize="small" sx={{ color: ORDER_COLOR, ml: -0.75 }} />
                              ) : (
                                <KeyboardArrowRightIcon fontSize="small" sx={{ color: "text.secondary", ml: -0.75 }} />
                              )}
                              <Typography variant="body2" sx={{ fontWeight: 700 }}>{g.group}</Typography>
                              {isTier && styleQuery.data && (
                                <Chip
                                  label={`${formatNumber(tierStyles.length)} styles`}
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
                          {renderCells(g.months, false)}
                        </TableRow>
                        {open && !isTier && (
                          <ChannelRows level={level} group={g.group} colSpan={months.length * metrics.length} renderCells={renderCells} />
                        )}
                        {open && isTier && styleQuery.isLoading && (
                          <TableRow>
                            <TableCell sx={{ ...groupCell, position: "sticky", left: 0, zIndex: 1, bgcolor: STYLE_ROW_BG, borderRight: 1, borderRightColor: "divider", pl: 4 }}>
                              <Typography variant="caption" sx={{ color: "text.secondary" }}>Loading styles…</Typography>
                            </TableCell>
                            <TableCell colSpan={months.length * metrics.length} sx={{ bgcolor: STYLE_ROW_BG }} />
                          </TableRow>
                        )}
                        {open && isTier && tierStyles.map((st) => {
                          const stOpen = expandedStyles.has(st.group);
                          return (
                            <Fragment key={st.group}>
                              <TableRow hover>
                                <TableCell
                                  onClick={() => toggleStyle(st.group)}
                                  title={stOpen ? "Hide channel breakdown" : "Show channel breakdown"}
                                  sx={{
                                    ...groupCell, position: "sticky", left: 0, zIndex: 1, bgcolor: STYLE_ROW_BG, borderRight: 1, borderRightColor: "divider",
                                    pl: 3.5, cursor: "pointer", userSelect: "none", "&:hover": { bgcolor: "#f4f3fb" },
                                  }}
                                >
                                  <Stack direction="row" spacing={0.5} alignItems="center">
                                    {stOpen ? (
                                      <KeyboardArrowDownIcon sx={{ fontSize: 18, color: ORDER_COLOR, ml: -0.75 }} />
                                    ) : (
                                      <KeyboardArrowRightIcon sx={{ fontSize: 18, color: "text.secondary", ml: -0.75 }} />
                                    )}
                                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{st.group}</Typography>
                                  </Stack>
                                  {styleQuery.data?.subCategoryOf?.[st.group] && (
                                    <Typography variant="caption" sx={{ color: "text.secondary", pl: 2.25 }}>
                                      {styleQuery.data.subCategoryOf[st.group]}
                                    </Typography>
                                  )}
                                </TableCell>
                                {renderCells(st.months, false, "child")}
                              </TableRow>
                              {stOpen && (
                                <ChannelRows level="style" group={st.group} colSpan={months.length * metrics.length} renderCells={renderCells} indent={7} />
                              )}
                            </Fragment>
                          );
                        })}
                      </Fragment>
                    );
                  })}
                  <TableRow sx={{ "& td": { position: "sticky", bottom: 0 } }}>
                    <TableCell sx={{ ...groupCell, left: 0, zIndex: 2, bgcolor: HEADER_BG, fontWeight: 800, borderRight: 1, borderRightColor: "divider" }}>
                      TOTAL
                      <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                        {formatNumber(filtered.length)} {level === "category" ? "categories" : isStyle ? "styles" : isTier ? "tiers" : "sub categories"}
                      </Typography>
                    </TableCell>
                    {renderCells(totals, true)}
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

          <Box sx={{ mt: 3 }}>
            <CategoryAnalysisCharts
              key={`${level}-${tier}`}
              months={months}
              groups={filtered}
              totals={totals}
              partialMonth={partialMonth}
              groupName={groupName}
            />
          </Box>
        </>
      )}
    </Box>
  );
}
