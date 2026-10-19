import { useState } from "react";
import { Box, Button, Chip, Paper, Stack, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from "@mui/material";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutlined";
import TrendingDownIcon from "@mui/icons-material/TrendingDown";
import DoNotDisturbOnOutlinedIcon from "@mui/icons-material/DoNotDisturbOnOutlined";
import AutoGraphIcon from "@mui/icons-material/AutoGraph";
import AboutButton from "./AboutButton";
import type { DesignLifecycle, DesignLifecycleItem, DesignSpike, DesignStage } from "@/types";

const ACCENT = "#312c5c";
const MUTED = "#64748b";
const SPARK_LINE = "#94a3b8"; // de-emphasised trend line; only the latest point carries the stage colour

// Stage colours = the reserved status palette, always with an icon + label.
const STAGES: { key: DesignStage; title: string; hint: string; color: string; Icon: typeof TrendingUpIcon }[] = [
  { key: "trending", title: "Trending", hint: "Getting popular fast", color: "#0ca30c", Icon: TrendingUpIcon },
  { key: "steady", title: "In trend", hint: "Selling steadily", color: ACCENT, Icon: CheckCircleOutlineIcon },
  { key: "fading", title: "Starting to fade", hint: "Share of sales starting to drop", color: "#fab219", Icon: TrendingDownIcon },
  { key: "out", title: "Out of trend", hint: "Far below its best, or no longer selling", color: "#d03b3b", Icon: DoNotDisturbOnOutlinedIcon },
];

const FACTORS = [
  { key: "subCategory", label: "Sub Category" },
  { key: "colour", label: "Colour" },
  { key: "fabric", label: "Fabric" },
  { key: "neck", label: "Neck" },
] as const;

const SHOW = 8;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const shortDate = (iso: string) => {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
};

const SPIKE = "#ea580c";

/** "⚡ Spike ×1.8" - its share of one report week's sales jumped vs the 4 weeks before. */
function SpikeBadge({ spike, week }: { spike: DesignSpike; week: string }) {
  const tip = spike.new
    ? `Sudden spike - new: ${spike.lastWeekUnits.toLocaleString("en-IN")} pieces in ${week} (${spike.lastWeekShare}% of sales), almost nothing in the 4 weeks before`
    : `Sudden spike: ${spike.lastWeekShare}% of sales in ${week} (${spike.lastWeekUnits.toLocaleString("en-IN")} pieces) vs ${spike.usualShare}% in the 4 weeks before`;
  return (
    <Tooltip title={tip}>
      <Box component="span" sx={{ px: 0.6, borderRadius: 1, fontSize: "0.66rem", fontWeight: 800, whiteSpace: "nowrap", color: SPIKE, bgcolor: "rgba(234,88,12,0.10)", flexShrink: 0 }}>
        ⚡ {spike.new ? "New" : `×${(spike.ratio ?? 0).toFixed(1)}`}
      </Box>
    </Tooltip>
  );
}

/** 6-point share history: thin muted line, latest point in the stage colour. */
function Sparkline({ values, color }: { values: number[]; color: string }) {
  const w = 64;
  const h = 20;
  const pad = 3;
  const max = Math.max(...values, 0.0001);
  const pts = values.map((v, i) => [pad + (i * (w - 2 * pad)) / Math.max(1, values.length - 1), h - pad - (v / max) * (h - 2 * pad)]);
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" style={{ flexShrink: 0 }}>
      <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={SPARK_LINE} strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={3.25} fill={color} stroke="#fff" strokeWidth={1.25} />
    </svg>
  );
}

function ItemRow({ item, color, blocks, spikeWeek }: { item: DesignLifecycleItem; color: string; blocks: DesignLifecycle["blocks"]; spikeWeek: string }) {
  const tip = (
    <Box>
      <Typography variant="caption" sx={{ fontWeight: 700, display: "block" }}>Share of sales, 4 report weeks at a time</Typography>
      {item.share.map((v, i) => (
        <Typography key={i} variant="caption" sx={{ display: "block" }}>
          {blocks[i].weeks ?? `${shortDate(blocks[i].from)} – ${shortDate(blocks[i].to)}`}: {v.toFixed(1)}%{i === item.peakBlock ? "  (best)" : ""}
        </Typography>
      ))}
      <Typography variant="caption" sx={{ display: "block", mt: 0.5 }}>
        {item.realNowUnits !== undefined
          ? `${item.realNowUnits.toLocaleString("en-IN")} pieces sold so far${item.expectedUnits ? ` + ~${item.expectedUnits.toLocaleString("en-IN")} expected this week` : ""}`
          : `${item.nowUnits.toLocaleString("en-IN")} pieces in the last 4 weeks`} · {item.activeStyles} designs selling
      </Typography>
    </Box>
  );
  return (
    <Tooltip title={tip} placement="left">
      <Box sx={{ py: 0.6, borderTop: "1px solid", borderColor: "divider" }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography variant="body2" sx={{ fontWeight: 700, flexGrow: 1, minWidth: 0 }} noWrap>{item.value}</Typography>
          {item.spike && <SpikeBadge spike={item.spike} week={spikeWeek} />}
          <Sparkline values={item.share} color={color} />
          <Typography variant="caption" sx={{ width: 44, textAlign: "right", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
            {item.change === null ? "" : `${item.change > 0 ? "+" : ""}${item.change.toFixed(0)}%`}
          </Typography>
        </Stack>
        <Typography variant="caption" sx={{ color: MUTED, display: "block", mt: 0.25 }}>
          {item.nowShare.toFixed(1)}% of sales now
          {item.fromPeakPct !== null && item.fromPeakPct < -5 ? ` · ${Math.abs(item.fromPeakPct).toFixed(0)}% below its best` : ""}
        </Typography>
      </Box>
    </Tooltip>
  );
}

function StageColumn({ stage, items, blocks, spikeWeek }: { stage: (typeof STAGES)[number]; items: DesignLifecycleItem[]; blocks: DesignLifecycle["blocks"]; spikeWeek: string }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, SHOW);
  const { Icon } = stage;
  return (
    <Paper variant="outlined" sx={{ p: 1.5, flex: "1 1 260px", minWidth: 240, borderTop: 3, borderTopColor: stage.color }}>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
        <Icon fontSize="small" sx={{ color: stage.color }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 800, flexGrow: 1 }}>{stage.title}</Typography>
        <Chip size="small" label={items.length} sx={{ height: 18, fontSize: "0.68rem", fontWeight: 700 }} />
      </Stack>
      <Typography variant="caption" sx={{ color: MUTED, display: "block", mb: 0.75 }}>{stage.hint}</Typography>
      {items.length === 0 && <Typography variant="body2" sx={{ color: MUTED, py: 1 }}>None right now.</Typography>}
      {shown.map((it) => (
        <ItemRow key={it.value} item={it} color={stage.color} blocks={blocks} spikeWeek={spikeWeek} />
      ))}
      {items.length > SHOW && (
        <Button size="small" onClick={() => setAll((v) => !v)} sx={{ mt: 0.5, textTransform: "none", fontWeight: 700 }}>
          {all ? "Show less" : `Show all ${items.length}`}
        </Button>
      )}
    </Paper>
  );
}

/** Trend status of every Category / Sub Category / Colour / Fabric - which is
 * trending, which is starting to fade and which is already out of trend. The
 * same stages feed the model and the scores on the rest of the page. */
export default function TrendStatus({ lifecycle, note = "", spikeWeek = "this week" }: { lifecycle: DesignLifecycle; note?: string; spikeWeek?: string }) {
  const [factor, setFactor] = useState<(typeof FACTORS)[number]["key"]>("subCategory");
  const items = lifecycle[factor] ?? [];
  const spiking = items.filter((it) => it.spike).length;
  const first = lifecycle.blocks[0];
  const last = lifecycle.blocks[lifecycle.blocks.length - 1];
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} sx={{ alignItems: { md: "center" }, mb: 1.5 }}>
        <Box sx={{ flexGrow: 1 }}>
          <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
            <AutoGraphIcon fontSize="small" sx={{ color: ACCENT }} />
            <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Trend status</Typography>
            <AboutButton topic="lifecycle" />
          </Stack>
          <Typography variant="body2" sx={{ color: MUTED }}>
            What is trending, starting to fade or already out of trend ({first && (first.weeks?.split(" – ")[0] ?? shortDate(first.from))} – {last && (last.weeks?.split(" – ")[1] ?? shortDate(last.to))}).
            The line shows its share of sales over the last 6 months, 4 report weeks per point.{note}
            {spiking > 0 && ` ⚡ = sudden spike in ${spikeWeek} (${spiking} here).`}
          </Typography>
        </Box>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={factor}
          onChange={(_e, v) => v && setFactor(v)}
          sx={{ "& .MuiToggleButton-root": { textTransform: "none", fontWeight: 700, px: 1.5 } }}
        >
          {FACTORS.map((f) => (
            <ToggleButton key={f.key} value={f.key} sx={{ "&.Mui-selected": { bgcolor: ACCENT, color: "#fff", "&:hover": { bgcolor: ACCENT } } }}>
              {f.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>
      <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.5 }}>
        {STAGES.map((st) => (
          <StageColumn key={`${factor}-${st.key}`} stage={st} items={items.filter((it) => it.stage === st.key)} blocks={lifecycle.blocks} spikeWeek={spikeWeek} />
        ))}
      </Stack>
    </Paper>
  );
}
