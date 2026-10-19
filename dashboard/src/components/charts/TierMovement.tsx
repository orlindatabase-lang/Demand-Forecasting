import { Fragment, useMemo, useState } from "react";
import {
  Alert,
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
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import { useTierMovement } from "@/hooks/useDashboardData";
import { LoadingSkeleton, ErrorState } from "@/components/common/StateViews";
import { formatNumber } from "@/utils/format";
import type { TierMovementStyle, TierStay } from "@/types";

const ACCENT = "#312c5c";
const MUTED = "#64748b";
const HEADER_BG = "#eef0f5";
const SHOW = 50;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// One colour per tier, always shown with the tier's name (never colour alone).
const TIER_COLOR: Record<string, string> = {
  T0: "#6d5bd0",
  T1: "#0f9d8f",
  T2: "#d97706",
  T3: "#2563eb",
  T11: "#94a3b8",
  T12: "#64748b",
};
const tierColor = (t: string) => TIER_COLOR[t] ?? "#a1a1aa";

const shortDate = (iso: string | null) => {
  if (!iso) return "";
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
};
const daysText = (d: number | null) => (d === null ? "—" : `${formatNumber(d)} day${d === 1 ? "" : "s"}`);

/** A style's tier journey as one bar: a block per tier stay, width by days. */
function JourneyBar({ journey }: { journey: TierStay[] }) {
  const tracked = journey.filter((j) => j.days !== null);
  const total = Math.max(1, tracked.reduce((a, j) => a + (j.days ?? 0), 0));
  return (
    <Stack direction="row" sx={{ width: "100%", minWidth: 260, height: 22, borderRadius: 1, overflow: "hidden", gap: "2px" }}>
      {journey.map((j, i) => {
        const tip = j.untracked
          ? `${j.tier} now - no entry in the tier history yet, so the start date is unknown`
          : `${j.tier}: ${shortDate(j.from)} – ${j.to ? shortDate(j.to) : "now"} · ${daysText(j.days)}`;
        const grow = j.untracked ? 0 : Math.max(0.04, (j.days ?? 0) / total);
        return (
          <Tooltip key={i} title={tip}>
            <Box
              sx={{
                flexGrow: grow, flexBasis: j.untracked ? 56 : 0, flexShrink: 0, minWidth: 34,
                bgcolor: tierColor(j.tier), color: "#fff", fontSize: "0.68rem", fontWeight: 700,
                display: "flex", alignItems: "center", justifyContent: "center", px: 0.5, whiteSpace: "nowrap", overflow: "hidden",
                ...(j.untracked ? { backgroundImage: "repeating-linear-gradient(45deg, rgba(255,255,255,0.25) 0 4px, transparent 4px 8px)" } : {}),
              }}
            >
              {j.tier}
              {!j.untracked && j.days !== null && (j.days ?? 0) / total > 0.12 ? ` · ${j.days}d` : ""}
            </Box>
          </Tooltip>
        );
      })}
    </Stack>
  );
}

function StyleRows({ styles, indent = 0 }: { styles: TierMovementStyle[]; indent?: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? styles : styles.slice(0, SHOW);
  return (
    <>
      {shown.map((st) => (
        <TableRow key={st.style} hover>
          <TableCell sx={{ pl: 2 + indent, whiteSpace: "nowrap" }}>
            <Typography variant="body2" sx={{ fontWeight: 700 }}>{st.style}</Typography>
            <Typography variant="caption" sx={{ color: MUTED }}>{st.subCategory || "—"}</Typography>
          </TableCell>
          <TableCell>
            <Chip size="small" label={st.tier} sx={{ height: 20, fontWeight: 700, bgcolor: tierColor(st.tier), color: "#fff" }} />
          </TableCell>
          <TableCell sx={{ width: "55%" }}><JourneyBar journey={st.journey} /></TableCell>
          <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
            {st.daysInTier === null ? <Typography variant="caption" sx={{ color: MUTED }}>not tracked</Typography> : daysText(st.daysInTier)}
          </TableCell>
        </TableRow>
      ))}
      {styles.length > SHOW && (
        <TableRow>
          <TableCell colSpan={4} sx={{ pl: 2 + indent }}>
            <Button size="small" onClick={() => setAll((v) => !v)} sx={{ textTransform: "none", fontWeight: 700 }}>
              {all ? "Show fewer" : `Show all ${formatNumber(styles.length)} styles`}
            </Button>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

const styleHead = (
  <TableRow sx={{ "& th": { fontWeight: 800, fontSize: "0.72rem", color: MUTED, bgcolor: "#f8f8fb" } }}>
    <TableCell>STYLE</TableCell>
    <TableCell>TIER NOW</TableCell>
    <TableCell>TIER JOURNEY (SINCE TRACKING STARTED)</TableCell>
    <TableCell align="right">DAYS IN CURRENT TIER</TableCell>
  </TableRow>
);

/** Tiers Spike Rate > Tier movement (2026-10-13, user-requested): how long
 * styles stay in each catalog tier and where they move, from the Cloud SQL
 * tier-change log. */
export default function TierMovement() {
  const { data, isLoading, isError, refetch } = useTierMovement(true);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [search, setSearch] = useState("");

  const stylesByTier = useMemo(() => {
    const out = new Map<string, TierMovementStyle[]>();
    for (const st of data?.styles ?? []) {
      if (!out.has(st.tier)) out.set(st.tier, []);
      out.get(st.tier)!.push(st);
    }
    for (const list of out.values()) list.sort((a, b) => (b.daysInTier ?? -1) - (a.daysInTier ?? -1) || a.style.localeCompare(b.style));
    return out;
  }, [data]);
  const q = search.trim().toLowerCase();
  const found = useMemo(
    () => (q ? (data?.styles ?? []).filter((s) => s.style.toLowerCase().includes(q) || s.subCategory.toLowerCase().includes(q)).slice(0, 100) : []),
    [data, q],
  );
  const tiers = data?.tiers ?? [];
  const tierNames = tiers.map((t) => t.tier);
  const moveCount = useMemo(() => {
    const m = new Map<string, number>();
    for (const mv of data?.moves ?? []) m.set(`${mv.from}>${mv.to}`, mv.styles);
    return m;
  }, [data]);
  const maxMove = Math.max(1, ...(data?.moves ?? []).map((m) => m.styles));
  const untracked = tiers.filter((t) => t.stylesNow > t.stylesTracked);

  if (isLoading) return <LoadingSkeleton />;
  if (isError || !data) return <ErrorState message="Couldn't load the tier history." onRetry={() => refetch()} />;
  if (!data.available) return <ErrorState message="The tier history is not available right now." onRetry={() => refetch()} />;

  return (
    <Stack spacing={2.5}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} sx={{ alignItems: { md: "center" } }}>
        <TextField
          size="small"
          placeholder="Search style or sub category…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> } }}
          sx={{ minWidth: 300 }}
        />
        <Typography variant="body2" sx={{ color: MUTED }}>
          Tier history from <b>{shortDate(data.trackingFrom)} {data.trackingFrom.slice(0, 4)}</b> (start of tracking). Styles already in a tier
          on that day count their days from then.
        </Typography>
      </Stack>
      {untracked.length > 0 && (
        <Alert severity="warning" variant="outlined" sx={{ py: 0 }}>
          {untracked.map((t) => `${t.tier}: ${formatNumber(t.stylesNow - t.stylesTracked)} of ${formatNumber(t.stylesNow)} styles`).join(" · ")}
          {" "}have no entry in the tier history yet, so their days in the tier are unknown ("not tracked"). Ask the catalog team to log tier changes for new styles.
        </Alert>
      )}

      {q && (
        <Paper variant="outlined">
          <Typography variant="subtitle2" sx={{ fontWeight: 800, px: 2, pt: 1.5 }}>
            {found.length === 0 ? "No style matches" : `${formatNumber(found.length)} matching style${found.length === 1 ? "" : "s"}`}
          </Typography>
          {found.length > 0 && (
            <TableContainer>
              <Table size="small">
                <TableHead>{styleHead}</TableHead>
                <TableBody><StyleRows styles={found} /></TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>
      )}

      <Paper variant="outlined">
        <Box sx={{ px: 2, pt: 1.5 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>How long styles stay in each tier</Typography>
          <Typography variant="body2" sx={{ color: MUTED }}>Click a tier to see its styles and their tier journeys, longest in the tier first.</Typography>
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ "& th": { fontWeight: 800, fontSize: "0.72rem", bgcolor: HEADER_BG, whiteSpace: "nowrap" } }}>
                <TableCell>TIER</TableCell>
                <TableCell align="right">STYLES NOW</TableCell>
                <TableCell align="right">MOVED IN</TableCell>
                <TableCell align="right">MOVED OUT</TableCell>
                <TableCell align="right">
                  <Tooltip title="Styles that left this tier: half stayed fewer days than this, half more"><span>MEDIAN DAYS BEFORE MOVING</span></Tooltip>
                </TableCell>
                <TableCell align="right">AVG DAYS BEFORE MOVING</TableCell>
                <TableCell align="right">
                  <Tooltip title="Styles still in this tier: median days so far"><span>MEDIAN DAYS SO FAR</span></Tooltip>
                </TableCell>
                <TableCell>MOSTLY MOVES TO</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {tiers.map((t) => {
                const isOpen = open.has(t.tier);
                return (
                  <Fragment key={t.tier}>
                    <TableRow
                      hover
                      onClick={() => setOpen((prev) => {
                        const next = new Set(prev);
                        if (next.has(t.tier)) next.delete(t.tier);
                        else next.add(t.tier);
                        return next;
                      })}
                      sx={{ cursor: "pointer", "& td": { fontVariantNumeric: "tabular-nums" } }}
                    >
                      <TableCell>
                        <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
                          {isOpen ? <KeyboardArrowDownIcon fontSize="small" sx={{ color: ACCENT }} /> : <KeyboardArrowRightIcon fontSize="small" sx={{ color: MUTED }} />}
                          <Box sx={{ width: 10, height: 10, borderRadius: "2px", bgcolor: tierColor(t.tier) }} />
                          <Typography variant="body2" sx={{ fontWeight: 800 }}>{t.tier}</Typography>
                        </Stack>
                      </TableCell>
                      <TableCell align="right">{formatNumber(t.stylesNow)}</TableCell>
                      <TableCell align="right">{formatNumber(t.movedIn)}</TableCell>
                      <TableCell align="right">{formatNumber(t.movedOut)}</TableCell>
                      <TableCell align="right" sx={{ fontWeight: 800 }}>{daysText(t.medianDaysBeforeMoving)}</TableCell>
                      <TableCell align="right">{daysText(t.avgDaysBeforeMoving)}</TableCell>
                      <TableCell align="right">{daysText(t.medianDaysSoFar)}</TableCell>
                      <TableCell>
                        {t.movesTo.length === 0 ? "—" : t.movesTo.map((m) => `${m.tier} (${formatNumber(m.styles)})`).join(", ")}
                      </TableCell>
                    </TableRow>
                    {isOpen && (
                      <TableRow>
                        <TableCell colSpan={8} sx={{ p: 0, bgcolor: "#fcfcfe" }}>
                          <Table size="small">
                            <TableHead>{styleHead}</TableHead>
                            <TableBody><StyleRows styles={stylesByTier.get(t.tier) ?? []} indent={3} /></TableBody>
                          </Table>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Where styles move</Typography>
        <Typography variant="body2" sx={{ color: MUTED, mb: 1.5 }}>
          Number of styles that moved from the tier on the left to the tier on top. Darker = more styles.
        </Typography>
        <TableContainer>
          <Table size="small" sx={{ width: "auto", "& td, & th": { textAlign: "center", minWidth: 64 } }}>
            <TableHead>
              <TableRow sx={{ "& th": { fontWeight: 800, fontSize: "0.72rem", bgcolor: HEADER_BG } }}>
                <TableCell sx={{ textAlign: "left !important" }}>FROM ↓ / TO →</TableCell>
                {tierNames.map((t) => <TableCell key={t}>{t}</TableCell>)}
              </TableRow>
            </TableHead>
            <TableBody>
              {tierNames.map((from) => (
                <TableRow key={from}>
                  <TableCell sx={{ fontWeight: 800, textAlign: "left !important" }}>{from}</TableCell>
                  {tierNames.map((to) => {
                    const n = moveCount.get(`${from}>${to}`) ?? 0;
                    const strength = n / maxMove;
                    return (
                      <TableCell
                        key={to}
                        sx={{
                          fontVariantNumeric: "tabular-nums", fontWeight: n ? 700 : 400,
                          bgcolor: from === to ? "#f4f4f7" : n ? `rgba(49,44,92,${0.08 + strength * 0.72})` : "transparent",
                          color: strength > 0.45 ? "#fff" : n ? ACCENT : "#cbd5e1",
                        }}
                      >
                        {from === to ? "" : n || "·"}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Stack>
  );
}
