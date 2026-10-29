import { Fragment, useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import StorefrontOutlinedIcon from "@mui/icons-material/StorefrontOutlined";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import CloseIcon from "@mui/icons-material/Close";
import AboutButton from "./AboutButton";
import { DOWN, UP } from "./TrendBadge";
import { useCatalogStyleTiers } from "@/hooks/useDashboardData";
import { formatNumber } from "@/utils/format";
import { COLORS, REPORT, REPORT_HEAD_SX, REPORT_GROUP_HEAD_SX, STICKY_EDGE_SX, eventAccent as reportAccent, eventTintOf } from "@/theme/colors";
import type { CatalogStyleImage, DesignLevel, DesignProductCard, DesignRecommendedStyle, DesignSeason, DesignStage } from "@/types";

// The Weekly Sales Report's own look, copied exactly (2026-10-27, user-
// requested: "copy the colour, font and style"): its navy actual / orange
// forecast numbers, shaded months and TOTAL cells, festival / sale week
// accents and tints, and the cream summary column.
// 2026-10-29: now read from theme/colors.ts REPORT, the report's portal palette.
const ACTUAL_COLOR = REPORT.actual;
const FORECAST_COLOR = REPORT.forecastText;
const SHADE_COLOR = REPORT.totalBg;
const SUMMARY_BG = REPORT.summaryBg;
const GROUP_BG = REPORT.rowAltBg;
const ACCENT_SOFT = "rgba(49,44,92,0.08)";
const MUTED = COLORS.textSecondary;
const HEADER_ROW1_HEIGHT = 37;
// Narrower pinned column on phones (2026-10-29, responsive layout)
const STYLE_COL = { xs: 170, md: 240 } as const;
const FEATURES_COL = 300;
const WEEK_COL = 72;

// Festival = amber, Sale = blue, Both = rose - the report's week colours.
function eventAccent(category: string): string | null {
  return reportAccent(category) ?? null;
}
function eventTint(category: string): string | undefined {
  return eventTintOf(category);
}

const FEATURE_ORDER = ["category", "subCategory", "colour", "fabric", "embroidery", "neck", "sleeve", "topType"];
const FEATURE_LABEL: Record<string, string> = {
  category: "Category", subCategory: "Sub-category", colour: "Colour", fabric: "Fabric",
  embroidery: "Embroidery", neck: "Neck", sleeve: "Sleeve", topType: "Top type",
};
const DETAIL_ATTRS = ["embroidery", "neck", "sleeve", "topType"] as const;
const LEVEL_LABEL: Record<DesignLevel, string> = { strong: "Strong", good: "Good", trySmall: "Try small" };
// Features listed trending first, then in trend, then those without a trend
// status (embroidery, sleeve, top type), then fading / out (2026-10-26, user-requested).
const STAGE_ORDER: Record<string, number> = { trending: 0, steady: 1, none: 2, fading: 3, out: 4 };
const STAGE_MARK: Record<DesignStage, { mark: string; color: string; word: string }> = {
  trending: { mark: "▲", color: UP, word: "Trending" },
  steady: { mark: "●", color: MUTED, word: "In trend" },
  fading: { mark: "▼", color: "#b45309", word: "Fading" },
  out: { mark: "⊘", color: DOWN, word: "Out of trend" },
};

type Photo = { style: string; subCategory: string; tier: string; imageUrl: string };
type Week = NonNullable<NonNullable<DesignSeason["expectedSaleWindow"]>["weekList"]>[number];

function dayMonth(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1]}`;
}

function StageMark({ stage }: { stage?: DesignStage | "none" | null }) {
  const s = stage && stage !== "none" ? STAGE_MARK[stage] : null;
  if (!s) return null;
  return (
    <Tooltip title={s.word}>
      <Box component="span" sx={{ color: s.color, fontSize: "0.7rem", mr: 0.5 }}>{s.mark}</Box>
    </Tooltip>
  );
}

/** One feature of a design: green when it matches the suggestion (with its
 * trend mark), grey and crossed out (with what the design has instead) when not. */
function Feature({ attr, value, matched, stage }: { attr: string; value: string; matched: boolean; stage?: DesignStage }) {
  const s = stage ? STAGE_MARK[stage] : null;
  return (
    <Tooltip title={`${FEATURE_LABEL[attr] ?? attr}${matched ? ` — matches the suggestion${s ? ` · ${s.word}` : ""}` : " — not the suggested one"}`}>
      <Box
        component="span"
        sx={{
          display: "inline-flex", alignItems: "center", gap: 0.4,
          px: 0.75, py: 0.1, borderRadius: 1, fontSize: "0.7rem", fontWeight: 700, whiteSpace: "nowrap",
          bgcolor: matched ? "rgba(21,128,61,0.1)" : "rgba(100,116,139,0.1)",
          color: matched ? UP : MUTED,
          textDecoration: matched ? "none" : "line-through",
        }}
      >
        {s && <Box component="span" sx={{ color: s.color, fontSize: "0.62rem" }}>{s.mark}</Box>}
        {value || `no ${(FEATURE_LABEL[attr] ?? attr).toLowerCase()}`}
      </Box>
    </Tooltip>
  );
}

/** A feature the sub-category's card suggests (on a colour x sub-category row). */
function Suggested({ attr, value, stage }: { attr: string; value: string; stage?: DesignStage | "none" | null }) {
  const s = stage && stage !== "none" ? STAGE_MARK[stage] : null;
  return (
    <Tooltip title={`${FEATURE_LABEL[attr] ?? attr} suggested for this sub-category${s ? ` · ${s.word}` : ""}`}>
      <Box
        component="span"
        sx={{
          display: "inline-flex", alignItems: "center", gap: 0.4, px: 0.75, py: 0.1, borderRadius: 1,
          fontSize: "0.7rem", fontWeight: 700, whiteSpace: "nowrap", bgcolor: "rgba(49,44,92,0.1)", color: ACTUAL_COLOR,
        }}
      >
        {s && <Box component="span" sx={{ color: s.color, fontSize: "0.62rem" }}>{s.mark}</Box>}
        {value}
      </Box>
    </Tooltip>
  );
}

const DASH = <span style={{ color: "#94a3b8" }}>—</span>;

/** A week cell exactly like the Weekly Sales Report's: sold so far on top
 * ("—" before the week starts), the forecast in orange underneath; the
 * running week faded, festival / sale weeks tinted. */
function WeekCell({ sold, forecast, tint, partial }: { sold?: number | null; forecast: number; tint?: string; partial?: boolean }) {
  return (
    <TableCell align="right" sx={{ opacity: partial ? 0.6 : 1, minWidth: WEEK_COL, ...(tint ? { bgcolor: tint } : {}) }}>
      <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.3 }}>
        {sold === null || sold === undefined ? DASH : formatNumber(sold)}
      </Typography>
      <Typography variant="caption" sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block" }}>{formatNumber(forecast)}</Typography>
    </TableCell>
  );
}

/** A month TOTAL cell exactly like the report's. */
function TotalCell({ sold, forecast }: { sold?: number | null; forecast: number }) {
  return (
    <TableCell align="right" sx={{ bgcolor: SHADE_COLOR, minWidth: WEEK_COL }}>
      <Typography variant="body2" sx={{ fontWeight: 700, lineHeight: 1.3, color: ACTUAL_COLOR }}>
        {sold === null || sold === undefined ? DASH : formatNumber(sold)}
      </Typography>
      <Typography variant="caption" sx={{ color: FORECAST_COLOR, lineHeight: 1.3, display: "block", fontWeight: 600 }}>{formatNumber(forecast)}</Typography>
    </TableCell>
  );
}

/** Existing styles to push (2026-10-26, user-requested), based on colour and
 * sub-category, in the Weekly Sales Report's layout and look (2026-10-27,
 * user-requested): one row per colour x sub-category with its designs under
 * it, the next 9 report weeks as columns grouped by month, and the expected
 * sale on the right. */
export default function RecommendedStyles({
  styles,
  products,
  window,
}: {
  styles: DesignRecommendedStyle[];
  products?: DesignProductCard[];
  window?: DesignSeason["expectedSaleWindow"];
}) {
  const forecastUnavailable = Boolean(window?.unavailable);
  const saleLabel = window?.from && window?.to ? `${window.from} – ${window.to}` : "next 9 weeks";
  const weekList: Week[] = useMemo(() => window?.weekList ?? [], [window]);
  const running = Boolean(window?.running);
  // Report weeks grouped into their months, each with its index range.
  const months = useMemo(() => {
    const out: { label: string; idx: number[] }[] = [];
    weekList.forEach((w, i) => {
      if (out.length && out[out.length - 1].label === w.month) out[out.length - 1].idx.push(i);
      else out.push({ label: w.month, idx: [i] });
    });
    return out;
  }, [weekList]);
  // Colour x sub-category pairs, in the API's order (card rank, then the
  // card's colour order, best first).
  const pairs = useMemo(() => {
    const map = new Map<string, DesignRecommendedStyle[]>();
    for (const s of styles) {
      const k = `${s.subCategory}|${s.colour}`;
      map.set(k, [...(map.get(k) ?? []), s]);
    }
    return [...map.entries()].map(([key, items]) => ({
      key,
      items,
      weekly: weekList.map((_, i) => items.reduce((sum, s) => sum + (s.weekly?.[i] ?? 0), 0)),
      sold: items.some((s) => s.soldSoFar !== null && s.soldSoFar !== undefined)
        ? items.reduce((sum, s) => sum + (s.soldSoFar ?? 0), 0) : null,
      total: items.reduce((sum, s) => sum + s.expectedSale, 0),
    }));
  }, [styles, weekList]);
  const cardOf = useMemo(() => new Map((products ?? []).map((p) => [p.subCategory, p])), [products]);
  const [openPairs, setOpenPairs] = useState<Set<string>>(() => new Set(pairs.length ? [pairs[0].key] : []));
  const [photo, setPhoto] = useState<Photo | null>(null);
  const catalog = useCatalogStyleTiers();
  const info = useMemo(() => {
    const map = new Map<string, CatalogStyleImage>();
    for (const g of catalog.data?.tiers ?? []) for (const st of g.styles) map.set(st.name.toUpperCase(), st);
    return map;
  }, [catalog.data]);
  const togglePair = (k: string) =>
    setOpenPairs((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  const subCategories = new Set(styles.map((s) => s.subCategory)).size;
  const monthBg = (_mi: number) => REPORT.headBg; // header cells: the report's pale blue
  const sticky = (bg: string) => ({ position: "sticky" as const, left: 0, zIndex: 1, bgcolor: bg });
  const headSx = REPORT_HEAD_SX;

  /** The 9 week cells + a TOTAL per month, for one row. */
  const weekCells = (weekly: number[], sold: number | null | undefined) =>
    months.map((m) => (
      <Fragment key={m.label}>
        {m.idx.map((i) => (
          <WeekCell
            key={i}
            sold={i === 0 && running ? sold ?? 0 : null}
            forecast={weekly[i] ?? 0}
            tint={eventTint(weekList[i]?.eventCategory ?? "")}
            partial={i === 0 && running}
          />
        ))}
        <TotalCell sold={m.idx.includes(0) && running ? sold ?? 0 : null} forecast={m.idx.reduce((sum, i) => sum + (weekly[i] ?? 0), 0)} />
      </Fragment>
    ));

  return (
    <Box>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mb: 0.5 }}>
        <StorefrontOutlinedIcon fontSize="small" sx={{ color: ACTUAL_COLOR }} />
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Styles to push</Typography>
        <AboutButton topic="styles" />
      </Stack>
      <Typography variant="body2" sx={{ color: "text.secondary", mb: 1.5 }}>
        {forecastUnavailable
          ? "Existing designs in a suggested colour of a suggested sub-category. The 9-week forecast is temporarily unavailable."
          : `Existing designs in a suggested colour of a suggested sub-category - one row per colour and sub-category (best first), its designs under it. Top number = sold so far, orange = forecast, the same numbers as the Weekly Sales Report; expected sale = ${saleLabel}.`}
      </Typography>
      {styles.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 3, color: "text.secondary" }}>No existing design matches the suggested colours yet.</Paper>
      ) : (
        <>
          <Stack direction="row" spacing={2} sx={{ mb: 1, width: "100%", alignItems: "center" }}>
            <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, flexGrow: 1 }}>
              {pairs.length} COLOUR × SUB-CATEGORY ROWS · {subCategories} SUB-CATEGORIES · {styles.length} DESIGNS
            </Typography>
            <Button size="small" onClick={() => setOpenPairs(new Set(pairs.map((p) => p.key)))} sx={{ textTransform: "none", fontWeight: 700 }}>
              Expand all
            </Button>
            <Button size="small" onClick={() => setOpenPairs(new Set())} sx={{ textTransform: "none", fontWeight: 700 }}>
              Collapse all
            </Button>
            <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700 }}>WEEK COLOR:</Typography>
            {(["festival", "sale", "both"] as const).map((cat) => (
              <Stack key={cat} direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                <Box sx={{ width: 14, height: 14, borderRadius: 0.5, bgcolor: eventTint(cat), border: `2px solid ${eventAccent(cat)}` }} />
                <Typography variant="caption" sx={{ color: "text.secondary" }}>{cat === "festival" ? "Festival" : cat === "sale" ? "Sale" : "Both"}</Typography>
              </Stack>
            ))}
          </Stack>
          <Paper variant="outlined" sx={{ overflow: "hidden" }}>
            <TableContainer sx={{ maxWidth: "100%", overflow: "auto", maxHeight: 760 }}>
              <Table size="small" stickyHeader sx={{ borderCollapse: "separate" }}>
                <TableHead>
                  <TableRow>
                    <TableCell rowSpan={2} sx={{ ...headSx, ...sticky(REPORT.headBg), top: 0, zIndex: 3, minWidth: STYLE_COL, verticalAlign: "bottom" }}>
                      Colour · Sub-category / Style
                    </TableCell>
                    <TableCell rowSpan={2} sx={{ ...headSx, minWidth: FEATURES_COL, verticalAlign: "bottom", ...STICKY_EDGE_SX, boxShadow: "none" }}>
                      Features
                    </TableCell>
                    {months.map((m) => (
                      <TableCell
                        key={m.label}
                        colSpan={m.idx.length + 1}
                        align="center"
                        sx={{ ...REPORT_GROUP_HEAD_SX, borderLeft: "1px solid", borderColor: "divider", borderBottom: "1px solid" }}
                      >
                        {m.label.toUpperCase()}
                      </TableCell>
                    ))}
                    <TableCell
                      rowSpan={2}
                      align="center"
                      sx={{ minWidth: 150, bgcolor: SUMMARY_BG, fontWeight: 700, fontSize: "0.68rem", letterSpacing: 0.4, borderLeft: `2px solid ${COLORS.borderDark}`, borderBottom: `2px solid ${COLORS.borderDark}`, verticalAlign: "bottom" }}
                    >
                      <Tooltip title="Weekly Sales Report forecast summed over the running report week and the 8 after it (W1 1-7, W2 8-15, W3 16-23, W4 24-end); the running week counts what has sold so far plus the current pace for its remaining days.">
                        <span>EXPECTED SALE<br />{forecastUnavailable ? "(UNAVAILABLE)" : `(${saleLabel.toUpperCase()})`}</span>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    {months.map((m, mi) => (
                      <Fragment key={m.label}>
                        {m.idx.map((i) => {
                          const w = weekList[i];
                          const accent = eventAccent(w.eventCategory);
                          const tip = `${dayMonth(w.start)} – ${dayMonth(w.end)}${i === 0 && running ? " · running week" : ""}${w.eventName ? ` · ${w.eventName}` : ""}`;
                          return (
                            <Tooltip key={i} title={tip} arrow placement="top">
                              <TableCell
                                align="right"
                                sx={{
                                  top: HEADER_ROW1_HEIGHT,
                                  // As in the report: event weeks bold, not coloured; the column tint marks them
                                  fontWeight: accent ? 800 : 600,
                                  fontSize: "0.68rem",
                                  color: accent ? COLORS.textDark : "text.secondary",
                                  bgcolor: monthBg(mi),
                                  borderBottom: `2px solid ${COLORS.borderDark}`,
                                  textDecoration: w.eventName ? "underline" : undefined,
                                  textDecorationStyle: w.eventName ? "dotted" : undefined,
                                  textUnderlineOffset: w.eventName ? "2px" : undefined,
                                }}
                              >
                                {w.label}
                              </TableCell>
                            </Tooltip>
                          );
                        })}
                        <TableCell
                          align="right"
                          sx={{ top: HEADER_ROW1_HEIGHT, fontWeight: 700, fontSize: "0.68rem", bgcolor: monthBg(mi), color: COLORS.primary, borderBottom: `2px solid ${COLORS.borderDark}` }}
                        >
                          TOTAL
                        </TableCell>
                      </Fragment>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {pairs.map(({ key, items, weekly, sold, total }) => {
                    const pOpen = openPairs.has(key);
                    const top = items[0];
                    const card = cardOf.get(top.subCategory);
                    return (
                      <Fragment key={key}>
                        {/* Colour x sub-category row: its designs summed */}
                        <TableRow hover onClick={() => togglePair(key)} sx={{ cursor: "pointer" }}>
                          <TableCell sx={{ ...sticky(GROUP_BG), minWidth: STYLE_COL }}>
                            <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                              <IconButton size="small" sx={{ p: 0.25 }} aria-label={pOpen ? `Hide ${top.colour} ${top.subCategory} designs` : `Show ${top.colour} ${top.subCategory} designs`}>
                                {pOpen ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
                              </IconButton>
                              <Typography variant="body2" sx={{ fontWeight: 700 }}>
                                <StageMark stage={top.colourStage} />{top.colour}
                              </Typography>
                            </Stack>
                            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", pl: 3.5 }}>
                              <StageMark stage={top.featureStages?.subCategory} />{top.subCategory}
                            </Typography>
                            <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", pl: 3.5 }}>
                              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                                {items.length} design{items.length === 1 ? "" : "s"} · colour #{top.colourRank + 1}
                              </Typography>
                              <Chip
                                label={LEVEL_LABEL[top.productLevel] ?? top.productLevel}
                                size="small"
                                sx={{ height: 16, fontSize: "0.62rem", fontWeight: 700, bgcolor: "rgba(49,44,92,0.1)", color: ACTUAL_COLOR }}
                              />
                            </Stack>
                          </TableCell>
                          <TableCell sx={{ bgcolor: GROUP_BG, borderRight: "1px solid", borderColor: "divider" }}>
                            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                              {(card?.use.fabric ?? []).map((f) => <Suggested key={`f-${f.value}`} attr="fabric" value={f.value} stage={f.stage} />)}
                              {DETAIL_ATTRS.map((a) => card?.use.details[a] ? <Suggested key={a} attr={a} value={card.use.details[a] as string} /> : null)}
                            </Box>
                          </TableCell>
                          {weekCells(weekly, sold)}
                          <TableCell align="center" sx={{ bgcolor: SUMMARY_BG, borderLeft: "2px solid", borderColor: "divider" }}>
                            <Typography variant="body2" sx={{ fontWeight: 800, color: total > 0 ? FORECAST_COLOR : "text.disabled" }}>
                              {forecastUnavailable ? "—" : formatNumber(total)}
                            </Typography>
                            <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
                              {items.length} design{items.length === 1 ? "" : "s"}
                            </Typography>
                          </TableCell>
                        </TableRow>
                        {pOpen && items.map((s) => {
                          const cat = info.get(s.style.toUpperCase());
                          const stages = s.featureStages ?? {};
                          const matched = FEATURE_ORDER
                            // colour and sub-category are the row above
                            .filter((a) => a in s.matchedFeatures && a !== "category" && a !== "subCategory" && a !== "colour")
                            .sort((a, b) => STAGE_ORDER[stages[a] ?? "none"] - STAGE_ORDER[stages[b] ?? "none"]);
                          const missing = FEATURE_ORDER.filter((a) => s.missingFeatures.includes(a) && a !== "category" && a !== "subCategory");
                          return (
                            <TableRow key={`${key}-${s.style}`} hover>
                              <TableCell sx={{ ...sticky("background.paper"), minWidth: STYLE_COL, pl: 4 }}>
                                <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
                                  {cat?.imageUrl ? (
                                    <Tooltip title="View photo">
                                      <Box
                                        component="img"
                                        src={cat.imageUrl}
                                        alt={s.style}
                                        loading="lazy"
                                        onClick={() => setPhoto({ style: s.style, subCategory: s.subCategory, tier: cat.tier, imageUrl: cat.imageUrl })}
                                        sx={{ width: 28, height: 28, borderRadius: 1, objectFit: "cover", cursor: "zoom-in", flexShrink: 0, border: "1px solid", borderColor: "divider" }}
                                      />
                                    </Tooltip>
                                  ) : (
                                    <Box sx={{ width: 28, height: 28, borderRadius: 1, bgcolor: ACCENT_SOFT, flexShrink: 0 }} />
                                  )}
                                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.style}</Typography>
                                </Stack>
                                <Typography variant="caption" sx={{ color: "text.secondary", display: "block", pl: 4.5 }}>{s.subCategory}</Typography>
                                <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", pl: 4.5 }}>
                                  <Typography variant="caption" sx={{ color: "text.secondary" }}>{s.colour}</Typography>
                                  {cat?.tier && (
                                    <Chip label={cat.tier} size="small" sx={{ height: 16, fontSize: "0.62rem", fontWeight: 700, bgcolor: "rgba(49,44,92,0.1)", color: ACTUAL_COLOR }} />
                                  )}
                                </Stack>
                              </TableCell>
                              <TableCell sx={{ borderRight: "1px solid", borderColor: "divider" }}>
                                <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
                                  <Tooltip title={`Matches ${s.featureMatchCount} of the ${s.suggestedFeatureCount} suggested features`}>
                                    <Typography variant="body2" sx={{ fontWeight: 700, color: s.featureMatchCount === s.suggestedFeatureCount ? UP : ACTUAL_COLOR, whiteSpace: "nowrap" }}>
                                      {s.featureMatchCount}/{s.suggestedFeatureCount}
                                    </Typography>
                                  </Tooltip>
                                  <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                                    {matched.map((a) => <Feature key={a} attr={a} value={s.matchedFeatures[a]} matched stage={stages[a]} />)}
                                    {missing.map((a) => <Feature key={a} attr={a} value={s.styleFeatures?.[a] ?? ""} matched={false} />)}
                                  </Box>
                                </Stack>
                              </TableCell>
                              {weekCells(s.weekly ?? [], s.soldSoFar)}
                              <TableCell align="center" sx={{ bgcolor: SUMMARY_BG, borderLeft: "2px solid", borderColor: "divider" }}>
                                <Typography variant="body2" sx={{ fontWeight: 800, color: s.expectedSale > 0 ? FORECAST_COLOR : "text.disabled" }}>
                                  {forecastUnavailable ? "—" : formatNumber(s.expectedSale)}
                                </Typography>
                                <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
                                  {s.featureMatchCount}/{s.suggestedFeatureCount} features
                                </Typography>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </>
      )}
      <Dialog
        open={photo !== null}
        onClose={() => setPhoto(null)}
        maxWidth={false}
        // The panel wraps the photo (no full-screen black area); the page is only dimmed.
        slotProps={{
          paper: { sx: { bgcolor: "#111", m: 2, borderRadius: 2, overflow: "hidden", maxWidth: "calc(100vw - 32px)", maxHeight: "calc(100vh - 32px)" } },
          backdrop: { sx: { bgcolor: "rgba(10,10,14,0.7)" } },
        }}
      >
        {photo && (
          <Box sx={{ position: "relative", display: "flex", flexDirection: "column" }}>
            <IconButton
              onClick={() => setPhoto(null)}
              aria-label="Close photo"
              size="small"
              sx={{ position: "absolute", top: 8, right: 8, color: "#fff", bgcolor: "rgba(0,0,0,0.45)", "&:hover": { bgcolor: "rgba(0,0,0,0.65)" }, zIndex: 1 }}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
            <Box
              component="img"
              src={photo.imageUrl}
              alt={photo.style}
              sx={{ display: "block", maxWidth: "calc(100vw - 32px)", maxHeight: "calc(100vh - 96px)", objectFit: "contain" }}
            />
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", justifyContent: "center", px: 2, py: 1, color: "#fff" }}>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>{photo.style}</Typography>
              <Typography variant="body2" sx={{ color: "rgba(255,255,255,0.7)" }}>{photo.subCategory}</Typography>
              {photo.tier && (
                <Chip label={photo.tier} size="small" sx={{ bgcolor: "rgba(255,255,255,0.18)", color: "#fff", fontWeight: 700 }} />
              )}
            </Stack>
          </Box>
        )}
      </Dialog>
    </Box>
  );
}
