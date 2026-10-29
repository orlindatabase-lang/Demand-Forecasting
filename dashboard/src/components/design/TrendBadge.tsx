import { Box, Tooltip } from "@mui/material";
import type { DesignStage } from "@/types";
import { COLORS, REPORT } from "@/theme/colors";

export const UP = REPORT.good;
export const DOWN = REPORT.bad;
const MUTED = COLORS.textSecondary;

export const NOW_TIP = "Now: share of the last 4 weeks' sales vs the 4 weeks before";
// Rising / falling threshold - the API's shared rule (data.rules.trendStrong),
// so a badge always agrees with the lists it appears next to.
export const RULES = { trendStrong: 30 };

// Trend stage (last 6 months, see the API's _lifecycle) - status colours, always with a symbol + word.
export const STAGE_META: Record<DesignStage, { label: string; color: string; bg: string; tip: string }> = {
  trending: { label: "▲ Trending", color: UP, bg: "rgba(21,128,61,0.10)", tip: "Trending: its part of sales jumped in the last 4 weeks" },
  steady: { label: "● In trend", color: MUTED, bg: "rgba(100,116,139,0.10)", tip: "In trend: selling steadily" },
  fading: { label: "▼ Fading", color: "#b45309", bg: "rgba(250,178,25,0.16)", tip: "Starting to fade: its part of sales dropped in the last 4 weeks" },
  out: { label: "⊘ Out of trend", color: DOWN, bg: "rgba(220,38,38,0.08)", tip: "Out of trend: far below its best of the last 6 months" },
};

const pill = { px: 0.75, py: 0.1, borderRadius: 1, fontSize: "0.68rem", fontWeight: 800, whiteSpace: "nowrap" } as const;

/** Trend label: the trend stage when known (Trending / In trend / Fading /
 * Out of trend), else Rising / Steady / Falling from the 4-week share trend. */
export default function TrendBadge({ value, tip = NOW_TIP, stage }: { value: number | null | undefined; tip?: string; stage?: DesignStage | "none" | null }) {
  const pct = value === null || value === undefined ? "" : `${tip}: ${value >= 0 ? "+" : ""}${value.toFixed(0)}%`;
  if (stage && stage !== "none") {
    const s = STAGE_META[stage];
    return (
      <Tooltip title={[s.tip, pct].filter(Boolean).join(" · ")}>
        <Box component="span" sx={{ ...pill, color: s.color, bgcolor: s.bg }}>{s.label}</Box>
      </Tooltip>
    );
  }
  if (value === null || value === undefined) return null;
  const kind = value >= RULES.trendStrong ? "up" : value <= -RULES.trendStrong ? "down" : "flat";
  const meta = {
    up: { label: `▲ Rising +${value.toFixed(0)}%`, color: UP, bg: "rgba(21,128,61,0.10)" },
    down: { label: `▼ Falling ${Math.abs(value).toFixed(0)}%`, color: DOWN, bg: "rgba(220,38,38,0.08)" },
    flat: { label: "● Steady", color: MUTED, bg: "rgba(100,116,139,0.10)" },
  }[kind];
  return (
    <Tooltip title={pct}>
      <Box component="span" sx={{ ...pill, color: meta.color, bgcolor: meta.bg }}>{meta.label}</Box>
    </Tooltip>
  );
}
