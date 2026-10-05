import { Fragment, useEffect, useMemo, useRef, useState } from "react";
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
import PageHeader from "@/components/common/PageHeader";
import CategoryAnalysisCharts from "@/components/charts/CategoryAnalysisCharts";
import { rowsToCsv, downloadCsv } from "@/components/common/ExportButton";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { useCatalogStyleTiers, useCategoryAnalysis } from "@/hooks/useDashboardData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { formatNumber } from "@/utils/format";
import type { CategoryAnalysisRow } from "@/types";

type Level = "category" | "subCategory" | "style";

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
  { key: "units", label: "UNITS", color: UNIT_COLOR },
  { key: "spike", label: "SPIKE", color: STYLE_COLOR },
] as const;

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
  const { data, isLoading, isError, refetch } = useCategoryAnalysis(level);
  const isStyle = level === "style";
  const groupName = level === "category" ? "Category" : level === "style" ? "Style" : "Sub Category";
  const metrics = isStyle ? METRICS.filter((mt) => mt.key !== "styles") : METRICS;

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
    const monthSet = new Set<string>();
    const byGroup = new Map<string, GroupRow>();
    for (const r of (data?.rows ?? []) as CategoryAnalysisRow[]) {
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
  }, [data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return groups.filter(
      (g) => (!q || g.group.toLowerCase().includes(q)) && (!isStyle || tier === "all" || tierOf.get(g.group) === tier),
    );
  }, [groups, search, isStyle, tier, tierOf]);
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
        out[`${label} Spike %`] = c?.changePct ?? "";
      }
      return out;
    });
    downloadCsv(rowsToCsv(rows), isStyle ? "tiers_spike_rate" : `category_analysis_${level}`);
  };

  const renderCells = (cells: Record<string, Cell>, bold: boolean) =>
    months.map((m, i) => {
      const c = cells[m];
      const bg = MONTH_BG[i % 2];
      const base = { ...numCell, bgcolor: bold ? HEADER_BG : bg, fontWeight: bold ? 800 : 400 };
      return (
        <Fragment key={m}>
          {!isStyle && <TableCell sx={{ ...base, color: STYLE_COLOR }}>{c ? formatNumber(c.styles) : "—"}</TableCell>}
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
        title={isStyle ? "Tiers Spike Rate" : `${groupName} Analysis`}
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
              placeholder={`Search ${groupName.toLowerCase()}…`}
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
                  {pageRows.map((g) => (
                    <TableRow key={g.group} hover>
                      <TableCell sx={{ ...groupCell, position: "sticky", left: 0, bgcolor: "background.paper", zIndex: 1, borderRight: 1, borderRightColor: "divider" }}>
                        <Stack direction="row" spacing={0.75} alignItems="center">
                          <Typography variant="body2" sx={{ fontWeight: 700 }}>{g.group}</Typography>
                          {isStyle && tierOf.get(g.group) && (
                            <Chip
                              label={tierOf.get(g.group)}
                              size="small"
                              sx={{ height: 18, fontSize: "0.65rem", fontWeight: 700, bgcolor: "rgba(49,44,92,0.1)", color: ORDER_COLOR }}
                            />
                          )}
                        </Stack>
                        {isStyle && data?.subCategoryOf?.[g.group] && (
                          <Typography variant="caption" sx={{ color: "text.secondary" }}>{data.subCategoryOf[g.group]}</Typography>
                        )}
                      </TableCell>
                      {renderCells(g.months, false)}
                    </TableRow>
                  ))}
                  <TableRow sx={{ "& td": { position: "sticky", bottom: 0 } }}>
                    <TableCell sx={{ ...groupCell, left: 0, zIndex: 2, bgcolor: HEADER_BG, fontWeight: 800, borderRight: 1, borderRightColor: "divider" }}>
                      TOTAL
                      <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                        {formatNumber(filtered.length)} {level === "category" ? "categories" : isStyle ? "styles" : "sub categories"}
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
