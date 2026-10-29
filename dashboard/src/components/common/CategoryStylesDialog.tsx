import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import SearchIcon from "@mui/icons-material/Search";
import DownloadIcon from "@mui/icons-material/Download";
import { rowsToCsv, downloadCsv } from "@/components/common/ExportButton";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { useCategoryAnalysisStyles } from "@/hooks/useDashboardData";
import type { CategoryFilter } from "@/services/dataService";
import { formatNumber } from "@/utils/format";
import { COLORS, REPORT, REPORT_HEAD_SX } from "@/theme/colors";

type Level = "category" | "subCategory" | "style" | "tier";
export interface StylesCellSelection {
  group: string; // "" = the TOTAL row
  month: string; // "YYYY-MM"
  channel?: string; // a marketplace sub-row
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (ym: string) => {
  const [y, m] = ym.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
};
const dayLabel = (iso: string) => {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
};

const num = { textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" } as const;
const head = { ...REPORT_HEAD_SX, fontSize: "0.68rem", py: 1 } as const;

/** The styles behind one STYLES cell of the Sub Category / Category / Tiers
 * Spike Rate tables (2026-10-29, user-requested): which styles sold that
 * month and how many pieces, the new ones flagged, and the stopped ones. */
export default function CategoryStylesDialog({
  level,
  sel,
  filter,
  onClose,
}: {
  level: Level;
  sel: StylesCellSelection | null;
  filter?: CategoryFilter;
  onClose: () => void;
}) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const { data, isLoading, isError, refetch } = useCategoryAnalysisStyles(level, sel, filter);
  const [tab, setTab] = useState<"sold" | "stopped">("sold");
  const [search, setSearch] = useState("");
  const q = search.trim().toLowerCase();
  const showSub = level !== "subCategory";
  const showTier = level !== "tier";

  const sold = useMemo(
    () => (data?.styles ?? []).filter((s) => !q || s.style.toLowerCase().includes(q) || s.subCategory.toLowerCase().includes(q)),
    [data, q],
  );
  const stopped = useMemo(
    () => (data?.stopped ?? []).filter((s) => !q || s.style.toLowerCase().includes(q) || s.subCategory.toLowerCase().includes(q)),
    [data, q],
  );
  const totalUnits = (data?.styles ?? []).reduce((a, s) => a + s.units, 0);
  const newCount = (data?.styles ?? []).filter((s) => s.new).length;
  const prev = data ? monthLabel(data.prevMonth) : "";
  const title = [sel?.group || "TOTAL", sel?.channel].filter(Boolean).join(" · ");

  const handleClose = () => {
    setTab("sold");
    setSearch("");
    onClose();
  };
  const handleExport = () => {
    if (!data || !sel) return;
    const rows = tab === "sold"
      ? sold.map((s) => ({
          Style: s.style, "Sub Category": s.subCategory, Tier: s.tier, Units: s.units, Orders: s.orders,
          [`${prev} units`]: s.prevUnits, New: s.new ? "yes" : "",
        }))
      : stopped.map((s) => ({ Style: s.style, "Sub Category": s.subCategory, Tier: s.tier, [`${prev} units`]: s.prevUnits }));
    downloadCsv(rowsToCsv(rows), `styles_${(sel.group || "total").replace(/\W+/g, "_")}_${sel.month}_${tab}`);
  };

  return (
    <Dialog open={sel !== null} onClose={handleClose} fullScreen={phone} fullWidth maxWidth="md">
      <DialogTitle sx={{ pr: 6, pb: 1 }}>
        <Typography variant="subtitle1" component="div" sx={{ fontWeight: 800, color: REPORT.actual }}>
          {title}
        </Typography>
        <Typography variant="caption" component="div" sx={{ color: "text.secondary" }}>
          Styles sold in {sel ? monthLabel(sel.month) : ""}
          {data?.partial ? ` (to ${dayLabel(data.dataThrough)})` : ""} · compared with the whole of {prev}
        </Typography>
        <IconButton onClick={handleClose} aria-label="Close" sx={{ position: "absolute", right: 8, top: 8 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ p: { xs: 1.5, sm: 2 } }}>
        {isLoading ? (
          <LoadingSkeleton />
        ) : isError || !data ? (
          <ErrorState message="Couldn't load the styles." onRetry={() => refetch()} />
        ) : (
          <>
            <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1, mb: 1.5 }}>
              <Chip size="small" label={`${formatNumber(data.styles.length)} styles`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: REPORT.actual }} />
              <Chip size="small" label={`${formatNumber(totalUnits)} units`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: REPORT.forecastText }} />
              <Chip size="small" label={`+${formatNumber(newCount)} new`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: REPORT.good }} />
              <Chip size="small" label={`−${formatNumber(data.stopped.length)} stopped`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: REPORT.bad }} />
            </Stack>
            <Stack direction="row" sx={{ alignItems: "center", flexWrap: "wrap", gap: 1, mb: 1 }}>
              <Tabs value={tab} onChange={(_e, v) => setTab(v)} sx={{ minHeight: 36, "& .MuiTab-root": { minHeight: 36, textTransform: "none", fontWeight: 700 } }}>
                <Tab value="sold" label={`Sold (${formatNumber(data.styles.length)})`} />
                <Tab value="stopped" label={`Stopped (${formatNumber(data.stopped.length)})`} />
              </Tabs>
              <TextField
                size="small"
                placeholder="Search style…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> } }}
                sx={{ ml: { sm: "auto" }, minWidth: { xs: "100%", sm: 200 } }}
              />
              <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} onClick={handleExport}>
                Export CSV
              </Button>
            </Stack>
            {tab === "stopped" && (
              <Typography variant="caption" sx={{ display: "block", color: "text.secondary", mb: 1 }}>
                Sold in {prev}, not in {monthLabel(data.month)}{data.partial ? " so far" : ""}.
              </Typography>
            )}
            <TableContainer sx={{ maxHeight: { xs: "none", sm: "60vh" }, border: 1, borderColor: "divider", borderRadius: 1 }}>
              <Table size="small" stickyHeader sx={{ "& td, & th": { borderColor: "divider" } }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={head}>STYLE</TableCell>
                    {showSub && <TableCell sx={head}>SUB CATEGORY</TableCell>}
                    {showTier && <TableCell sx={head}>TIER</TableCell>}
                    {tab === "sold" && <TableCell sx={{ ...head, ...num, color: REPORT.forecastText }}>UNITS</TableCell>}
                    {tab === "sold" && <TableCell sx={{ ...head, ...num }}>ORDERS</TableCell>}
                    <TableCell sx={{ ...head, ...num }}>{prev.toUpperCase()}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tab === "sold"
                    ? sold.map((s, i) => (
                        <TableRow key={s.style} hover sx={{ bgcolor: i % 2 ? REPORT.rowAltBg : undefined }}>
                          <TableCell sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>
                            {s.style}
                            {s.new && (
                              <Chip label="NEW" size="small" sx={{ ml: 0.75, height: 16, fontSize: "0.6rem", fontWeight: 800, bgcolor: REPORT.good, color: "#fff" }} />
                            )}
                          </TableCell>
                          {showSub && <TableCell sx={{ color: "text.secondary" }}>{s.subCategory || "—"}</TableCell>}
                          {showTier && <TableCell sx={{ color: "text.secondary" }}>{s.tier || "—"}</TableCell>}
                          <TableCell sx={{ ...num, fontWeight: 700, color: REPORT.forecastText }}>{formatNumber(s.units)}</TableCell>
                          <TableCell sx={{ ...num, color: REPORT.actual }}>{formatNumber(s.orders)}</TableCell>
                          <TableCell sx={{ ...num, color: "text.secondary" }}>{s.prevUnits ? formatNumber(s.prevUnits) : "—"}</TableCell>
                        </TableRow>
                      ))
                    : stopped.map((s, i) => (
                        <TableRow key={s.style} hover sx={{ bgcolor: i % 2 ? REPORT.rowAltBg : undefined }}>
                          <TableCell sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>{s.style}</TableCell>
                          {showSub && <TableCell sx={{ color: "text.secondary" }}>{s.subCategory || "—"}</TableCell>}
                          {showTier && <TableCell sx={{ color: "text.secondary" }}>{s.tier || "—"}</TableCell>}
                          <TableCell sx={{ ...num, color: REPORT.bad }}>{formatNumber(s.prevUnits)}</TableCell>
                        </TableRow>
                      ))}
                  {(tab === "sold" ? sold : stopped).length === 0 && (
                    <TableRow>
                      <TableCell colSpan={6}>
                        <Box sx={{ py: 2, textAlign: "center", color: "text.secondary" }}>No styles</Box>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
