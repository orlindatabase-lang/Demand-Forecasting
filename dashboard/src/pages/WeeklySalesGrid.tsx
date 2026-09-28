import { Fragment, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useQueries } from "@tanstack/react-query";
import {
  Box,
  Button,
  Chip,
  Dialog,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  MenuItem,
  Paper,
  Popover,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import CelebrationIcon from "@mui/icons-material/Celebration";
import SellIcon from "@mui/icons-material/Sell";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import DownloadIcon from "@mui/icons-material/Download";
import EventNoteIcon from "@mui/icons-material/EventNote";
import CloseIcon from "@mui/icons-material/Close";
import ImageNotSupportedIcon from "@mui/icons-material/ImageNotSupported";
import PageHeader from "@/components/common/PageHeader";
import RefreshButton from "@/components/common/RefreshButton";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { rowsToCsv, downloadCsv } from "@/components/common/ExportButton";
import ProductionLogDialog from "@/components/tables/ProductionLogDialog";
import {
  useWeeklyGrid,
  useChannelSourceWeeklyTotal,
  useCatalogStyleTiers,
  useStyleLifecycle,
} from "@/hooks/useDashboardData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { dataService } from "@/services/dataService";
import { formatNumber } from "@/utils/format";
import type {
  WeeklyGridCell,
  WeeklyGridRow,
  WeeklyGridMonth,
  WeeklyGridWeek,
  FestivalOutlook,
  UpcomingEventOutlook,
  ChannelSourceWeeklySeries,
  CatalogStyleImage,
} from "@/types";

const ACTUAL_COLOR = "#312c5c";
const FORECAST_COLOR = "#f97316";
// Real server-side pagination (2026-09-14, user-requested Prev/Next footer)
// - the table body is also row-virtualized (see rowVirtualizer), so a
// bigger PAGE_SIZE only costs a bit more network payload, not render time.
// Raised from 50 to 200 (2026-09-15, user-requested: show more styles per
// page) - still real pagination (~8 pages instead of ~30), just fewer clicks
// to browse the full list.
const PAGE_SIZE = 200;
// Backend caps `limit` at 2000 (api/main.py) — comfortably above the current
// ~1,500 Styles, so one request covers the entire filtered table for export
// (2026-09-18, user-requested: export the whole report, not just the
// current 200-row page).
const FULL_EXPORT_LIMIT = 2000;
const KEY_COL_WIDTH = 220;
// Pinned immediately after the sticky STYLE column (2026-09-14,
// user-requested: keep Launch Date clipped next to Style) so both stay
// visible together while the week/month columns scroll horizontally.
const LAUNCH_DATE_COL_WIDTH = 96;
// Initial guess for react-virtual's size estimate - it self-corrects per
// row via measureElement, so this only needs to be roughly right to avoid
// an initial scrollbar jump. Measured at 81px in the actual rendered table
// (2026-09-24) - the previous 62 was a stale guess from before this row's
// content grew (Style image + name + Sub Category + SKU count/tier chips),
// and a ~30% underestimate meant react-virtual was constantly recalculating
// row positions as it self-corrected during scroll, which showed up as a
// blank gap flashing in place of rows mid-scroll (user-reported).
const ROW_HEIGHT_ESTIMATE = 81;
// Table viewport sized to show this many data rows before needing to
// scroll (2026-09-15, user-requested: first 20, then narrowed to 8) - a
// fixed px height (row estimate x count, plus the two-row header) rather
// than a vh percentage, so the row count stays consistent across window
// sizes instead of shrinking on a shorter screen.
const VISIBLE_ROWS = 8;
const HEADER_HEIGHT = 75; // both header rows combined (see HEADER_ROW1_HEIGHT)
const TABLE_MAX_HEIGHT = HEADER_HEIGHT + VISIBLE_ROWS * ROW_HEIGHT_ESTIMATE;
// Solid (not the theme's "action.hover", which is translucent rgba) - this
// table's header row is `position: sticky` over a scrolling body, and a
// translucent background lets the scrolling rows underneath show/bleed
// through the pinned header text as the user scrolls.
const SHADE_COLOR = "#eef0f5";
// Measured height of the header's first row (the STYLE/LAUNCH DATE/month-
// name row) - MUI's `stickyHeader` gives every header cell `top: 0` by
// default, which is only correct for a SINGLE header row. With two stacked
// header rows that makes them overlap at the same sticky position instead
// of stacking, so the month-name row visually disappeared (only the week-
// label row stayed pinned) as soon as you scrolled past it (2026-09-15,
// user-requested: "clip" the month header row too). The second row below
// needs `top: HEADER_ROW1_HEIGHT` to sit right under the first instead.
const HEADER_ROW1_HEIGHT = 37;

function SummaryCard({
  label,
  value,
  color,
  currentWeekLabel,
  currentWeekValue,
}: {
  label: string;
  value: number;
  color: string;
  currentWeekLabel?: string;
  currentWeekValue?: number;
}) {
  return (
    <Paper variant="outlined" sx={{ flex: 1, minWidth: 220, p: 2 }}>
      <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600, letterSpacing: 0.3 }}>
        {label.toUpperCase()}
      </Typography>
      <Typography variant="h4" sx={{ fontWeight: 800, color, lineHeight: 1.3 }}>
        {formatNumber(value)}
      </Typography>
      {currentWeekLabel !== undefined && currentWeekValue !== undefined && (
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {currentWeekLabel}: <strong style={{ color }}>{formatNumber(currentWeekValue)}</strong>
        </Typography>
      )}
    </Paper>
  );
}

// `sticky` pins the cell to the bottom of the scrolling TableContainer
// (2026-09-15, user-requested: "clip" the TOTAL row like the header is
// clipped at top) - only ever passed true from the TOTAL row below, never
// from a normal data row.
// `bottom` is a prop, not hardcoded 0, so the TOTAL row's own sticky
// position can shift when its channel-breakdown dropdown is expanded below
// it (2026-09-18, user-requested: TOTAL first, breakdown stacked
// underneath, not above) - stacking sticky rows correctly means whichever
// one is meant to sit visually HIGHER needs the LARGER bottom offset.
const stickyBottomSx = (bottom: number) => ({ position: "sticky" as const, bottom, zIndex: 2 });
// Background for a Style's expanded per-marketplace sub-rows (2026-09-16,
// user-requested inline channel breakdown) - a faint shade distinguishes
// them from normal Style rows without competing with the existing
// even/odd-month header shading.
const MARKET_ROW_BG = "#f8fafc";

function Cell({
  cell,
  stickyBottom,
  showAccuracy,
  eventCategory,
}: {
  cell: WeeklyGridCell | undefined;
  stickyBottom?: number;
  showAccuracy?: boolean;
  eventCategory?: string;
}) {
  // stickyBottom (the TOTAL row) keeps its own solid paper background so it
  // stays legible pinned over whatever scrolls beneath it - the event tint
  // only applies to ordinary body-row cells.
  const tint = stickyBottom === undefined ? eventTint(eventCategory ?? "") : undefined;
  if (!cell) {
    return (
      <TableCell
        align="right"
        sx={
          stickyBottom !== undefined
            ? { ...stickyBottomSx(stickyBottom), bgcolor: "background.paper" }
            : tint
              ? { bgcolor: tint }
              : undefined
        }
      >
        <Typography variant="body2" sx={{ color: "text.disabled" }}>
          —
        </Typography>
      </TableCell>
    );
  }
  const accuracy = showAccuracy && cell.actual !== null ? monthAccuracy(cell.actual, cell.forecast) : null;
  return (
    <TableCell
      align="right"
      sx={{
        opacity: cell.partial ? 0.6 : 1,
        ...(stickyBottom !== undefined
          ? { ...stickyBottomSx(stickyBottom), bgcolor: "background.paper" }
          : tint
            ? { bgcolor: tint }
            : {}),
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.3 }}>
        {cell.actual === null ? <span style={{ color: "#94a3b8" }}>—</span> : formatNumber(cell.actual)}
      </Typography>
      <Typography variant="caption" sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block" }}>
        {formatNumber(cell.forecast)}
      </Typography>
      {accuracy !== null && (
        <Typography
          variant="caption"
          sx={{ color: accuracyColor(accuracy), lineHeight: 1.3, display: "block", fontWeight: 700, fontSize: "0.65rem" }}
        >
          {accuracy.toFixed(1)}% acc
        </Typography>
      )}
    </TableCell>
  );
}

// One marketplace's actual + forecast Gross Sale for one week (2026-09-16,
// user-requested inline channel breakdown; forecast added 2026-09-18,
// user-requested - see ChannelSourceWeeklySeries.forecastCells: a top-down
// proportional split of the row's own pooled forecast by this channel's
// historical actual-sales share, not an independently-modeled number).
function MarketplaceCell({ qty, forecast }: { qty: number | undefined; forecast: number | undefined }) {
  return (
    <TableCell align="right" sx={{ bgcolor: MARKET_ROW_BG }}>
      <Typography variant="body2" sx={{ color: qty ? "text.primary" : "text.disabled" }}>
        {qty ? formatNumber(qty) : "—"}
      </Typography>
      <Typography variant="caption" sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block" }}>
        {forecast ? formatNumber(forecast) : "—"}
      </Typography>
    </TableCell>
  );
}

// Style-lifecycle chip list (2026-09-23, user-requested) - a Sub Category
// can have 100+ styles in either bucket, which was previously rendered as
// one giant unbroken wall of chips. Shows only the first CHIP_LIMIT by
// default with a "See more…" chip to reveal the rest; owns its own expand
// state, so mounting a fresh instance per Sub Category (via the parent's
// `key={subCategoryFilter}`) naturally resets it back to collapsed.
const STYLE_CHIP_LIMIT = 30;

function StyleChipList({ styles, color }: { styles: string[]; color?: "primary" }) {
  const [expanded, setExpanded] = useState(false);
  if (styles.length === 0) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary", mt: 0.5 }}>
        None
      </Typography>
    );
  }
  const visible = expanded ? styles : styles.slice(0, STYLE_CHIP_LIMIT);
  const remaining = styles.length - STYLE_CHIP_LIMIT;
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mt: 0.5 }}>
      {visible.map((s) => (
        <Chip key={s} label={s} size="small" variant="outlined" color={color} />
      ))}
      {remaining > 0 && (
        <Chip
          label={expanded ? "See less" : `See more… (${remaining})`}
          size="small"
          onClick={() => setExpanded((v) => !v)}
          sx={{ cursor: "pointer", fontWeight: 700 }}
        />
      )}
    </Box>
  );
}

// Month has real actual sales to compare against (past/current months only -
// a future month's actual is still 0, which would otherwise divide-by-zero
// into a meaningless 0% instead of just omitting the figure).
function monthAccuracy(actual: number, forecast: number): number | null {
  if (actual <= 0) return null;
  // Decimal, not rounded to a whole number (2026-09-24, user-requested) -
  // callers format the display precision themselves (see the "% acc" render
  // sites' .toFixed(1)).
  return Math.max(0, Math.min(100, (1 - Math.abs(actual - forecast) / actual) * 100));
}

function accuracyColor(pct: number): string {
  if (pct >= 80) return "#16a34a";
  if (pct >= 50) return FORECAST_COLOR;
  return "#dc2626";
}

function TotalCell({
  actual,
  forecast,
  stickyBottom,
  showAccuracy,
}: {
  actual: number;
  forecast: number;
  stickyBottom?: number;
  showAccuracy?: boolean;
}) {
  const accuracy = showAccuracy ? monthAccuracy(actual, forecast) : null;
  return (
    <TableCell align="right" sx={{ bgcolor: SHADE_COLOR, ...(stickyBottom !== undefined ? stickyBottomSx(stickyBottom) : {}) }}>
      <Typography variant="body2" sx={{ fontWeight: 700, lineHeight: 1.3, color: ACTUAL_COLOR }}>
        {formatNumber(actual)}
      </Typography>
      <Typography variant="caption" sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block", fontWeight: 600 }}>
        {formatNumber(forecast)}
      </Typography>
      {accuracy !== null && (
        <Typography
          variant="caption"
          sx={{ color: accuracyColor(accuracy), lineHeight: 1.3, display: "block", fontWeight: 700, fontSize: "0.65rem" }}
        >
          {accuracy.toFixed(1)}% acc
        </Typography>
      )}
    </TableCell>
  );
}

function formatDateShort(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

// Includes the year, unlike formatDateShort - needed for the W1..W4 event
// popover since the festival/sale calendar spans multiple years and the
// short form alone would be ambiguous (2026-09-24, user-requested).
function formatDateWithYear(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

// "High Sale" color-coding for the festival/sale outlook's ranked lists
// (2026-09-14, user-requested) - a sub-category/color that sold MUCH higher
// than its own normal (pre-event) level stands out from one that just
// happens to be a naturally big seller with little real festival lift.
const HIGH_UPLIFT_COLOR = "#dc2626";
function upliftColor(pct: number | null): string {
  if (pct !== null && pct >= 100) return HIGH_UPLIFT_COLOR; // 2x+ vs normal
  if (pct !== null && pct >= 30) return FORECAST_COLOR; // notably elevated
  return ACTUAL_COLOR; // normal / no real lift / no baseline to compare
}

const FESTIVAL_ACCENT = "#f97316";
const SALE_ACCENT = "#a855f7";
// A week with BOTH a Festival and a Sale overlapping (2026-09-24, user-
// requested) gets its own third accent, distinct from either alone - e.g.
// this week's Ganesh Chaturthi overlapping the Meesho/Myntra/Flipkart sale
// window - rather than silently showing as just one or the other.
const BOTH_EVENTS_ACCENT = "#b45309";
// Light background tints of the accents above, for highlighting a whole
// week's column - past or upcoming - when it falls inside a real festival
// or sale-event window (2026-09-24, user-requested; see
// api/festival_calendar.py, the same categorized source that already
// powers the "Upcoming Festival"/"Upcoming Sale" cards above this table).
function eventTint(category: string): string | undefined {
  if (category === "festival") return "#fff1e6";
  if (category === "sale") return "#f6ecfe";
  if (category === "both") return "#fef3c7";
  return undefined;
}

// Best-effort swatch for a garment color name (2026-09-14, user-requested
// visual redesign) - covers the color names actually seen in the SKU master
// sheet's Top_Color column. Falls back to a stable hash-derived hue so an
// unmapped name still gets a distinct, legible bar instead of one flat gray.
const COLOR_SWATCHES: Record<string, string> = {
  coffee: "#6f4e37",
  grey: "#9ca3af",
  gray: "#9ca3af",
  "rose pink": "#f472b6",
  pink: "#f9a8d4",
  orange: "#f97316",
  mustard: "#d4a017",
  "teal blue": "#0d9488",
  teal: "#14b8a6",
  violet: "#8b5cf6",
  red: "#ef4444",
  lavender: "#c4b5fd",
  "royal blue": "#2563eb",
  blue: "#3b82f6",
  neon: "#a3e635",
  black: "#111827",
  white: "#d1d5db",
  beige: "#e8dcc8",
  olive: "#808000",
  peach: "#ffcba4",
  magenta: "#d946ef",
  turquoise: "#2dd4bf",
  wine: "#722f37",
  rust: "#b7410e",
  coral: "#fb7185",
  maroon: "#7f1d1d",
  green: "#22c55e",
  "bottle green": "#0b6623",
  mint: "#86efac",
  yellow: "#eab308",
  gold: "#d4af37",
  silver: "#c0c0c0",
  brown: "#92400e",
  navy: "#1e3a8a",
  purple: "#9333ea",
  cream: "#fdf6e3",
  mauve: "#b784a7",
  copper: "#b87333",
  "sea green": "#2e8b57",
  multi: "#94a3b8",
};
function colorSwatch(name: string): string {
  const key = name.trim().toLowerCase();
  if (COLOR_SWATCHES[key]) return COLOR_SWATCHES[key];
  const lastWord = key.split(" ").pop() ?? key;
  if (COLOR_SWATCHES[lastWord]) return COLOR_SWATCHES[lastWord];
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return `hsl(${hash % 360}, 55%, 55%)`;
}

function daysBetween(startIso: string, endIso: string): number {
  const start = Date.parse(`${startIso}T00:00:00Z`);
  const end = Date.parse(`${endIso}T00:00:00Z`);
  return Math.round((end - start) / 86400000) + 1;
}

// Ranked mini-table (rank / label / units + proportional bar) shared by the
// "Top Selling Sub Categories" and "Top Selling Design Colors" panels below -
// same data shape, just a different bar color rule per list.
function MiniRankTable({
  title,
  columnLabel,
  items,
  barColor,
}: {
  title: string;
  columnLabel: string;
  items: { key: string; label: string; qty: number; upliftPct: number | null }[];
  barColor: (label: string) => string;
}) {
  const maxQty = Math.max(1, ...items.map((i) => i.qty));
  return (
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Typography variant="body2" sx={{ fontWeight: 700, mb: 0.75 }}>
        {title}
      </Typography>
      <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden" }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "24px 1fr 90px",
            gap: 1,
            px: 1.25,
            py: 0.6,
            bgcolor: SHADE_COLOR,
            fontSize: "0.68rem",
            fontWeight: 700,
            color: "text.secondary",
            textTransform: "uppercase",
            letterSpacing: 0.3,
          }}
        >
          <span>#</span>
          <span>{columnLabel}</span>
          <span style={{ textAlign: "right" }}>Units</span>
        </Box>
        {items.map((item, i) => (
          <Box
            key={item.key}
            sx={{
              display: "grid",
              gridTemplateColumns: "24px 1fr 90px",
              alignItems: "center",
              gap: 1,
              px: 1.25,
              py: 0.85,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              {i + 1}
            </Typography>
            <Typography variant="body2" noWrap title={item.label}>
              {item.label}
            </Typography>
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 700, textAlign: "right", color: upliftColor(item.upliftPct) }}>
                {formatNumber(item.qty)}
              </Typography>
              <Box sx={{ height: 5, borderRadius: 3, bgcolor: "action.hover", mt: 0.4, overflow: "hidden" }}>
                <Box
                  sx={{
                    height: "100%",
                    width: `${Math.max(6, (item.qty / maxQty) * 100)}%`,
                    bgcolor: barColor(item.label),
                    borderRadius: 3,
                    ml: "auto",
                  }}
                />
              </Box>
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

function EventPanel({
  kind,
  label,
  event,
}: {
  kind: "festival" | "sale";
  label: string;
  event: UpcomingEventOutlook | null;
}) {
  const accent = kind === "festival" ? FESTIVAL_ACCENT : SALE_ACCENT;
  const Icon = kind === "festival" ? CelebrationIcon : SellIcon;

  if (!event) {
    return (
      <Paper variant="outlined" sx={{ flex: 1, minWidth: 300, p: 2 }}>
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, letterSpacing: 0.3 }}>
          {label}
        </Typography>
        <Typography variant="body2" sx={{ color: "text.disabled", mt: 0.5 }}>
          None found in the next 12 months.
        </Typography>
      </Paper>
    );
  }

  const days = daysBetween(event.eventStart, event.eventEnd);

  return (
    <Paper variant="outlined" sx={{ flex: 1, minWidth: 320, overflow: "hidden" }}>
      <Box
        sx={{
          px: 2.5,
          py: 2,
          borderLeft: `4px solid ${accent}`,
          background:
            kind === "festival"
              ? "linear-gradient(135deg, #fff4e8 0%, #ffe6cc 100%)"
              : "linear-gradient(135deg, #faf0fd 0%, #f6e0fa 100%)",
        }}
      >
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
          <Box
            sx={{
              width: 22,
              height: 22,
              borderRadius: "6px",
              bgcolor: accent,
              color: "#fff",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <Icon sx={{ fontSize: 14 }} />
          </Box>
          <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: 0.4, color: "text.secondary" }}>
            {label.toUpperCase()}
          </Typography>
        </Stack>
        <Typography variant="h6" sx={{ fontWeight: 800, color: accent, lineHeight: 1.25 }}>
          {event.eventName}
        </Typography>
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          {formatDateShort(event.eventStart)} – {formatDateShort(event.eventEnd)} ({days} day{days === 1 ? "" : "s"})
        </Typography>

        {event.spikeLeadDays !== null && (
          <Typography variant="caption" sx={{ color: ACTUAL_COLOR, fontWeight: 600, display: "block", mt: 0.5 }}>
            🔥 Demand historically starts rising ~{event.spikeLeadDays} day{event.spikeLeadDays === 1 ? "" : "s"}{" "}
            before this event
          </Typography>
        )}
      </Box>

      <Box sx={{ p: 2 }}>
        {event.topSubCategories.length === 0 ? (
          <Typography variant="body2" sx={{ color: "text.disabled" }}>
            No historical sales data available for this event yet.
          </Typography>
        ) : (
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <MiniRankTable
              title={`Top Selling Sub Categories in ${event.historicalYear} (${formatDateShort(
                event.historicalStart,
              )} – ${formatDateShort(event.historicalEnd)})`}
              columnLabel="Sub Category"
              items={event.topSubCategories.map((s) => ({
                key: s.subCategory,
                label: s.subCategory,
                qty: s.qty,
                upliftPct: s.upliftPct,
              }))}
              barColor={() => accent}
            />
            {event.topColors.length > 0 && (
              <MiniRankTable
                title={`Top Selling Design Colors in ${event.historicalYear} (${formatDateShort(
                  event.historicalStart,
                )} – ${formatDateShort(event.historicalEnd)})`}
                columnLabel="Color"
                items={event.topColors.map((c) => ({
                  key: c.color,
                  label: c.color,
                  qty: c.qty,
                  upliftPct: c.upliftPct,
                }))}
                barColor={colorSwatch}
              />
            )}
          </Stack>
        )}
      </Box>
    </Paper>
  );
}

function FestivalCard({ outlook }: { outlook: FestivalOutlook | null }) {
  if (!outlook) return null;
  return (
    <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mb: 2 }}>
      <EventPanel kind="festival" label="Upcoming Festival" event={outlook.upcomingFestival} />
      <EventPanel kind="sale" label="Upcoming Sale" event={outlook.upcomingSale} />
    </Stack>
  );
}

// One flattened virtualizer item: either a normal Style row, one of its
// expanded per-marketplace week-wise sub-rows, or a status line (loading/
// empty/error) while that Style's breakdown is fetched (2026-09-16,
// user-requested: replace the month-level dialog with an inline
// expand/collapse dropdown, matching the app's existing size-level expand
// pattern, shown week-wise instead of month-wise).
type FlatItem =
  | { kind: "style"; row: WeeklyGridRow }
  | { kind: "market"; style: string; series: ChannelSourceWeeklySeries }
  | { kind: "status"; style: string; text: string };

// One CSV row per Style, exactly mirroring what's on screen: every week
// (Actual + Forecast) and every month TOTAL across the full display axis
// (April 2025 → the horizon's end), plus the summary columns shown in the
// STYLE and SUGGESTED PRODUCTION QTY cells (2026-09-18, user-requested full
// table export).
function flattenGridRow(row: WeeklyGridRow, months: WeeklyGridMonth[]): Record<string, unknown> {
  const flat: Record<string, unknown> = {
    Style: row.key,
    "Sub Category": row.subCategory || "Unclassified",
    "SKU Count": row.skuCount,
    "Launch Date": row.launchDate || "",
    "Days Since Launch": row.daysSinceLaunch >= 0 ? row.daysSinceLaunch : "",
  };
  for (const m of months) {
    for (const w of m.weeks) {
      const cell = row.cells[w.weekStart];
      flat[`${m.label} ${w.label} Actual`] = cell?.actual ?? "";
      flat[`${m.label} ${w.label} Forecast`] = cell?.forecast ?? 0;
    }
    flat[`${m.label} TOTAL Actual`] = row.monthActualTotal[m.label] ?? 0;
    flat[`${m.label} TOTAL Forecast`] = row.monthForecastTotal[m.label] ?? 0;
  }
  flat["Grand Actual Total"] = row.grandActualTotal;
  flat["Grand Forecast Total"] = row.grandForecastTotal;
  flat["Future Forecast Total"] = row.futureForecastTotal;
  flat["Available Qty"] = row.availableQty;
  flat["Suggested Production Qty"] = row.suggestedProduction;
  return flat;
}

export default function WeeklySalesGrid() {
  const [searchText, setSearchText] = useState("");
  const [subCategoryFilter, setSubCategoryFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [tierFilter, setTierFilter] = useState("all");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebouncedValue(searchText, 300);

  // Catalog image + externally-maintained forecast tier (Cloud SQL
  // CatalogStyle.forecastStatus), applied directly onto this page's own
  // Style rows rather than as a separate page (2026-09-21, user-requested:
  // "do not create a separate page for it, apply it to the Demand
  // forecast"). tierStyleSet is null when "All Tiers" is selected (no
  // client-side filtering needed).
  const catalogQuery = useCatalogStyleTiers();

  // Styles present (sold) last calendar year vs newly launched this one, for
  // the currently selected Sub Category (2026-09-22, user-requested) - only
  // fetches once a specific one is picked, mirroring how the rest of this
  // page already reacts to subCategoryFilter.
  const lifecycleQuery = useStyleLifecycle(subCategoryFilter === "all" ? "" : subCategoryFilter);
  const lifecycleEntry =
    subCategoryFilter !== "all" ? lifecycleQuery.data?.bySubCategory[subCategoryFilter] : undefined;

  const styleTierMap = useMemo(() => {
    const map = new Map<string, CatalogStyleImage>();
    for (const g of catalogQuery.data?.tiers ?? []) {
      for (const s of g.styles) map.set(s.name, s);
    }
    return map;
  }, [catalogQuery.data]);
  // T12 excluded from the style-lifecycle panel (2026-09-24, user-requested)
  // - those styles aren't meant to be tracked here, so both the chip lists
  // and their counts drop any style catalogued as T12.
  const nonT12 = (styles: string[]) => styles.filter((s) => styleTierMap.get(s)?.tier !== "T12");
  const presentStylesFiltered = useMemo(
    () => nonT12(lifecycleEntry?.presentStyles ?? []),
    [lifecycleEntry, styleTierMap],
  );
  const addedStylesFiltered = useMemo(
    () => nonT12(lifecycleEntry?.addedStyles ?? []),
    [lifecycleEntry, styleTierMap],
  );
  const tierStyleSet = useMemo(() => {
    if (tierFilter === "all") return null;
    const group = catalogQuery.data?.tiers.find((g) => g.tier === tierFilter);
    return new Set(group?.styles.map((s) => s.name) ?? []);
  }, [catalogQuery.data, tierFilter]);
  const [lightboxStyle, setLightboxStyle] = useState<CatalogStyleImage | null>(null);
  const [productionLogOpen, setProductionLogOpen] = useState(false);
  // Clicking a W1..W4 header on a highlighted week shows each overlapping
  // event's own real start/end date (2026-09-24, user-requested) - distinct
  // from the calendar chunk's dates, since a chunk can be a partial slice
  // of a longer event or straddle more than one.
  const [eventPopover, setEventPopover] = useState<{ anchorEl: HTMLElement; week: WeeklyGridWeek } | null>(null);
  const [expandedStyles, setExpandedStyles] = useState<Set<string>>(new Set());
  const toggleExpand = (style: string) =>
    setExpandedStyles((prev) => {
      const next = new Set(prev);
      if (next.has(style)) next.delete(style);
      else next.add(style);
      return next;
    });
  const [exporting, setExporting] = useState(false);
  // TOTAL row's own channel breakdown (2026-09-18, user-requested) - same
  // expand/collapse pattern as a Style row, just summed across every
  // currently-matching Style instead of one; only fetched while expanded.
  const [totalExpanded, setTotalExpanded] = useState(false);
  const totalChannelQuery = useChannelSourceWeeklyTotal(
    totalExpanded, 9, debouncedSearch,
    subCategoryFilter === "all" ? "" : subCategoryFilter,
    categoryFilter === "all" ? "" : categoryFilter,
  );
  // The TOTAL row is `position: sticky; bottom: ...` (stays pinned while
  // scrolling past all ~1,500 Style rows) - its channel breakdown rows need
  // to be sticky too, stacked directly below it (2026-09-18, user-requested:
  // TOTAL first/topmost, breakdown underneath it, both staying pinned
  // together), or they'd sit in normal document flow at the very end of the
  // row list and be invisible without scrolling all the way down (confirmed
  // broken without this fix). Stacking sticky elements at the bottom means
  // whichever one should appear visually HIGHER needs the LARGER `bottom`
  // offset - so TOTAL's own bottom = the combined height of every
  // breakdown row (pushing it up above them), and each breakdown row's
  // bottom = the combined height of the breakdown rows AFTER it (the last
  // one sits at bottom: 0, the true bottom edge). Row heights aren't fixed
  // constants (font rendering, DPI), so they're measured after render and
  // every offset computed from the ACTUAL heights, same two-pass
  // measure-then-position technique react-virtual uses internally - not
  // guessed pixel values.
  const marketRowRefs = useRef<(HTMLTableRowElement | null)[]>([]);
  const [marketStickyBottoms, setMarketStickyBottoms] = useState<number[]>([]);
  const [totalStickyBottom, setTotalStickyBottom] = useState(0);
  useLayoutEffect(() => {
    if (!totalExpanded || !totalChannelQuery.data) {
      setMarketStickyBottoms([]);
      setTotalStickyBottom(0);
      return;
    }
    const heights = marketRowRefs.current.map((el) => el?.getBoundingClientRect().height ?? 0);
    const offsets = new Array(heights.length).fill(0);
    let acc = 0;
    for (let i = heights.length - 1; i >= 0; i--) {
      offsets[i] = acc;
      acc += heights[i];
    }
    setMarketStickyBottoms(offsets);
    setTotalStickyBottom(acc);
  }, [totalExpanded, totalChannelQuery.data]);

  // A tier filter narrows across the WHOLE catalog, not just the current
  // page - fetch every matching Style at once (same FULL_EXPORT_LIMIT the
  // CSV export already uses) and filter client-side, rather than trying to
  // paginate a set the backend doesn't know how to slice by tier.
  const tierFilterActive = tierStyleSet !== null;
  const { data, isLoading, isError, refetch, isFetching } = useWeeklyGrid("style", {
    limit: tierFilterActive ? FULL_EXPORT_LIMIT : PAGE_SIZE,
    offset: tierFilterActive ? 0 : offset,
    search: debouncedSearch,
    subCategory: subCategoryFilter === "all" ? "" : subCategoryFilter,
    category: categoryFilter === "all" ? "" : categoryFilter,
  });

  const visibleRows = useMemo(() => {
    if (!data) return [];
    return tierStyleSet !== null ? data.rows.filter((r) => tierStyleSet.has(r.key)) : data.rows;
  }, [data, tierStyleSet]);

  // Sub Category dropdown narrows to only the ones under the selected
  // Category (2026-09-23, user-requested cascading filter) - "All
  // Categories" still lists every Sub Category across the whole plan.
  const availableSubCategoryOptions = useMemo(() => {
    if (!data) return [];
    if (categoryFilter === "all") return data.subCategoryOptions;
    return data.categorySubCategoryMap[categoryFilter] ?? [];
  }, [data, categoryFilter]);

  // The header totals and the TOTAL row itself need to reflect the active
  // tier filter too (2026-09-21, user-requested) - the backend has no
  // concept of tier (it lives in the separate Cloud SQL catalog table,
  // joined in client-side only), so with a tier active these are
  // recomputed here by summing the tier-filtered rows' own already-correct
  // per-row figures, the same way the backend itself sums "every matching
  // group" into these totals server-side. With no tier filter, pass the
  // server's own totals straight through unchanged.
  const displayTotals = useMemo(() => {
    if (!data) return null;
    if (tierStyleSet === null) {
      return {
        totalForecast: data.totalForecast,
        totalActual: data.totalActual,
        totalSuggestedProduction: data.totalSuggestedProduction,
        currentWeekForecast: data.currentWeekForecast,
        currentWeekActual: data.currentWeekActual,
        weeklyTotals: data.weeklyTotals,
      };
    }
    const totalForecast = visibleRows.reduce((s, r) => s + r.futureForecastTotal, 0);
    const totalActual = visibleRows.reduce((s, r) => s + r.grandActualTotal, 0);
    const totalSuggestedProduction = visibleRows.reduce((s, r) => s + r.suggestedProduction, 0);

    const cells: Record<string, WeeklyGridCell> = {};
    const monthActualTotal: Record<string, number> = {};
    const monthForecastTotal: Record<string, number> = {};
    // Exactly one week in the whole grid is ever "partial" (still in
    // progress) - the one containing today - so it doubles as a reliable
    // way to identify "the current week" without re-deriving calendar-chunk
    // date math client-side.
    let currentWeekForecast = 0;
    let currentWeekActual = 0;
    for (const m of data.months) {
      let monthActual = 0;
      let monthForecast = 0;
      for (const w of m.weeks) {
        let actual = 0;
        let forecast = 0;
        let partial = false;
        let hasActual = false;
        for (const r of visibleRows) {
          const c = r.cells[w.weekStart];
          if (!c) continue;
          forecast += c.forecast;
          if (c.actual !== null) {
            actual += c.actual;
            hasActual = true;
          }
          if (c.partial) partial = true;
        }
        cells[w.weekStart] = { actual: hasActual ? actual : null, forecast, partial };
        monthForecast += forecast;
        if (hasActual) monthActual += actual;
        if (partial) {
          currentWeekForecast = forecast;
          currentWeekActual = hasActual ? actual : 0;
        }
      }
      monthActualTotal[m.label] = monthActual;
      monthForecastTotal[m.label] = monthForecast;
    }

    return {
      totalForecast,
      totalActual,
      totalSuggestedProduction,
      currentWeekForecast,
      currentWeekActual,
      weeklyTotals: { cells, monthActualTotal, monthForecastTotal },
    };
  }, [data, tierStyleSet, visibleRows]);

  // Fetch every currently-expanded Style's per-marketplace weekly breakdown
  // in parallel (useQueries handles the variable-length query list cleanly,
  // unlike calling a hook in a loop).
  const expandedList = useMemo(() => Array.from(expandedStyles), [expandedStyles]);
  const weeklyResults = useQueries({
    queries: expandedList.map((style) => ({
      queryKey: ["channelSourceWeekly", style, 9],
      queryFn: () => dataService.channelSourceWeekly(style, 9),
    })),
  });

  // Flatten Style rows + any expanded Style's marketplace sub-rows into one
  // list the virtualizer treats as its item source - each sub-row is its own
  // real virtual item (not nested DOM), so react-virtual's dynamic
  // measurement/repositioning works exactly as it does for normal rows.
  // Accuracy is only meaningful once a period is FULLY over - the currently
  // running week/month is still partial (its "actual" is a running total
  // that will keep climbing), so comparing it to the forecast produces a
  // misleadingly low "0% acc" rather than a real accuracy figure (2026-09-19,
  // user-requested: only COMPLETED months/weeks, not the running one - a
  // completed week is one with actual data and cell.partial=false; a
  // completed month is one whose every week is like that). Originally only
  // flagged the single MOST RECENT completed week/month; extended
  // (2026-09-24, user-requested: "show the accuracy percentage for each
  // completed week and month") to flag every completed one, not just the
  // latest.
  const { completedWeekStarts, completedMonthLabels } = useMemo(() => {
    if (!data || !displayTotals) {
      return { completedWeekStarts: new Set<string>(), completedMonthLabels: new Set<string>() };
    }
    const weekStarts = new Set<string>();
    const monthLabels = new Set<string>();
    for (const m of data.months) {
      let monthComplete = m.weeks.length > 0;
      for (const w of m.weeks) {
        const cell = displayTotals.weeklyTotals.cells[w.weekStart];
        const weekComplete = cell !== undefined && cell.actual !== null && !cell.partial;
        if (weekComplete) weekStarts.add(w.weekStart);
        else monthComplete = false;
      }
      if (monthComplete) monthLabels.add(m.label);
    }
    return { completedWeekStarts: weekStarts, completedMonthLabels: monthLabels };
  }, [data, displayTotals]);

  const flatRows: FlatItem[] = useMemo(() => {
    const out: FlatItem[] = [];
    for (const row of visibleRows) {
      out.push({ kind: "style", row });
      if (expandedStyles.has(row.key)) {
        const i = expandedList.indexOf(row.key);
        const result = weeklyResults[i];
        if (!result || result.isLoading) {
          out.push({ kind: "status", style: row.key, text: "Loading channel breakdown…" });
        } else if (result.isError) {
          out.push({ kind: "status", style: row.key, text: "Failed to load channel breakdown." });
        } else if (!result.data || result.data.series.length === 0) {
          out.push({ kind: "status", style: row.key, text: "No channel sales in this window." });
        } else {
          for (const series of result.data.series) {
            out.push({ kind: "market", style: row.key, series });
          }
        }
      }
    }
    return out;
  }, [visibleRows, expandedStyles, expandedList, weeklyResults]);

  // Only the rows scrolled into view are ever mounted - with up to ~1,475
  // Styles fetched at once (PAGE_SIZE), rendering every <TableRow> at once
  // froze the tab. Declared before the loading/error early returns below so
  // the hook still runs on every render (rules of hooks); count falls back
  // to 0 until `data` arrives.
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT_ESTIMATE,
    // NOT raised beyond 8 (2026-09-24, user-reported blank rows while
    // scrolling) - tried 20 to give fast scrolling more pre-rendered buffer,
    // but that instead reliably CRASHED the tab after ~25 wheel events
    // (confirmed via a Playwright repro: each row mounts an image, tooltips,
    // and a measureElement ResizeObserver, so nearly doubling how many are
    // simultaneously alive during a fast scroll is what broke it). The real
    // fix was ROW_HEIGHT_ESTIMATE above being wrong; leave this at 8.
    overscan: 8,
  });

  // Which month contains today, by ISO week-start date rather than string-
  // matching the "%B %Y" label - immune to any locale mismatch between the
  // backend's Python strftime and the browser's date formatting.
  const currentMonthLabel = useMemo(() => {
    if (!data) return null;
    const todayIso = new Date().toISOString().slice(0, 10);
    const currentMonth = data.months.find((m) =>
      m.weeks.some((w) => {
        const weekEnd = new Date(w.weekStart);
        weekEnd.setDate(weekEnd.getDate() + 6);
        return w.weekStart <= todayIso && weekEnd.toISOString().slice(0, 10) >= todayIso;
      }),
    );
    return currentMonth?.label ?? null;
  }, [data]);

  // Auto-scroll to the current month once on first load (2026-09-22, user-
  // requested: "when we open the dashboard it has to jump directly to the
  // current month" - a true PINNED middle column isn't possible with CSS
  // sticky positioning, verified earlier this session with a minimal plain-
  // HTML repro, so this scrolls there once on open instead). Guarded so it
  // only ever fires once per page visit, not on every subsequent
  // filter/search-driven refetch.
  const currentMonthCellRef = useRef<HTMLTableCellElement | null>(null);
  const hasAutoScrolledRef = useRef(false);
  useLayoutEffect(() => {
    if (hasAutoScrolledRef.current || !currentMonthLabel) return;
    const cell = currentMonthCellRef.current;
    if (cell && scrollRef.current) {
      scrollRef.current.scrollLeft = Math.max(0, cell.offsetLeft - (KEY_COL_WIDTH + LAUNCH_DATE_COL_WIDTH));
      hasAutoScrolledRef.current = true;
    }
  }, [currentMonthLabel]);

  // Exports the ENTIRE filtered table (every Style matching the current
  // search/Sub Category filter, not just the current 200-row page) with
  // every week/month column shown on screen — a separate on-demand fetch
  // since only the current page's rows live in `data` client-side.
  const handleExportAll = async () => {
    setExporting(true);
    try {
      const full = await dataService.weeklyGrid("style", {
        limit: FULL_EXPORT_LIMIT,
        offset: 0,
        search: debouncedSearch,
        subCategory: subCategoryFilter === "all" ? "" : subCategoryFilter,
        category: categoryFilter === "all" ? "" : categoryFilter,
      });
      const rows = tierStyleSet !== null ? full.rows.filter((row) => tierStyleSet.has(row.key)) : full.rows;
      const flatRows = rows.map((row) => flattenGridRow(row, full.months));
      downloadCsv(rowsToCsv(flatRows), "weekly_sales_report");
    } finally {
      setExporting(false);
    }
  };

  if (isLoading) return <LoadingSkeleton variant="page" />;
  if (isError || !data || !displayTotals) return <ErrorState onRetry={() => refetch()} />;

  const page = data.total === 0 ? 0 : Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  // Total column count (STYLE + LAUNCH DATE + every week/month-total column
  // + SUGGESTED PRODUCTION QTY) - used both by the "No rows" placeholder and
  // the two virtualizer spacer rows below.
  const totalColSpan = 3 + data.months.reduce((a, m) => a + m.weeks.length + 1, 0);
  const virtualRows = rowVirtualizer.getVirtualItems();
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom =
    virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0;

  return (
    <Box>
      <PageHeader
        title="Demand Forecasting"
        subtitle={
          <>
            Forecasted vs Actual sales, week by week, next 2 months — top number is Actual, bottom (orange) is
            Forecast. The last column suggests how much to produce (forecast demand minus what's already
            available).
            {isFetching && " Refreshing…"}
          </>
        }
        actions={<RefreshButton />}
      />

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mb: 2 }}>
        <SummaryCard
          label="Total Forecast (Next 2 Months)"
          value={displayTotals.totalForecast}
          color={FORECAST_COLOR}
          currentWeekLabel={data.currentWeekLabel}
          currentWeekValue={displayTotals.currentWeekForecast}
        />
        <SummaryCard
          label="Total Actual Sale (Since Apr 2025)"
          value={displayTotals.totalActual}
          color={ACTUAL_COLOR}
          currentWeekLabel={data.currentWeekLabel}
          currentWeekValue={displayTotals.currentWeekActual}
        />
        <SummaryCard
          label="Total Suggested Production Qty"
          value={displayTotals.totalSuggestedProduction}
          color={FORECAST_COLOR}
        />
      </Stack>

      <FestivalCard outlook={data.festivalOutlook} />

      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 2 }}>
        <TextField
          size="small"
          placeholder="Search style…"
          value={searchText}
          onChange={(e) => {
            setSearchText(e.target.value);
            setOffset(0);
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
          sx={{ minWidth: 220 }}
        />

        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="weekly-grid-category-label">Category</InputLabel>
          <Select
            labelId="weekly-grid-category-label"
            label="Category"
            value={categoryFilter}
            onChange={(e) => {
              const nextCategory = e.target.value;
              setCategoryFilter(nextCategory);
              // Narrowing (or widening back) the Category can make the
              // currently-picked Sub Category no longer valid for it
              // (2026-09-23, user-requested cascading filter) - fall back
              // to "All Sub Categories" rather than silently keeping a
              // selection that's no longer even listed in the dropdown.
              const nextSubOptions =
                nextCategory === "all" ? data.subCategoryOptions : data.categorySubCategoryMap[nextCategory] ?? [];
              if (subCategoryFilter !== "all" && !nextSubOptions.includes(subCategoryFilter)) {
                setSubCategoryFilter("all");
              }
              setOffset(0);
            }}
          >
            <MenuItem value="all">All Categories</MenuItem>
            {data.categoryOptions.map((c) => (
              <MenuItem key={c} value={c}>
                {c}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="weekly-grid-subcategory-label">Sub Category</InputLabel>
          <Select
            labelId="weekly-grid-subcategory-label"
            label="Sub Category"
            value={subCategoryFilter}
            onChange={(e) => {
              setSubCategoryFilter(e.target.value);
              setOffset(0);
            }}
          >
            <MenuItem value="all">All Sub Categories</MenuItem>
            {availableSubCategoryOptions.map((sc) => (
              <MenuItem key={sc} value={sc}>
                {sc}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <Button
          onClick={() => setProductionLogOpen(true)}
          size="small"
          variant="outlined"
          startIcon={<EventNoteIcon fontSize="small" />}
          sx={{ ml: "auto" }}
        >
          Production Log
        </Button>

        <Button
          size="small"
          variant="outlined"
          startIcon={<DownloadIcon fontSize="small" />}
          onClick={handleExportAll}
          disabled={exporting || data.total === 0}
        >
          {exporting ? "Exporting…" : "Export Full Report"}
        </Button>
      </Box>

      {/* Style-lifecycle panel: styles present last calendar year vs newly
          launched this one, for the currently selected Sub Category
          (2026-09-22, user-requested). Hidden for "All Sub Categories" -
          there's no single meaningful count to show across all of them at
          once (see the one-off chat report for that view). */}
      {subCategoryFilter !== "all" && lifecycleEntry && (
        <Paper key={subCategoryFilter} variant="outlined" sx={{ p: 1.5, mb: 2 }}>
          <Stack direction={{ xs: "column", md: "row" }} spacing={3}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>
                PRESENT IN {lifecycleQuery.data?.lastYear} ({presentStylesFiltered.length})
              </Typography>
              <StyleChipList styles={presentStylesFiltered} />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>
                ADDED IN {lifecycleQuery.data?.thisYear} ({addedStylesFiltered.length})
              </Typography>
              <StyleChipList styles={addedStylesFiltered} color="primary" />
            </Box>
          </Stack>
        </Paper>
      )}

      {/* Catalog forecast-tier filter (Cloud SQL CatalogStyle.forecastStatus,
          2026-09-21, user-requested) - a button per tier, clicking one shows
          only that tier's Styles in the table below. */}
      {catalogQuery.data && catalogQuery.data.tiers.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center", mb: 2 }}>
          <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, mr: 0.5 }}>
            TIER:
          </Typography>
          <Button
            onClick={() => {
              setTierFilter("all");
              setOffset(0);
            }}
            variant={tierFilter === "all" ? "contained" : "outlined"}
            size="small"
            sx={{
              borderRadius: 999,
              textTransform: "none",
              fontWeight: 700,
              px: 1.75,
              ...(tierFilter === "all"
                ? { bgcolor: ACTUAL_COLOR, "&:hover": { bgcolor: ACTUAL_COLOR } }
                : { color: ACTUAL_COLOR, borderColor: ACTUAL_COLOR }),
            }}
          >
            All Tiers
          </Button>
          {catalogQuery.data.tiers.map((g) => (
            <Button
              key={g.tier}
              onClick={() => {
                setTierFilter(g.tier);
                setOffset(0);
              }}
              variant={tierFilter === g.tier ? "contained" : "outlined"}
              size="small"
              sx={{
                borderRadius: 999,
                textTransform: "none",
                fontWeight: 700,
                px: 1.75,
                ...(tierFilter === g.tier
                  ? { bgcolor: ACTUAL_COLOR, "&:hover": { bgcolor: ACTUAL_COLOR } }
                  : { color: ACTUAL_COLOR, borderColor: ACTUAL_COLOR }),
              }}
            >
              {g.tier}
              <Chip
                label={g.count}
                size="small"
                sx={{
                  ml: 0.75,
                  height: 18,
                  fontSize: "0.68rem",
                  fontWeight: 700,
                  bgcolor: tierFilter === g.tier ? "rgba(255,255,255,0.25)" : "rgba(49,44,92,0.1)",
                  color: tierFilter === g.tier ? "#fff" : ACTUAL_COLOR,
                }}
              />
            </Button>
          ))}
        </Box>
      )}

      <Stack direction="row" spacing={2} sx={{ mb: 1, width: "100%", alignItems: "center", justifyContent: "flex-end" }}>
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700 }}>
          WEEK COLOR:
        </Typography>
        {([
          ["Festival", FESTIVAL_ACCENT, eventTint("festival")],
          ["Sale", SALE_ACCENT, eventTint("sale")],
          ["Both", BOTH_EVENTS_ACCENT, eventTint("both")],
        ] as const).map(([label, accent, tint]) => (
          <Stack key={label} direction="row" alignItems="center" spacing={0.5}>
            <Box
              sx={{
                width: 14,
                height: 14,
                borderRadius: 0.5,
                bgcolor: tint,
                border: `2px solid ${accent}`,
              }}
            />
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              {label}
            </Typography>
          </Stack>
        ))}
      </Stack>

      <Paper variant="outlined" sx={{ overflow: "hidden" }}>
        <TableContainer
          ref={scrollRef}
          sx={{ maxWidth: "100%", overflow: "auto", maxHeight: TABLE_MAX_HEIGHT }}
        >
          <Table size="small" stickyHeader sx={{ borderCollapse: "separate" }}>
            <TableHead>
              <TableRow>
                <TableCell
                  rowSpan={2}
                  sx={{
                    position: "sticky",
                    left: 0,
                    top: 0,
                    zIndex: 3,
                    minWidth: KEY_COL_WIDTH,
                    bgcolor: "background.paper",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    fontSize: "0.72rem",
                    letterSpacing: 0.4,
                    color: "text.secondary",
                    borderBottom: "2px solid",
                    borderColor: "divider",
                    verticalAlign: "bottom",
                  }}
                >
                  STYLE
                </TableCell>
                <TableCell
                  rowSpan={2}
                  sx={{
                    position: "sticky",
                    left: KEY_COL_WIDTH,
                    top: 0,
                    zIndex: 3,
                    minWidth: LAUNCH_DATE_COL_WIDTH,
                    bgcolor: "background.paper",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    fontSize: "0.72rem",
                    letterSpacing: 0.4,
                    color: "text.secondary",
                    borderRight: "1px solid",
                    borderBottom: "2px solid",
                    borderColor: "divider",
                    verticalAlign: "bottom",
                  }}
                >
                  Launch Date
                </TableCell>
                {data.months.map((m, mi) => (
                  <TableCell
                    key={m.label}
                    ref={m.label === currentMonthLabel ? currentMonthCellRef : undefined}
                    colSpan={m.weeks.length + 1}
                    align="center"
                    sx={{
                      position: "sticky",
                      top: 0,
                      fontWeight: 700,
                      fontSize: "0.78rem",
                      bgcolor: mi % 2 === 0 ? "background.paper" : SHADE_COLOR,
                      borderLeft: "1px solid",
                      borderColor: "divider",
                      borderBottom: "1px solid",
                    }}
                  >
                    {m.label.toUpperCase()}
                  </TableCell>
                ))}
                <TableCell
                  rowSpan={2}
                  align="center"
                  sx={{
                    position: "sticky",
                    top: 0,
                    minWidth: 150,
                    bgcolor: "#fff7ed",
                    fontWeight: 700,
                    fontSize: "0.68rem",
                    letterSpacing: 0.4,
                    borderLeft: "2px solid",
                    borderColor: "divider",
                    borderBottom: "2px solid",
                    verticalAlign: "bottom",
                  }}
                >
                  SUGGESTED
                  <br />
                  PRODUCTION QTY
                </TableCell>
              </TableRow>
              <TableRow>
                {data.months.map((m, mi) => (
                  <Fragment key={m.label}>
                    {m.weeks.map((w) => {
                      // Highlight a week that falls inside a real festival
                      // or sale-event window, past or upcoming (2026-09-24,
                      // user-requested) - a different accent per category so
                      // the three are visually distinguishable at a glance
                      // ("both" gets its own accent, not just whichever of
                      // festival/sale happens to be checked first).
                      const accent = w.eventCategory === "both" ? BOTH_EVENTS_ACCENT
                        : w.eventCategory === "festival" ? FESTIVAL_ACCENT
                        : w.eventCategory === "sale" ? SALE_ACCENT
                        : null;
                      const hasEvents = w.events.length > 0;
                      const header = (
                        <TableCell
                          key={w.weekStart}
                          align="right"
                          onClick={
                            hasEvents
                              ? (e) => setEventPopover({ anchorEl: e.currentTarget, week: w })
                              : undefined
                          }
                          sx={{
                            position: "sticky",
                            top: HEADER_ROW1_HEIGHT,
                            fontWeight: accent ? 800 : 600,
                            fontSize: "0.68rem",
                            color: accent ?? "text.secondary",
                            bgcolor: accent ? eventTint(w.eventCategory) : mi % 2 === 0 ? "background.paper" : SHADE_COLOR,
                            borderBottom: accent ? `2px solid ${accent}` : "2px solid",
                            borderColor: accent ?? "divider",
                            cursor: hasEvents ? "pointer" : undefined,
                            textDecoration: hasEvents ? "underline" : undefined,
                            textDecorationStyle: hasEvents ? "dotted" : undefined,
                            textUnderlineOffset: hasEvents ? "2px" : undefined,
                          }}
                        >
                          {w.label}
                        </TableCell>
                      );
                      return accent ? (
                        <Tooltip key={w.weekStart} title={`${w.eventName} - click for exact dates`} arrow placement="top">
                          {header}
                        </Tooltip>
                      ) : (
                        header
                      );
                    })}
                    <TableCell
                      align="right"
                      sx={{
                        position: "sticky",
                        top: HEADER_ROW1_HEIGHT,
                        fontWeight: 700,
                        fontSize: "0.68rem",
                        bgcolor: mi % 2 === 0 ? "background.paper" : SHADE_COLOR,
                        borderBottom: "2px solid",
                        borderColor: "divider",
                      }}
                    >
                      TOTAL
                    </TableCell>
                  </Fragment>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {flatRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={totalColSpan} sx={{ textAlign: "center", py: 4 }}>
                    <Typography variant="body2" sx={{ color: "text.secondary" }}>
                      No rows to show.
                    </Typography>
                  </TableCell>
                </TableRow>
              ) : (
                <>
                  {paddingTop > 0 && (
                    <TableRow>
                      {/* height via style, not sx (2026-09-24, user-reported crash while
                          scrolling) - this changes on every scroll tick, and sx's dynamic
                          value goes through Emotion, which mints a brand-new, never-reused
                          <style> rule for every distinct height seen - confirmed via a
                          Playwright repro to leak thousands of <style> tags per scroll
                          session until the tab ran out of memory and crashed. */}
                      <TableCell colSpan={totalColSpan} sx={{ p: 0, border: 0 }} style={{ height: paddingTop }} />
                    </TableRow>
                  )}
                  {virtualRows.map((virtualRow) => {
                    const item = flatRows[virtualRow.index];

                    if (item.kind === "status") {
                      return (
                        <TableRow
                          key={`${item.style}-status`}
                          data-index={virtualRow.index}
                          ref={rowVirtualizer.measureElement}
                        >
                          <TableCell
                            colSpan={totalColSpan}
                            sx={{ bgcolor: MARKET_ROW_BG, color: "text.secondary", fontStyle: "italic", py: 1 }}
                          >
                            {item.text}
                          </TableCell>
                        </TableRow>
                      );
                    }

                    if (item.kind === "market") {
                      const { series } = item;
                      return (
                        <TableRow
                          key={`${item.style}-${series.marketplace}-${series.source}`}
                          data-index={virtualRow.index}
                          ref={rowVirtualizer.measureElement}
                          sx={{ bgcolor: MARKET_ROW_BG }}
                        >
                          <TableCell
                            sx={{ position: "sticky", left: 0, zIndex: 1, bgcolor: MARKET_ROW_BG, pl: 3.5 }}
                          >
                            <Chip
                              size="small"
                              label={series.marketplace}
                              sx={{
                                fontWeight: 700,
                                fontSize: "0.7rem",
                                bgcolor: series.source === "WEBSITE" ? "#fdf2f8" : "#eef2ff",
                                color: series.source === "WEBSITE" ? "#be185d" : "#3730a3",
                              }}
                            />
                          </TableCell>
                          <TableCell
                            sx={{
                              position: "sticky",
                              left: KEY_COL_WIDTH,
                              zIndex: 1,
                              bgcolor: MARKET_ROW_BG,
                              borderRight: "1px solid",
                              borderColor: "divider",
                            }}
                          />
                          {data.months.map((m) => {
                            const monthTotal = m.weeks.reduce(
                              (acc, w) => acc + (series.cells[w.weekStart] ?? 0),
                              0,
                            );
                            const monthForecastTotal = m.weeks.reduce(
                              (acc, w) => acc + (series.forecastCells[w.weekStart] ?? 0),
                              0,
                            );
                            return (
                              <Fragment key={m.label}>
                                {m.weeks.map((w) => (
                                  <MarketplaceCell
                                    key={w.weekStart}
                                    qty={series.cells[w.weekStart]}
                                    forecast={series.forecastCells[w.weekStart]}
                                  />
                                ))}
                                <TableCell align="right" sx={{ bgcolor: SHADE_COLOR }}>
                                  <Typography variant="body2" sx={{ fontWeight: 700, color: ACTUAL_COLOR }}>
                                    {monthTotal > 0 ? formatNumber(monthTotal) : "—"}
                                  </Typography>
                                  <Typography
                                    variant="caption"
                                    sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block", fontWeight: 600 }}
                                  >
                                    {monthForecastTotal > 0 ? formatNumber(monthForecastTotal) : "—"}
                                  </Typography>
                                </TableCell>
                              </Fragment>
                            );
                          })}
                          <TableCell sx={{ bgcolor: MARKET_ROW_BG, borderLeft: "2px solid", borderColor: "divider" }} />
                        </TableRow>
                      );
                    }

                    const r = item.row;
                    const expanded = expandedStyles.has(r.key);
                    return (
                      <TableRow key={r.key} data-index={virtualRow.index} ref={rowVirtualizer.measureElement} hover>
                        <TableCell
                          sx={{
                            position: "sticky",
                            left: 0,
                            zIndex: 1,
                            bgcolor: "background.paper",
                          }}
                        >
                          <Stack direction="row" alignItems="center" spacing={0.5}>
                            {(() => {
                              const catalogInfo = styleTierMap.get(r.key);
                              return catalogInfo?.imageUrl ? (
                                <Box
                                  component="img"
                                  src={catalogInfo.imageUrl}
                                  alt={r.key}
                                  loading="lazy"
                                  onClick={() => setLightboxStyle(catalogInfo)}
                                  sx={{
                                    width: 28,
                                    height: 28,
                                    borderRadius: 1,
                                    objectFit: "cover",
                                    cursor: "zoom-in",
                                    flexShrink: 0,
                                    border: "1px solid",
                                    borderColor: "divider",
                                  }}
                                />
                              ) : null;
                            })()}
                            <IconButton size="small" onClick={() => toggleExpand(r.key)} sx={{ p: 0.25, ml: -0.5 }}>
                              {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
                            </IconButton>
                            <Typography variant="body2" sx={{ fontWeight: 600 }}>
                              {r.key}
                            </Typography>
                            {r.festivalBoosted && (
                              <Tooltip
                                title="Forecast raised above the base model for the upcoming festival - this Style historically sold well above normal during this same event last year."
                                arrow
                              >
                                <Box component="span" sx={{ fontSize: "0.85rem", cursor: "help" }}>
                                  🎉
                                </Box>
                              </Tooltip>
                            )}
                          </Stack>
                          <Typography variant="caption" sx={{ color: "text.secondary", display: "block", pl: 3.5 }}>
                            {r.subCategory || "Unclassified"}
                          </Typography>
                          <Stack direction="row" alignItems="center" spacing={0.5} sx={{ pl: 3.5 }}>
                            <Typography variant="caption" sx={{ color: "text.secondary" }}>
                              {r.skuCount} SKU{r.skuCount === 1 ? "" : "s"}
                            </Typography>
                            {styleTierMap.get(r.key)?.tier && (
                              <Chip
                                label={styleTierMap.get(r.key)!.tier}
                                size="small"
                                sx={{
                                  height: 16,
                                  fontSize: "0.62rem",
                                  fontWeight: 700,
                                  bgcolor: "rgba(49,44,92,0.1)",
                                  color: ACTUAL_COLOR,
                                }}
                              />
                            )}
                          </Stack>
                        </TableCell>
                        <TableCell
                          sx={{
                            position: "sticky",
                            left: KEY_COL_WIDTH,
                            zIndex: 1,
                            bgcolor: "background.paper",
                            borderRight: "1px solid",
                            borderColor: "divider",
                          }}
                        >
                          {r.launchDate ? (
                            <>
                              <Typography variant="body2">{formatDateShort(r.launchDate)}</Typography>
                              {r.daysSinceLaunch >= 0 && (
                                <Typography variant="caption" sx={{ color: "text.secondary" }}>
                                  {r.daysSinceLaunch}d ago
                                </Typography>
                              )}
                            </>
                          ) : (
                            <Typography variant="body2" sx={{ color: "text.disabled" }}>
                              —
                            </Typography>
                          )}
                        </TableCell>
                        {data.months.map((m) => (
                          <Fragment key={m.label}>
                            {m.weeks.map((w) => (
                              <Cell key={w.weekStart} cell={r.cells[w.weekStart]} eventCategory={w.eventCategory} />
                            ))}
                            <TotalCell
                              actual={r.monthActualTotal[m.label] ?? 0}
                              forecast={r.monthForecastTotal[m.label] ?? 0}
                            />
                          </Fragment>
                        ))}
                        <TableCell
                          align="center"
                          sx={{ bgcolor: "#fff7ed", borderLeft: "2px solid", borderColor: "divider" }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              fontWeight: 800,
                              color: r.suggestedProduction > 0 ? FORECAST_COLOR : "text.disabled",
                            }}
                          >
                            {formatNumber(r.suggestedProduction)}
                          </Typography>
                          <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
                            Fc {formatNumber(r.futureForecastTotal)} · Avl {formatNumber(r.availableQty)}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {paddingBottom > 0 && (
                    <TableRow>
                      <TableCell colSpan={totalColSpan} sx={{ p: 0, border: 0 }} style={{ height: paddingBottom }} />
                    </TableRow>
                  )}
                </>
              )}
              <TableRow>
                <TableCell
                  sx={{
                    position: "sticky",
                    left: 0,
                    bottom: totalStickyBottom,
                    zIndex: 3,
                    bgcolor: SHADE_COLOR,
                    borderTop: "2px solid",
                    borderColor: "divider",
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={0.25}>
                    <IconButton
                      size="small"
                      onClick={() => setTotalExpanded((e) => !e)}
                      sx={{ p: 0.25, ml: -0.5 }}
                    >
                      {totalExpanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
                    </IconButton>
                    <Typography variant="body2" sx={{ fontWeight: 800 }}>
                      TOTAL
                    </Typography>
                  </Stack>
                  <Typography variant="caption" sx={{ color: "text.secondary", pl: 3.5 }}>
                    {formatNumber(data.total)} style{data.total === 1 ? "" : "s"}
                  </Typography>
                </TableCell>
                <TableCell
                  sx={{
                    position: "sticky",
                    left: KEY_COL_WIDTH,
                    bottom: totalStickyBottom,
                    zIndex: 3,
                    bgcolor: SHADE_COLOR,
                    borderRight: "1px solid",
                    borderTop: "2px solid",
                    borderColor: "divider",
                  }}
                />
                {data.months.map((m) => (
                  <Fragment key={m.label}>
                    {m.weeks.map((w) => (
                      <Cell
                        key={w.weekStart}
                        cell={displayTotals.weeklyTotals.cells[w.weekStart]}
                        stickyBottom={totalStickyBottom}
                        showAccuracy={completedWeekStarts.has(w.weekStart)}
                      />
                    ))}
                    <TotalCell
                      actual={displayTotals.weeklyTotals.monthActualTotal[m.label] ?? 0}
                      forecast={displayTotals.weeklyTotals.monthForecastTotal[m.label] ?? 0}
                      stickyBottom={totalStickyBottom}
                      showAccuracy={completedMonthLabels.has(m.label)}
                    />
                  </Fragment>
                ))}
                <TableCell
                  align="center"
                  sx={{
                    position: "sticky",
                    bottom: totalStickyBottom,
                    zIndex: 2,
                    bgcolor: "#fff7ed",
                    borderLeft: "2px solid",
                    borderColor: "divider",
                    borderTop: "2px solid",
                  }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 800, color: FORECAST_COLOR }}>
                    {formatNumber(displayTotals.totalSuggestedProduction)}
                  </Typography>
                </TableCell>
              </TableRow>
              {totalExpanded && totalChannelQuery.data && totalChannelQuery.data.series.length > 0 && (
                totalChannelQuery.data.series.map((series, i) => (
                  <TableRow
                    key={`total-market-${series.marketplace}-${series.source}`}
                    ref={(el) => { marketRowRefs.current[i] = el; }}
                  >
                    <TableCell
                      sx={{
                        position: "sticky",
                        left: 0,
                        bottom: marketStickyBottoms[i] ?? 0,
                        zIndex: 3,
                        bgcolor: MARKET_ROW_BG,
                        pl: 3.5,
                      }}
                    >
                      <Chip
                        size="small"
                        label={series.marketplace}
                        sx={{
                          fontWeight: 700,
                          fontSize: "0.7rem",
                          bgcolor: series.source === "WEBSITE" ? "#fdf2f8" : "#eef2ff",
                          color: series.source === "WEBSITE" ? "#be185d" : "#3730a3",
                        }}
                      />
                    </TableCell>
                    <TableCell
                      sx={{
                        position: "sticky",
                        left: KEY_COL_WIDTH,
                        bottom: marketStickyBottoms[i] ?? 0,
                        zIndex: 3,
                        bgcolor: MARKET_ROW_BG,
                        borderRight: "1px solid",
                        borderColor: "divider",
                      }}
                    />
                    {data.months.map((m) => {
                      const monthTotal = m.weeks.reduce(
                        (acc, w) => acc + (series.cells[w.weekStart] ?? 0),
                        0,
                      );
                      const monthForecastTotal = m.weeks.reduce(
                        (acc, w) => acc + (series.forecastCells[w.weekStart] ?? 0),
                        0,
                      );
                      return (
                        <Fragment key={m.label}>
                          {m.weeks.map((w) => (
                            <TableCell
                              key={w.weekStart}
                              align="right"
                              sx={{
                                position: "sticky",
                                bottom: marketStickyBottoms[i] ?? 0,
                                zIndex: 2,
                                bgcolor: MARKET_ROW_BG,
                              }}
                            >
                              <Typography
                                variant="body2"
                                sx={{ color: series.cells[w.weekStart] ? "text.primary" : "text.disabled" }}
                              >
                                {series.cells[w.weekStart] ? formatNumber(series.cells[w.weekStart]) : "—"}
                              </Typography>
                              <Typography
                                variant="caption"
                                sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block" }}
                              >
                                {series.forecastCells[w.weekStart] ? formatNumber(series.forecastCells[w.weekStart]) : "—"}
                              </Typography>
                            </TableCell>
                          ))}
                          <TableCell
                            align="right"
                            sx={{
                              position: "sticky",
                              bottom: marketStickyBottoms[i] ?? 0,
                              zIndex: 2,
                              bgcolor: SHADE_COLOR,
                            }}
                          >
                            <Typography variant="body2" sx={{ fontWeight: 700, color: ACTUAL_COLOR }}>
                              {monthTotal > 0 ? formatNumber(monthTotal) : "—"}
                            </Typography>
                            <Typography
                              variant="caption"
                              sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block", fontWeight: 600 }}
                            >
                              {monthForecastTotal > 0 ? formatNumber(monthForecastTotal) : "—"}
                            </Typography>
                          </TableCell>
                        </Fragment>
                      );
                    })}
                    <TableCell
                      sx={{
                        position: "sticky",
                        bottom: marketStickyBottoms[i] ?? 0,
                        zIndex: 2,
                        bgcolor: MARKET_ROW_BG,
                        borderLeft: "2px solid",
                        borderColor: "divider",
                      }}
                    />
                  </TableRow>
                ))
              )}
              {totalExpanded && totalChannelQuery.isLoading && (
                <TableRow ref={(el) => { marketRowRefs.current[0] = el; }}>
                  <TableCell
                    colSpan={totalColSpan}
                    sx={{
                      position: "sticky",
                      bottom: marketStickyBottoms[0] ?? 0,
                      zIndex: 2,
                      bgcolor: MARKET_ROW_BG,
                      color: "text.secondary",
                      fontStyle: "italic",
                      py: 1,
                    }}
                  >
                    Loading channel breakdown…
                  </TableCell>
                </TableRow>
              )}
              {totalExpanded && totalChannelQuery.isError && (
                <TableRow ref={(el) => { marketRowRefs.current[0] = el; }}>
                  <TableCell
                    colSpan={totalColSpan}
                    sx={{
                      position: "sticky",
                      bottom: marketStickyBottoms[0] ?? 0,
                      zIndex: 2,
                      bgcolor: MARKET_ROW_BG,
                      color: "text.secondary",
                      fontStyle: "italic",
                      py: 1,
                    }}
                  >
                    Failed to load channel breakdown.
                  </TableCell>
                </TableRow>
              )}
              {totalExpanded && totalChannelQuery.data && totalChannelQuery.data.series.length === 0 && (
                <TableRow ref={(el) => { marketRowRefs.current[0] = el; }}>
                  <TableCell
                    colSpan={totalColSpan}
                    sx={{
                      position: "sticky",
                      bottom: marketStickyBottoms[0] ?? 0,
                      zIndex: 2,
                      bgcolor: MARKET_ROW_BG,
                      color: "text.secondary",
                      fontStyle: "italic",
                      py: 1,
                    }}
                  >
                    No channel sales in this window.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>

        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: SHADE_COLOR,
          }}
        >
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            {tierFilterActive
              ? `${formatNumber(visibleRows.length)} row${visibleRows.length === 1 ? "" : "s"} in tier "${tierFilter}"`
              : `${formatNumber(data.total)} row${data.total === 1 ? "" : "s"}`}
          </Typography>
          {tierFilterActive ? (
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              Showing every matching Style at once - clear the tier filter to page normally.
            </Typography>
          ) : (
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Button
                size="small"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                sx={{ color: "text.secondary" }}
              >
                Prev
              </Button>
              <Typography variant="body2" sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>
                Page {page} of {pageCount}
              </Typography>
              <Button
                size="small"
                variant="outlined"
                disabled={offset + PAGE_SIZE >= data.total}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Next
              </Button>
            </Stack>
          )}
        </Box>
      </Paper>

      <ProductionLogDialog open={productionLogOpen} onClose={() => setProductionLogOpen(false)} />

      <Dialog
        open={lightboxStyle !== null}
        onClose={() => setLightboxStyle(null)}
        maxWidth={false}
        slotProps={{ paper: { sx: { bgcolor: "rgba(10,10,14,0.96)", boxShadow: "none", m: 0, maxHeight: "100vh" } } }}
      >
        {lightboxStyle && (
          <Box
            sx={{
              position: "relative",
              width: "100vw",
              height: "100vh",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <IconButton
              onClick={() => setLightboxStyle(null)}
              sx={{ position: "absolute", top: 16, right: 16, color: "#fff", zIndex: 1 }}
            >
              <CloseIcon />
            </IconButton>
            {lightboxStyle.imageUrl ? (
              <Box
                component="img"
                src={lightboxStyle.imageUrl}
                alt={lightboxStyle.name}
                sx={{ maxWidth: "92vw", maxHeight: "82vh", objectFit: "contain" }}
              />
            ) : (
              <Stack alignItems="center" spacing={1} sx={{ color: "rgba(255,255,255,0.7)" }}>
                <ImageNotSupportedIcon sx={{ fontSize: 64 }} />
                <Typography>No image on file</Typography>
              </Stack>
            )}
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ position: "absolute", bottom: 24, color: "#fff" }}
            >
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                {lightboxStyle.name}
              </Typography>
              {lightboxStyle.tier && (
                <Chip
                  label={lightboxStyle.tier}
                  size="small"
                  sx={{ bgcolor: "rgba(255,255,255,0.18)", color: "#fff", fontWeight: 700 }}
                />
              )}
            </Stack>
          </Box>
        )}
      </Dialog>

      <Popover
        open={eventPopover !== null}
        anchorEl={eventPopover?.anchorEl ?? null}
        onClose={() => setEventPopover(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        transformOrigin={{ vertical: "top", horizontal: "center" }}
      >
        {eventPopover && (
          <Box sx={{ p: 1.5, maxWidth: 340 }}>
            <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>
              {eventPopover.week.label} · WEEK OF {formatDateWithYear(eventPopover.week.weekStart)}
            </Typography>
            <Stack spacing={1} sx={{ mt: 1 }}>
              {eventPopover.week.events.map((ev, i) => (
                <Stack key={`${ev.name}-${i}`} spacing={0.25}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Box
                      sx={{
                        width: 10,
                        height: 10,
                        borderRadius: "50%",
                        bgcolor: ev.category === "festival" ? FESTIVAL_ACCENT : SALE_ACCENT,
                        flexShrink: 0,
                      }}
                    />
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>
                      {ev.name}
                    </Typography>
                  </Stack>
                  <Typography variant="caption" sx={{ color: "text.secondary", pl: 2.25 }}>
                    {formatDateWithYear(ev.start)} – {formatDateWithYear(ev.end)}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        )}
      </Popover>
    </Box>
  );
}
