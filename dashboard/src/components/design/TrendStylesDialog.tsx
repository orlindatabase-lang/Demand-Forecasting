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
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import SearchIcon from "@mui/icons-material/Search";
import DownloadIcon from "@mui/icons-material/Download";
import { rowsToCsv, downloadCsv } from "@/components/common/ExportButton";
import type { DesignLifecycle, DesignLifecycleItem } from "@/types";
import { formatNumber } from "@/utils/format";
import { COLORS, REPORT, REPORT_HEAD_SX } from "@/theme/colors";

export type TrendFactor = "subCategory" | "colour" | "fabric" | "neck";
const LABEL: Record<TrendFactor, string> = { subCategory: "Sub Category", colour: "Colour", fabric: "Fabric", neck: "Neck" };
const ORDER: TrendFactor[] = ["subCategory", "colour", "fabric", "neck"];

const num = { textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" } as const;
const head = { ...REPORT_HEAD_SX, fontSize: "0.68rem", py: 1 } as const;

/** The styles behind one Trend status row (2026-10-29, user-requested): which
 * styles are in that Sub Category / Colour / Fabric / Neck, their other
 * attributes and how many pieces each sold in the latest 4 report weeks vs
 * the 4 before. */
export default function TrendStylesDialog({
  item,
  factor,
  stage,
  lifecycle,
  onClose,
}: {
  item: DesignLifecycleItem | null;
  factor: TrendFactor;
  stage?: { title: string; color: string };
  lifecycle: DesignLifecycle;
  onClose: () => void;
}) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const [search, setSearch] = useState("");
  const q = search.trim().toLowerCase();
  const info = lifecycle.styleInfo ?? {};
  const others = ORDER.filter((f) => f !== factor);
  const nb = lifecycle.blocks.length;
  const nowWeeks = lifecycle.blocks[nb - 1]?.weeks ?? "";
  const beforeWeeks = lifecycle.blocks[nb - 2]?.weeks ?? "";

  const rows = useMemo(
    () => (item?.styles ?? [])
      .map(([style, now, before]) => ({ style, now, before, attrs: info[style] }))
      .filter((r) => !q || r.style.toLowerCase().includes(q) || others.some((f) => (r.attrs?.[f] ?? "").toLowerCase().includes(q))),
    [item, info, q, others],
  );
  const all = item?.styles ?? [];
  const selling = all.filter((s) => s[1] > 0).length;
  const totalNow = all.reduce((a, s) => a + s[1], 0);
  const totalBefore = all.reduce((a, s) => a + s[2], 0);

  const handleClose = () => {
    setSearch("");
    onClose();
  };
  const handleExport = () => {
    if (!item) return;
    downloadCsv(
      rowsToCsv(rows.map((r) => ({
        Style: r.style,
        ...Object.fromEntries(others.map((f) => [LABEL[f], r.attrs?.[f] ?? ""])),
        [`Pieces ${nowWeeks} (so far)`]: r.now,
        [`Pieces ${beforeWeeks}`]: r.before,
      }))),
      `trend_styles_${factor}_${item.value.replace(/\W+/g, "_")}`,
    );
  };

  return (
    <Dialog open={item !== null} onClose={handleClose} fullScreen={phone} fullWidth maxWidth="md">
      <DialogTitle sx={{ pr: 6, pb: 1 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
          <Typography variant="subtitle1" component="div" sx={{ fontWeight: 800, color: REPORT.actual }}>
            {item?.value}
          </Typography>
          <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700 }}>{LABEL[factor]}</Typography>
          {stage && (
            <Chip size="small" label={stage.title} sx={{ height: 20, fontWeight: 700, color: "#fff", bgcolor: stage.color }} />
          )}
        </Stack>
        <Typography variant="caption" component="div" sx={{ color: "text.secondary" }}>
          Pieces sold in {nowWeeks} (so far) vs {beforeWeeks}
        </Typography>
        <IconButton onClick={handleClose} aria-label="Close" sx={{ position: "absolute", right: 8, top: 8 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ p: { xs: 1.5, sm: 2 } }}>
        <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1, mb: 1.5 }}>
          <Chip size="small" label={`${formatNumber(all.length)} styles`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: REPORT.actual }} />
          <Chip size="small" label={`${formatNumber(selling)} selling now`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: REPORT.good }} />
          <Chip size="small" label={`${formatNumber(totalNow)} pieces now`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: REPORT.forecastText }} />
          <Chip size="small" label={`${formatNumber(totalBefore)} pieces before`} sx={{ fontWeight: 700, bgcolor: COLORS.primaryBgSubtle, color: COLORS.textSecondary }} />
        </Stack>
        <Stack direction="row" sx={{ alignItems: "center", flexWrap: "wrap", gap: 1, mb: 1 }}>
          <TextField
            size="small"
            placeholder="Search style, colour, fabric…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> } }}
            sx={{ flexGrow: 1, minWidth: { xs: "100%", sm: 240 } }}
          />
          <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} onClick={handleExport}>
            Export CSV
          </Button>
        </Stack>
        <TableContainer sx={{ maxHeight: { xs: "none", sm: "60vh" }, border: 1, borderColor: "divider", borderRadius: 1 }}>
          <Table size="small" stickyHeader sx={{ "& td, & th": { borderColor: "divider" } }}>
            <TableHead>
              <TableRow>
                <TableCell sx={head}>STYLE</TableCell>
                {others.map((f) => <TableCell key={f} sx={head}>{LABEL[f].toUpperCase()}</TableCell>)}
                <TableCell sx={{ ...head, ...num, color: REPORT.forecastText }}>NOW</TableCell>
                <TableCell sx={{ ...head, ...num }}>BEFORE</TableCell>
                <TableCell sx={{ ...head, ...num }}>CHANGE</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r, i) => {
                const change = r.before > 0 ? ((r.now - r.before) / r.before) * 100 : null;
                return (
                  <TableRow key={r.style} hover sx={{ bgcolor: i % 2 ? REPORT.rowAltBg : undefined }}>
                    <TableCell sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>
                      {r.style}
                      {r.before === 0 && r.now > 0 && (
                        <Chip label="NEW" size="small" sx={{ ml: 0.75, height: 16, fontSize: "0.6rem", fontWeight: 800, bgcolor: REPORT.good, color: "#fff" }} />
                      )}
                    </TableCell>
                    {others.map((f) => (
                      <TableCell key={f} sx={{ color: "text.secondary" }}>{r.attrs?.[f] || "—"}</TableCell>
                    ))}
                    <TableCell sx={{ ...num, fontWeight: 700, color: r.now > 0 ? REPORT.forecastText : "text.disabled" }}>{formatNumber(r.now)}</TableCell>
                    <TableCell sx={{ ...num, color: "text.secondary" }}>{r.before ? formatNumber(r.before) : "—"}</TableCell>
                    <TableCell sx={{ ...num, fontWeight: 700, color: change === null ? "text.disabled" : change >= 0 ? REPORT.good : REPORT.bad }}>
                      {change === null ? "—" : `${change >= 0 ? "▲ +" : "▼ "}${change.toFixed(0)}%`}
                    </TableCell>
                  </TableRow>
                );
              })}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={others.length + 4}>
                    <Box sx={{ py: 2, textAlign: "center", color: "text.secondary" }}>
                      {all.length === 0 ? "No style sold in these 8 weeks" : "No styles"}
                    </Box>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </DialogContent>
    </Dialog>
  );
}
