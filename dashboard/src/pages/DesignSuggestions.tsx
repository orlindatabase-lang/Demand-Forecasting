import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import TrendingDownIcon from "@mui/icons-material/TrendingDown";
import WhatshotIcon from "@mui/icons-material/Whatshot";
import PageHeader from "@/components/common/PageHeader";
import AboutButton from "@/components/design/AboutButton";
import TrendStatus from "@/components/design/TrendStatus";
import ProductCards from "@/components/design/ProductCards";
import RecommendedStyles from "@/components/design/RecommendedStyles";
import { RULES } from "@/components/design/TrendBadge";
import type { AboutKey } from "@/components/design/aboutContent";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { useDesignSuggestions } from "@/hooks/useDashboardData";
import { formatNumber } from "@/utils/format";
import type { DesignDriver, DesignLifecycle, DesignSeason, DesignTrendingNow, DesignWindow } from "@/types";
import { COLORS, REPORT, REPORT_HEAD_SX } from "@/theme/colors";

const ACCENT = REPORT.actual;
const ACCENT_SOFT = "rgba(49,44,92,0.08)";
const UP = REPORT.good;
const DOWN = REPORT.bad;
const MUTED = COLORS.textSecondary;

const ATTR_LABEL: Record<string, string> = {
  category: "Category", subCategory: "Sub Category", colour: "Colour", fabric: "Fabric",
  embroidery: "Embroidery", neck: "Neck", sleeve: "Sleeve", topType: "Top type",
};

function Momentum({ value, label }: { value: number | null | undefined; label?: string }) {
  if (value === null || value === undefined) {
    return label ? null : <Typography component="span" variant="caption" sx={{ color: MUTED }}>—</Typography>;
  }
  const up = value >= 0;
  return (
    <Box component="span" sx={{ whiteSpace: "nowrap" }}>
      {label && <Box component="span" sx={{ color: MUTED, mr: 0.4 }}>{label}</Box>}
      <Box component="span" sx={{ color: up ? UP : DOWN, fontWeight: 700 }}>
        {up ? "▲ +" : "▼ "}
        {value.toFixed(0)}%
      </Box>
    </Box>
  );
}

/** Section title + one-line plain explanation + About button. */
function SectionTitle({ icon, title, hint, topic }: { icon?: React.ReactNode; title: string; hint: string; topic?: AboutKey }) {
  return (
    <Box sx={{ mb: 1.25 }}>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
        {icon}
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{title}</Typography>
        {topic && <AboutButton topic={topic} />}
      </Stack>
      <Typography variant="body2" sx={{ color: MUTED }}>{hint}</Typography>
    </Box>
  );
}

function DriverList({ title, items, positive }: { title: string; items: DesignDriver[]; positive: boolean }) {
  const max = Math.max(1, ...items.map((d) => Math.abs(d.effectPct)));
  return (
    <Box sx={{ flex: "1 1 320px" }}>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mb: 1 }}>
        {positive ? <TrendingUpIcon fontSize="small" sx={{ color: UP }} /> : <TrendingDownIcon fontSize="small" sx={{ color: DOWN }} />}
        <Typography variant="subtitle2" sx={{ fontWeight: 800 }}>{title}</Typography>
      </Stack>
      <Stack spacing={0.8}>
        {items.map((d) => (
          <Box key={`${d.attribute}-${d.value}`}>
            <Stack direction="row" spacing={1} sx={{ alignItems: "baseline" }}>
              <Typography variant="caption" sx={{ color: MUTED, width: 82, flexShrink: 0 }}>{ATTR_LABEL[d.attribute] ?? d.attribute}</Typography>
              <Typography variant="body2" sx={{ fontWeight: 600, flexGrow: 1 }} noWrap>{d.value}</Typography>
              <Typography variant="body2" sx={{ fontWeight: 800, color: positive ? UP : DOWN }}>
                {d.effectPct > 0 ? "+" : ""}{d.effectPct.toFixed(0)}%
              </Typography>
            </Stack>
            <Box sx={{ ml: "90px", height: 4, borderRadius: 2, bgcolor: ACCENT_SOFT }}>
              <Box sx={{ width: `${(Math.abs(d.effectPct) / max) * 100}%`, height: "100%", borderRadius: 2, bgcolor: positive ? UP : DOWN, opacity: 0.75 }} />
            </Box>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1]}`;
}

const SUB_STAGES = [
  { key: "both", label: "Trending + In trend" },
  { key: "trending", label: "Trending" },
  { key: "steady", label: "In trend" },
] as const;
const STAGE_CHIP: Record<string, { label: string; color: string }> = {
  trending: { label: "Trending", color: "#0ca30c" },
  steady: { label: "In trend", color: ACCENT },
};

/** Trending designs: the designs selling more right now (last 4 report weeks
 * vs the 4 before) - the same for both seasons. Factor trends are in Trend status.
 * Every growing design whose sub category is Trending or In trend in Trend
 * status (2026-10-29, user-requested: was the top 20 of all). */
function TrendingNow({ trending, window, lifecycle }: { trending: DesignTrendingNow; window?: DesignWindow; lifecycle?: DesignLifecycle }) {
  const running = trending.runningWeek ?? window?.runningWeek ?? "this week";
  const [subStage, setSubStage] = useState<(typeof SUB_STAGES)[number]["key"]>("both");
  const stageOf = useMemo(() => new Map((lifecycle?.subCategory ?? []).map((it) => [it.value, it.stage as string])), [lifecycle]);
  const inStage = useMemo(
    () => trending.styles.filter((st) => ["trending", "steady"].includes(stageOf.get(st.subCategory) ?? "")),
    [trending, stageOf],
  );
  const count = (k: string) => (k === "both" ? inStage.length : inStage.filter((st) => stageOf.get(st.subCategory) === k).length);
  const rows = subStage === "both" ? inStage : inStage.filter((st) => stageOf.get(st.subCategory) === subStage);
  // Paging (2026-10-29, user-requested): 25 a page, or All.
  const [page, setPage] = useState(0);
  const [perPage, setPerPage] = useState(25);
  const lastPage = perPage < 0 ? 0 : Math.max(0, Math.ceil(rows.length / perPage) - 1);
  const pageNo = Math.min(page, lastPage);
  const shown = perPage < 0 ? rows : rows.slice(pageNo * perPage, (pageNo + 1) * perPage);
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <SectionTitle
        icon={<WhatshotIcon fontSize="small" sx={{ color: "#ea580c" }} />}
        title="Trending designs"
        hint={`Every design selling more in a Trending or In trend sub category, biggest gain first: ${trending.nowWeeks ?? `${shortDate(trending.nowFrom)} – ${shortDate(trending.nowTo)}`} compared with ${trending.beforeWeeks ?? "the 4 weeks before"}. Pieces are real sales; growth also counts the rest of ${running} by its forecast (hover a growth number to see how).`}
        topic="trending"
      />
      <ToggleButtonGroup
        exclusive
        size="small"
        value={subStage}
        onChange={(_e, v) => {
          if (v) {
            setSubStage(v);
            setPage(0);
          }
        }}
        sx={{ mb: 1.25, flexWrap: "wrap", "& .MuiToggleButton-root": { textTransform: "none", fontWeight: 700, px: 1.5 } }}
      >
        {SUB_STAGES.map((o) => (
          <ToggleButton key={o.key} value={o.key} sx={{ "&.Mui-selected": { bgcolor: ACCENT, color: "#fff", "&:hover": { bgcolor: ACCENT } } }}>
            {o.label} ({formatNumber(count(o.key))})
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      {rows.length === 0 && <Typography variant="body2" sx={{ color: MUTED }}>No design is growing fast right now.</Typography>}
      {rows.length > 0 && (
        <TableContainer sx={{ maxHeight: 520 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow sx={{ "& th": REPORT_HEAD_SX }}>
                <TableCell>DESIGN</TableCell>
                <TableCell>SUB CATEGORY</TableCell>
                <TableCell>COLOUR</TableCell>
                <TableCell>FABRIC</TableCell>
                <TableCell>DETAILS</TableCell>
                <TableCell align="right">SOLD SO FAR</TableCell>
                <TableCell align="right">4 WEEKS BEFORE</TableCell>
                <TableCell align="right">GROWTH</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.map((st) => (
                <TableRow key={st.style} hover>
                  <TableCell sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>
                    {st.style}
                    {st.newLaunch && (
                      <Chip size="small" label="New launch" sx={{ ml: 1, height: 18, fontSize: "0.65rem", fontWeight: 700, bgcolor: "rgba(234,88,12,0.12)", color: "#c2410c" }} />
                    )}
                  </TableCell>
                  <TableCell>
                    {st.subCategory || "—"}
                    {STAGE_CHIP[stageOf.get(st.subCategory) ?? ""] && (
                      <Chip
                        size="small"
                        label={STAGE_CHIP[stageOf.get(st.subCategory) ?? ""].label}
                        sx={{ ml: 0.75, height: 18, fontSize: "0.62rem", fontWeight: 700, color: "#fff", bgcolor: STAGE_CHIP[stageOf.get(st.subCategory) ?? ""].color }}
                      />
                    )}
                  </TableCell>
                  <TableCell>{st.colour || "—"}</TableCell>
                  <TableCell>{st.fabric || "—"}</TableCell>
                  <TableCell sx={{ color: MUTED, fontSize: "0.8rem" }}>{[st.neck, st.sleeve].filter(Boolean).join(" · ")}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{formatNumber(st.nowUnits)}</TableCell>
                  <TableCell align="right" sx={{ color: MUTED, fontVariantNumeric: "tabular-nums" }}>{formatNumber(st.beforeUnits)}</TableCell>
                  <TableCell align="right">
                    {st.growthPct === null ? (
                      <Typography variant="caption" sx={{ color: MUTED }}>new</Typography>
                    ) : (
                      <Tooltip
                        title={st.expectedUnits
                          ? `(${formatNumber(st.nowUnits)} sold + about ${formatNumber(st.expectedUnits)} expected in the rest of ${running}) vs ${formatNumber(st.beforeUnits)} in the 4 weeks before`
                          : `${formatNumber(st.nowUnits)} sold vs ${formatNumber(st.beforeUnits)} in the 4 weeks before`}
                      >
                        <span><Momentum value={st.growthPct} /></span>
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {rows.length > 0 && (
        <TablePagination
          component="div"
          count={rows.length}
          page={pageNo}
          onPageChange={(_e, p) => setPage(p)}
          rowsPerPage={perPage}
          rowsPerPageOptions={[25, 50, 100, { label: "All", value: -1 }]}
          onRowsPerPageChange={(e) => {
            setPerPage(Number(e.target.value));
            setPage(0);
          }}
          labelRowsPerPage="Designs per page:"
        />
      )}
    </Paper>
  );
}

type View = "analysis" | "suggestion" | "styles";

/** " Oct W2 = real sales up to 9 Oct + its weekly forecast for the other 6 days." */
function runningNote(window?: DesignWindow): string {
  if (!window) return "";
  if (window.forecastDays <= 0) return ` ${window.runningWeek} is complete.`;
  return ` ${window.runningWeek} = real sales up to ${shortDate(window.actualThrough)} + its weekly forecast for the other ${window.forecastDays} day${window.forecastDays === 1 ? "" : "s"}, adjusted to how fast each product is really selling this week.`;
}

function SeasonView({
  season,
  trending,
  lifecycle,
  view,
  window,
}: {
  season: DesignSeason;
  trending?: DesignTrendingNow;
  lifecycle?: DesignLifecycle;
  view: View;
  window?: DesignWindow;
}) {
  const boosts = useMemo(() => season.drivers.filter((d) => d.effectPct > 0).slice(0, 8), [season]);
  const drags = useMemo(() => season.drivers.filter((d) => d.effectPct < 0).slice(-8).reverse(), [season]);

  if (season.note) {
    return <Paper variant="outlined" sx={{ p: 3, color: MUTED }}>{season.note}</Paper>;
  }
  if (view === "styles") {
    return <RecommendedStyles styles={season.recommendedStyles ?? []} products={season.products} window={season.expectedSaleWindow} />;
  }
  if (view === "suggestion") return <ProductCards season={season} />;
  return (
    <Stack spacing={2.5}>
      {/* What is happening: trend stages (with sudden spikes), designs taking off, and what the model learned */}
      {lifecycle && <TrendStatus lifecycle={lifecycle} note={runningNote(window)} spikeWeek={window?.spike?.week} />}
      {trending && <TrendingNow trending={trending} window={window} lifecycle={lifecycle} />}
      {(boosts.length > 0 || drags.length > 0) && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <SectionTitle
            title="What drives sales this season"
            hint="Design features that help sales (green) or hurt sales (red), each on its own."
            topic="drivers"
          />
          <Stack direction="row" sx={{ flexWrap: "wrap", gap: 4 }}>
            <DriverList title="Lifts sales" items={boosts} positive />
            <DriverList title="Holds sales back" items={drags} positive={false} />
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}

/** Design Suggestions tab: analysis, product-level recommendations, and a
 * separate ranked list of existing styles to push. */
export default function DesignSuggestions() {
  const { data, isLoading, isError, refetch } = useDesignSuggestions();
  const [role, setRole] = useState<"running" | "upcoming">("running");
  const [params, setParams] = useSearchParams();
  const requestedView = params.get("view");
  const view: View = requestedView === "suggestion" || requestedView === "styles" ? requestedView : "analysis";
  const season = data?.seasons.find((s) => s.role === role);
  if (data?.rules) RULES.trendStrong = data.rules.trendStrong;

  return (
    <Box>
      <PageHeader title="Design Suggestions" />
      <Tabs
        value={view}
        onChange={(_e, v: View) => setParams((p) => { p.set("view", v); return p; }, { replace: true })}
        sx={{ mb: 2, minHeight: 40, borderBottom: 1, borderColor: "divider", "& .MuiTab-root": { textTransform: "none", fontWeight: 700, minHeight: 40 } }}
      >
        <Tab value="analysis" label="Design Analysis" />
        <Tab value="suggestion" label="Suggestion" />
        <Tab value="styles" label="Styles to push" />
      </Tabs>
      {isLoading ? (
        <LoadingSkeleton />
      ) : isError ? (
        <ErrorState message="Couldn't load design suggestions." onRetry={() => refetch()} />
      ) : data?.building ? (
        <Paper variant="outlined" sx={{ p: 5, textAlign: "center" }}>
          <CircularProgress size={28} sx={{ color: ACCENT }} />
          <Typography variant="body1" sx={{ mt: 2, fontWeight: 700 }}>Preparing suggestions…</Typography>
          <Typography variant="body2" sx={{ color: MUTED }}>This takes about a minute after each data refresh.</Typography>
        </Paper>
      ) : !data?.available ? (
        <ErrorState message={data?.error ?? "Suggestions are not available yet."} onRetry={() => refetch()} />
      ) : (
        <>
          <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ alignItems: { md: "center" }, mb: 2.5 }}>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={role}
              onChange={(_e, v) => v && setRole(v)}
              sx={{ "& .MuiToggleButton-root": { textTransform: "none", fontWeight: 700, px: 2 } }}
            >
              {data.seasons.map((s) => (
                <ToggleButton key={s.role} value={s.role} sx={{ "&.Mui-selected": { bgcolor: ACCENT, color: "#fff", "&:hover": { bgcolor: ACCENT } } }}>
                  {s.role === "running" ? "This season" : "Next season"} · {s.label} ({s.months})
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            {season && (
              <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", rowGap: 1 }}>
                <Chip size="small" label={season.occasion} sx={{ fontWeight: 700, bgcolor: ACCENT_SOFT, color: ACCENT }} />
                {season.reference && <Chip size="small" label={`Compared with ${season.reference}`} variant="outlined" />}
                {data.tierStyles !== undefined && (
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`${data.tiers && data.tiers.length > 0 ? `Tiers ${data.tiers.join(", ")}` : "All designs"} · ${formatNumber(data.tierStyles)} designs`}
                  />
                )}
              </Stack>
            )}
          </Stack>
          {season && <SeasonView key={season.role} season={season} trending={data.trendingNow} lifecycle={data.lifecycle} view={view} window={data.window} />}
        </>
      )}
    </Box>
  );
}
