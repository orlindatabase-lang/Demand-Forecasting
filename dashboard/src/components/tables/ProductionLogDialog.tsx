import { Fragment, useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import DownloadIcon from "@mui/icons-material/Download";
import CloseIcon from "@mui/icons-material/Close";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { rowsToCsv, downloadCsv } from "@/components/common/ExportButton";
import { useCatalogStyleTiers, useWeeklyProductionLog } from "@/hooks/useDashboardData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { formatNumber } from "@/utils/format";
import type { WeeklyProductionLogRow } from "@/types";

const ACTUAL_COLOR = "#312c5c";
const FORECAST_COLOR = "#f97316";
const SUGGESTED_COLOR = "#0f766e";
const AVAILABLE_COLOR = "#475569";
const HEADER_BG = "#eef0f5";
const STYLE_COL_WIDTH = 170;
const NUM_COL_WIDTH = 96;
const ROW_H1 = 36; // month header row height, for the sticky second/third rows

interface WeekInfo {
  weekStart: string;
  weekEnd: string;
  month: string;
  week: string;
  status: "open" | "settling" | "completed";
  capturedOn: string;
  updatedOn: string;
  actualOn: string; // data date of the latest actual (settling / completed weeks)
}

// Days after a week ends during which its actual keeps updating (late
// marketplace orders) - matches api/weekly_log.py's SETTLE_DAYS.
const SETTLE_DAYS = 14;

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const STATUS_CHIP = {
  open: { bg: "rgba(249,115,22,0.14)", fg: "#c2410c" },
  settling: { bg: "rgba(202,138,4,0.14)", fg: "#a16207" },
  completed: { bg: "rgba(21,128,61,0.12)", fg: "#15803d" },
} as const;

function statusLabel(w: WeekInfo): string {
  if (w.status === "open") return `Running · updated ${formatDay(w.updatedOn)}`;
  if (w.status === "settling") return `Settling · final ${formatDay(addDays(w.weekEnd, SETTLE_DAYS))}`;
  return "Completed";
}

interface StyleRow {
  style: string;
  subCategory: string;
  category: string;
  weeks: Record<string, WeeklyProductionLogRow>;
  latestSuggested: number;
}

function formatDay(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1]}`;
}

/** A completed week's actual, or the running week's sale so far. */
function ActualValue({ actual, soFar }: { actual: number | null; soFar: number | null }) {
  if (actual !== null) return <>{formatNumber(actual)}</>;
  if (soFar === null) return <>—</>;
  return (
    <>
      {formatNumber(soFar)}
      <Typography component="div" variant="caption" sx={{ color: "text.secondary", lineHeight: 1 }}>
        so far
      </Typography>
    </>
  );
}

const numCell = {
  px: 1.25, py: 0.75, textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
  width: NUM_COL_WIDTH, minWidth: NUM_COL_WIDTH,
} as const;
const styleCell = { width: STYLE_COL_WIDTH, minWidth: STYLE_COL_WIDTH, maxWidth: STYLE_COL_WIDTH } as const;

/** Production Log popup, opened from the Weekly Sales Report toolbar. */
export default function ProductionLogDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading, isError, refetch } = useWeeklyProductionLog(open);
  const [searchText, setSearchText] = useState("");
  const search = useDebouncedValue(searchText, 250);
  const [category, setCategory] = useState("all");
  const [subCategory, setSubCategory] = useState("all");
  const [tier, setTier] = useState("all");
  // Catalog tier per Style (Cloud SQL CatalogStyle.forecastStatus) - the
  // same source as the Weekly Sales Report's tier buttons.
  const catalogQuery = useCatalogStyleTiers();
  const tierOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const g of catalogQuery.data?.tiers ?? []) for (const st of g.styles) map.set(st.name, g.tier);
    return map;
  }, [catalogQuery.data]);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(100);

  // Pivot the flat (style, week) rows into one row per Style with a column
  // group per week, oldest week on the left.
  const { weeks, styles } = useMemo(() => {
    const weekMap = new Map<string, WeekInfo>();
    const styleMap = new Map<string, StyleRow>();
    for (const r of data ?? []) {
      if (!weekMap.has(r.week_start)) {
        weekMap.set(r.week_start, {
          weekStart: r.week_start, weekEnd: r.week_end, month: r.month, week: r.week,
          status: r.status, capturedOn: r.captured_on, updatedOn: r.updated_on ?? r.captured_on,
          actualOn: r.completed_on ?? "",
        });
      }
      let s = styleMap.get(r.style);
      if (!s) {
        s = { style: r.style, subCategory: r.sub_category ?? "", category: r.category ?? "", weeks: {}, latestSuggested: 0 };
        styleMap.set(r.style, s);
      }
      s.weeks[r.week_start] = r;
    }
    const weekList = [...weekMap.values()].sort((a, b) => a.weekStart.localeCompare(b.weekStart));
    const latest = weekList[weekList.length - 1]?.weekStart;
    for (const s of styleMap.values()) s.latestSuggested = latest ? s.weeks[latest]?.suggested_production_qty ?? 0 : 0;
    const styleList = [...styleMap.values()].sort((a, b) => b.latestSuggested - a.latestSuggested || a.style.localeCompare(b.style));
    return { weeks: weekList, styles: styleList };
  }, [data]);

  const categoryOptions = useMemo(() => [...new Set(styles.map((s) => s.category).filter(Boolean))].sort(), [styles]);
  const subCategoryOptions = useMemo(
    () => [...new Set(styles.filter((s) => category === "all" || s.category === category).map((s) => s.subCategory).filter(Boolean))].sort(),
    [styles, category],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return styles.filter(
      (s) =>
        (!q || s.style.toLowerCase().includes(q)) &&
        (category === "all" || s.category === category) &&
        (subCategory === "all" || s.subCategory === subCategory) &&
        (tier === "all" || tierOf.get(s.style) === tier),
    );
  }, [styles, search, category, subCategory, tier, tierOf]);

  // Tier buttons in catalog order; counts are the log's own Styles per tier.
  const tierCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of styles) {
      const t = tierOf.get(s.style);
      if (t) counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    return (catalogQuery.data?.tiers ?? []).map((g) => ({ tier: g.tier, count: counts.get(g.tier) ?? 0 })).filter((g) => g.count > 0);
  }, [styles, tierOf, catalogQuery.data]);

  const totals = useMemo(() => {
    const out: Record<string, { actual: number | null; soFar: number | null; forecast: number; available: number; suggested: number }> = {};
    for (const w of weeks) {
      let actual: number | null = null;
      let soFar: number | null = null;
      let forecast = 0;
      let available = 0;
      let suggested = 0;
      for (const s of filtered) {
        const r = s.weeks[w.weekStart];
        if (!r) continue;
        forecast += r.forecast_2m_qty;
        available += r.available_qty;
        suggested += r.suggested_production_qty;
        if (r.actual_qty !== null) actual = (actual ?? 0) + r.actual_qty;
        if (r.actual_so_far != null) soFar = (soFar ?? 0) + r.actual_so_far;
      }
      out[w.weekStart] = { actual, soFar, forecast, available, suggested };
    }
    return out;
  }, [weeks, filtered]);

  const months = useMemo(() => {
    const out: { month: string; count: number }[] = [];
    for (const w of weeks) {
      const last = out[out.length - 1];
      if (last && last.month === w.month) last.count++;
      else out.push({ month: w.month, count: 1 });
    }
    return out;
  }, [weeks]);


  const pageRows = filtered.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage);

  const handleExport = () => {
    const rows = filtered.map((s) => {
      const out: Record<string, unknown> = { Style: s.style, Tier: tierOf.get(s.style) ?? "", "Sub Category": s.subCategory, Category: s.category };
      for (const w of weeks) {
        const r = s.weeks[w.weekStart];
        const label = `${w.month} ${w.week}`;
        out[`${label} Actual${w.status === "open" ? " (so far)" : w.status === "settling" ? " (still updating)" : ""}`] = r?.actual_qty ?? r?.actual_so_far ?? "";
        out[`${label} Forecast (2 months)`] = r?.forecast_2m_qty ?? "";
        out[`${label} Available (stock + WIP)`] = r?.available_qty ?? "";
        out[`${label} Suggested Production (2 months)`] = r?.suggested_production_qty ?? "";
      }
      return out;
    });
    downloadCsv(rowsToCsv(rows), "weekly_production_log");
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xl">
      <DialogTitle sx={{ pr: 7 }}>
        <Typography component="span" variant="h6" sx={{ fontWeight: 800, display: "block" }}>
          Production Log
        </Typography>
        <Typography component="span" variant="body2" sx={{ color: "text.secondary", display: "block" }}>
          For each report week: the style's forecast for the next ~2 months, stock + WIP, suggested production and actual
          sale. Running weeks update with every data refresh. After a week ends it is "Settling" for 14 days - its actual still
          updates as late marketplace orders arrive - then "Completed" and locked. Keeps the latest 8 finished weeks.
        </Typography>
        <IconButton aria-label="Close" onClick={onClose} sx={{ position: "absolute", top: 12, right: 12 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
      {isLoading ? (
        <LoadingSkeleton />
      ) : isError ? (
        <ErrorState message="Couldn't load the production log." onRetry={() => refetch()} />
      ) : (
      <>

      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 2 }}>
        <TextField
          size="small"
          placeholder="Search style…"
          value={searchText}
          onChange={(e) => {
            setSearchText(e.target.value);
            setPage(0);
          }}
          InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }}
          sx={{ minWidth: 220 }}
        />
        <FormControl size="small" sx={{ minWidth: 180 }}>
          <InputLabel id="log-category-label">Category</InputLabel>
          <Select
            labelId="log-category-label"
            label="Category"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setSubCategory("all");
              setPage(0);
            }}
          >
            <MenuItem value="all">All Categories</MenuItem>
            {categoryOptions.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="log-subcategory-label">Sub Category</InputLabel>
          <Select
            labelId="log-subcategory-label"
            label="Sub Category"
            value={subCategory}
            onChange={(e) => {
              setSubCategory(e.target.value);
              setPage(0);
            }}
          >
            <MenuItem value="all">All Sub Categories</MenuItem>
            {subCategoryOptions.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </Select>
        </FormControl>
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
      </Box>

      {tierCounts.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center", mb: 2 }}>
          <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, mr: 0.5 }}>
            TIER:
          </Typography>
          {[{ tier: "all", count: styles.length }, ...tierCounts].map((g) => {
            const active = tier === g.tier;
            return (
              <Button
                key={g.tier}
                onClick={() => {
                  setTier(g.tier);
                  setPage(0);
                }}
                variant={active ? "contained" : "outlined"}
                size="small"
                sx={{
                  borderRadius: 999,
                  textTransform: "none",
                  fontWeight: 700,
                  px: 1.75,
                  ...(active
                    ? { bgcolor: ACTUAL_COLOR, "&:hover": { bgcolor: ACTUAL_COLOR } }
                    : { color: ACTUAL_COLOR, borderColor: ACTUAL_COLOR }),
                }}
              >
                {g.tier === "all" ? "All Tiers" : g.tier}
                <Chip
                  label={formatNumber(g.count)}
                  size="small"
                  sx={{
                    ml: 0.75,
                    height: 18,
                    fontSize: "0.68rem",
                    fontWeight: 700,
                    bgcolor: active ? "rgba(255,255,255,0.25)" : "rgba(49,44,92,0.1)",
                    color: active ? "#fff" : ACTUAL_COLOR,
                  }}
                />
              </Button>
            );
          })}
        </Box>
      )}

      {weeks.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 4, textAlign: "center", color: "text.secondary" }}>
          Nothing stored yet. The running week is saved on the next model refresh.
        </Paper>
      ) : (
        <Paper variant="outlined">
          <TableContainer sx={{ maxHeight: "60vh" }}>
            <Table size="small" stickyHeader sx={{ width: "max-content", "& td, & th": { borderColor: "divider" } }}>
              <TableHead>
                <TableRow>
                  <TableCell
                    rowSpan={3}
                    sx={{ ...styleCell, left: 0, zIndex: 4, bgcolor: HEADER_BG, fontWeight: 800, borderRight: 1 }}
                  >
                    STYLE
                  </TableCell>
                  {months.map((m) => (
                    <TableCell
                      key={m.month}
                      colSpan={m.count * 4}
                      align="center"
                      sx={{ bgcolor: HEADER_BG, fontWeight: 800, borderRight: 1, height: ROW_H1 }}
                    >
                      {m.month.toUpperCase()}
                    </TableCell>
                  ))}
                </TableRow>
                <TableRow>
                  {weeks.map((w) => (
                    <TableCell key={w.weekStart} colSpan={4} align="center" sx={{ top: ROW_H1, bgcolor: HEADER_BG, borderRight: 1, py: 0.5 }}>
                      <Stack direction="row" spacing={0.75} alignItems="center" justifyContent="center">
                        <Typography variant="body2" sx={{ fontWeight: 800 }}>{w.week}</Typography>
                        <Typography variant="caption" sx={{ color: "text.secondary" }}>
                          {formatDay(w.weekStart)}–{formatDay(w.weekEnd)}
                        </Typography>
                        <Chip
                          size="small"
                          label={statusLabel(w)}
                          title={
                            w.status === "settling"
                              ? `Week ended - late marketplace orders still arriving. Actual updates daily (data through ${formatDay(w.actualOn)}) until ${formatDay(addDays(w.weekEnd, SETTLE_DAYS))}.`
                              : undefined
                          }
                          sx={{
                            height: 18, fontSize: "0.65rem", fontWeight: 700,
                            bgcolor: STATUS_CHIP[w.status].bg,
                            color: STATUS_CHIP[w.status].fg,
                          }}
                        />
                      </Stack>
                    </TableCell>
                  ))}
                </TableRow>
                <TableRow>
                  {weeks.map((w) => (
                    <Fragment key={w.weekStart}>
                      <TableCell sx={{ ...numCell, top: ROW_H1 + 33, bgcolor: HEADER_BG, color: ACTUAL_COLOR, fontWeight: 700, fontSize: "0.72rem" }}>ACTUAL</TableCell>
                      <TableCell sx={{ ...numCell, top: ROW_H1 + 33, bgcolor: HEADER_BG, color: FORECAST_COLOR, fontWeight: 700, fontSize: "0.72rem", whiteSpace: "normal", lineHeight: 1.2 }}>
                        FORECAST
                        <Box component="span" sx={{ display: "block", fontWeight: 600, fontSize: "0.65rem" }}>NEXT 2 MONTHS</Box>
                      </TableCell>
                      <TableCell sx={{ ...numCell, top: ROW_H1 + 33, bgcolor: HEADER_BG, color: AVAILABLE_COLOR, fontWeight: 700, fontSize: "0.72rem", whiteSpace: "normal", lineHeight: 1.2 }}>
                        AVAILABLE
                        <Box component="span" sx={{ display: "block", fontWeight: 600, fontSize: "0.65rem" }}>STOCK + WIP</Box>
                      </TableCell>
                      <TableCell sx={{ ...numCell, top: ROW_H1 + 33, bgcolor: HEADER_BG, color: SUGGESTED_COLOR, fontWeight: 700, fontSize: "0.72rem", borderRight: 1 }}>
                        SUGGESTED
                      </TableCell>
                    </Fragment>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {pageRows.map((s) => (
                  <TableRow key={s.style} hover>
                    <TableCell sx={{ ...styleCell, position: "sticky", left: 0, bgcolor: "background.paper", zIndex: 1, borderRight: 1 }}>
                      <Stack direction="row" spacing={0.75} alignItems="center">
                        <Typography variant="body2" sx={{ fontWeight: 700 }}>{s.style}</Typography>
                        {tierOf.get(s.style) && (
                          <Chip
                            label={tierOf.get(s.style)}
                            size="small"
                            sx={{ height: 18, fontSize: "0.65rem", fontWeight: 700, bgcolor: "rgba(49,44,92,0.1)", color: ACTUAL_COLOR }}
                          />
                        )}
                      </Stack>
                      <Typography variant="caption" sx={{ color: "text.secondary" }}>{s.subCategory}</Typography>
                    </TableCell>
                    {weeks.map((w) => {
                      const r = s.weeks[w.weekStart];
                      return (
                        <Fragment key={w.weekStart}>
                          <TableCell sx={{ ...numCell, color: ACTUAL_COLOR }}>
                            <ActualValue actual={r?.actual_qty ?? null} soFar={r?.actual_so_far ?? null} />
                          </TableCell>
                          <TableCell sx={{ ...numCell, color: FORECAST_COLOR, fontWeight: 600 }}>{r ? formatNumber(r.forecast_2m_qty) : "—"}</TableCell>
                          <TableCell sx={{ ...numCell, color: AVAILABLE_COLOR }}>{r ? formatNumber(r.available_qty) : "—"}</TableCell>
                          <TableCell sx={{ ...numCell, color: SUGGESTED_COLOR, fontWeight: 700, borderRight: 1 }}>
                            {r ? formatNumber(r.suggested_production_qty) : "—"}
                          </TableCell>
                        </Fragment>
                      );
                    })}
                  </TableRow>
                ))}
                <TableRow sx={{ "& td": { position: "sticky", bottom: 0, bgcolor: HEADER_BG, fontWeight: 800 } }}>
                  <TableCell sx={{ ...styleCell, left: 0, zIndex: 2, borderRight: 1 }}>
                    TOTAL
                    <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                      {formatNumber(filtered.length)} styles
                    </Typography>
                  </TableCell>
                  {weeks.map((w) => {
                    const t = totals[w.weekStart];
                    return (
                      <Fragment key={w.weekStart}>
                        <TableCell sx={{ ...numCell, color: ACTUAL_COLOR }}>
                          <ActualValue actual={t.actual} soFar={t.soFar} />
                        </TableCell>
                        <TableCell sx={{ ...numCell, color: FORECAST_COLOR }}>{formatNumber(t.forecast)}</TableCell>
                        <TableCell sx={{ ...numCell, color: AVAILABLE_COLOR }}>{formatNumber(t.available)}</TableCell>
                        <TableCell sx={{ ...numCell, color: SUGGESTED_COLOR, borderRight: 1 }}>{formatNumber(t.suggested)}</TableCell>
                      </Fragment>
                    );
                  })}
                </TableRow>
              </TableBody>
            </Table>
          </TableContainer>
          <TablePagination
            component="div"
            count={filtered.length}
            page={page}
            onPageChange={(_e, p) => setPage(p)}
            rowsPerPage={rowsPerPage}
            rowsPerPageOptions={[50, 100, 200]}
            onRowsPerPageChange={(e) => {
              setRowsPerPage(parseInt(e.target.value, 10));
              setPage(0);
            }}
          />
        </Paper>
      )}
      <Typography variant="caption" sx={{ display: "block", mt: 1, color: "text.secondary" }}>
        Forecast is the style's total forecast for the ~2 months from the week, Available is its stock + WIP, and Suggested =
        Forecast − Available (0 when stock covers it). While a week is running these three update with every data refresh; when
        the week ends they are locked and its Actual is filled in. The Actual keeps updating for 14 days (late marketplace orders),
        then the week is completed and fully locked. Sorted by the latest week's suggested production.
      </Typography>
      </>
      )}
      </DialogContent>
    </Dialog>
  );
}
