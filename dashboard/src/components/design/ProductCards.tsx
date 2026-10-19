import { useState } from "react";
import { Box, Button, Chip, Paper, Stack, Tooltip, Typography } from "@mui/material";
import LightbulbOutlinedIcon from "@mui/icons-material/LightbulbOutlined";
import AboutButton from "./AboutButton";
import TrendBadge, { DOWN, UP } from "./TrendBadge";
import type { AboutContent } from "./aboutContent";
import type { DesignLevel, DesignProductCard, DesignSeason, DesignStage } from "@/types";

const ACCENT = "#312c5c";
const ACCENT_SOFT = "rgba(49,44,92,0.08)";
const MUTED = "#64748b";
const SHOW = 8;

const ATTR: Record<string, string> = { colour: "colour", fabric: "fabric" };
const DETAIL_ORDER = ["embroidery", "neck", "sleeve", "topType"] as const;

const LEVEL: Record<DesignLevel, { label: string; bars: number; color: string }> = {
  strong: { label: "Strong", bars: 3, color: UP },
  good: { label: "Good", bars: 2, color: ACCENT },
  trySmall: { label: "Try small", bars: 1, color: MUTED },
};

/** Strength as 3 small bars + a word (never colour alone). */
function Level({ level, suffix }: { level: DesignLevel; suffix?: string }) {
  const l = LEVEL[level];
  return (
    <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", whiteSpace: "nowrap" }}>
      <Stack direction="row" spacing={0.25} aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <Box key={i} sx={{ width: 4, height: 10, borderRadius: 0.5, bgcolor: i < l.bars ? l.color : "rgba(100,116,139,0.25)" }} />
        ))}
      </Stack>
      <Typography variant="caption" sx={{ fontWeight: 800, color: l.color }}>{l.label}{suffix}</Typography>
    </Stack>
  );
}

const STAGE_MARK: Record<DesignStage, { mark: string; color: string; word: string }> = {
  trending: { mark: "▲", color: UP, word: "Trending" },
  steady: { mark: "●", color: MUTED, word: "In trend" },
  fading: { mark: "▼", color: "#b45309", word: "Fading" },
  out: { mark: "⊘", color: DOWN, word: "Out of trend" },
};

function ValueChip({ value, stage, more }: { value: string; stage?: DesignStage | "none" | null; more?: boolean }) {
  const s = stage && stage !== "none" ? STAGE_MARK[stage] : null;
  return (
    <Tooltip title={[s?.word, more ? "Make more: sells well, few designs" : ""].filter(Boolean).join(" · ")}>
      <Box
        component="span"
        sx={{
          display: "inline-flex", alignItems: "center", gap: 0.5, px: 1, py: 0.25, borderRadius: 1, fontSize: "0.8rem", fontWeight: 700,
          bgcolor: ACCENT_SOFT, border: more ? `1px solid ${UP}` : "1px solid transparent",
        }}
      >
        {s && <Box component="span" sx={{ color: s.color, fontSize: "0.7rem" }}>{s.mark}</Box>}
        {value}
      </Box>
    </Tooltip>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Stack direction="row" spacing={1.5} sx={{ alignItems: "baseline", py: 0.5 }}>
      <Typography variant="caption" sx={{ color: MUTED, width: 64, flexShrink: 0, fontWeight: 700 }}>{label}</Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}

function Part({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider", pt: 1, mt: 1 }}>
      <Typography variant="caption" sx={{ fontWeight: 800, color: ACCENT, letterSpacing: 0.4 }}>{title}</Typography>
      {children}
    </Box>
  );
}

const CUT_REASON: Record<string, string> = {
  out: "out of trend",
  fading: "starting to fade",
  declining: "selling less now",
  saturated: "too many designs, each sells little",
};

function seasonText(c: DesignProductCard, reference: string): string {
  if (!reference) return "";
  if (!c.lastSeason) return `new — not sold in ${reference}`;
  if (c.lift >= 1.15) return `was strong in ${reference}`;
  if (c.lift <= 0.85) return `was weak in ${reference}`;
  return `normal in ${reference}`;
}

const list = (xs: string[]) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

const STAGE_TEXT: Record<string, string> = {
  trending: "▲ Trending: its part of sales jumped (30% or more) in the last 4 weeks.",
  steady: "● In trend: selling steadily — its part of sales has not jumped or dropped much in the last 4 weeks, and it is not far below its best.",
};
const LEVEL_TEXT: Record<DesignLevel, string> = {
  strong: "a safe choice (score 70 or more out of 100)",
  good: "a fair choice (score 50–69 out of 100)",
  trySmall: "weaker evidence (score under 50 out of 100) — try a small quantity first",
};
const DETAIL_TEXT: Record<string, string> = { embroidery: "embroidery", neck: "neck", sleeve: "sleeve", topType: "top type" };

/** The card's own "About": what each of ITS numbers means, in plain words. */
function cardAbout(card: DesignProductCard, rank: number, reference: string, seasonLabel: string): AboutContent {
  const sc = card.subCategory;
  const moreSet = new Set(card.makeMore.map((m) => m.value));
  const stageWord = (st?: DesignStage | "none" | null) => (st === "trending" ? "▲ Trending" : st === "steady" ? "● In trend" : "");
  const colours = card.use.colour.map((u) => u.value);
  const fabrics = card.use.fabric.map((u) => u.value);
  const detailKeys = DETAIL_ORDER.filter((k) => card.use.details[k] && card.use.details[k] !== "Unknown");
  const sections: AboutContent["sections"] = [
    {
      heading: "Top line",
      points: [
        `${rank} = it is the number ${rank} product to focus on for ${seasonLabel}.`,
        card.category ? `Category: ${card.category} — ${sc} is one of its product types (sub categories).` : "",
        STAGE_TEXT[card.stage ?? ""] ?? "",
        `${LEVEL[card.level].label} = ${LEVEL_TEXT[card.level]}.`,
      ].filter(Boolean),
    },
    {
      heading: "Grey line (real sales, no estimates)",
      points: [
        `${card.nowShare}% of all sales: of all pieces sold in the last 4 weeks, ${card.nowShare}% were ${sc}.`,
        `${card.activeStyles} designs selling: that many of its designs sold at least one piece in the last 4 weeks.`,
        card.vsAverage !== null
          ? `Each sells ${card.vsAverage.toFixed(1)}× the average design: one ${sc} design sells ${card.vsAverage >= 1 ? `about ${card.vsAverage.toFixed(1)} times as much as` : "less than"} an average design of any product.`
          : "",
        reference
          ? !card.lastSeason
            ? `New — not sold in ${reference}, so last year cannot tell us how this season suits it.`
            : card.lift >= 1.15
              ? `Was strong in ${reference}: last year in this season it sold ${card.lift.toFixed(1)}× its usual share — this season suits it.`
              : card.lift <= 0.85
                ? `Was weak in ${reference}: last year in this season it sold only ${card.lift.toFixed(1)}× its usual share — this season suits it less.`
                : `Normal in ${reference}: last year in this season it sold about its usual share — the season neither helps nor hurts it.`
          : "",
      ].filter(Boolean),
    },
  ];
  const use: string[] = [];
  if (colours.length)
    use.push(`Colours: ${list(card.use.colour.map((u) => `${u.value}${stageWord(u.stage) ? ` (${stageWord(u.stage)})` : ""}`))} sell best inside ${sc} right now.`);
  if (moreSet.size) use.push(`A green outline (${list([...moreSet])}) means "make more" — see below.`);
  if (fabrics.length) use.push(`Fabrics: ${list(fabrics)}.`);
  if (detailKeys.length)
    use.push(`Details: ${list(detailKeys.map((k) => `${card.use.details[k]} (${DETAIL_TEXT[k]})`))} sell best in ${sc}.`);
  if (!colours.length && !fabrics.length) use.push(`Too few ${sc} designs are selling to compare colours and fabrics yet.`);
  sections.push({ heading: "Use these (what to design with, inside this product)", points: use });
  if (card.mixes.length) {
    const pts = card.mixes.map((m) => {
      const pct = Math.round(m.vsAveragePct);
      const lvl = m.level === "strong" ? `Strong — about ${pct}% better than an average ${sc} mix`
        : m.level === "good" ? `Good — close to an average ${sc} mix (${pct >= 0 ? "+" : ""}${pct}%)`
          : `Try small — below an average ${sc} mix (${pct}%)`;
      return `${m.colour} + ${m.fabric}: ${m.tried ? "made before (safer)" : "a new idea — never made, so start small"}. ${lvl}.`;
    });
    pts.unshift("The best colour + fabric pairs, made only from the colours and fabrics listed above. The model compares them with an average mix of this product; it is good at telling which is better, not at exact pieces.");
    const fab = [...new Set(card.mixes.map((m) => m.fabric))];
    if (card.mixes.length > 1 && fab.length === 1)
      pts.push(`All of them use ${fab[0]} because ${fab[0]} sells far more than the other fabrics in ${sc}, so its pairs come out on top.`);
    sections.push({ heading: "Best mixes", points: pts });
  }
  if (card.makeMore.length || card.cutBack.length) {
    const why: Record<string, (n: number) => string> = {
      saturated: (n) => `already ${n} designs, but each sells little — more designs would mostly split the same sales`,
      out: (n) => `out of trend (far below its best of the last 6 months), still used by ${n} designs — don't add new ones`,
      fading: (n) => `starting to fade (its part of sales dropped in the last 4 weeks), ${n} designs`,
      declining: (n) => `sold well before but is selling less now, ${n} designs`,
    };
    sections.push({
      heading: "Make more / Cut back",
      points: [
        ...card.makeMore.map((m) => `＋ ${m.value}: each ${m.value} ${sc} design sells ${m.times.toFixed(1)}× the average ${sc} design, but there are only ${m.styles} of them — room for more.`),
        ...card.cutBack.map((c) => `－ ${c.value}: ${(why[c.reason] ?? why.declining)(c.styles)}.`),
      ],
    });
  }
  const make = list(card.makeMore.map((m) => m.value));
  const stop = list(card.cutBack.map((c) => c.value));
  const how = [
    fabrics[0] ? `mainly in ${fabrics[0]}` : "",
    detailKeys.length ? `with ${list(detailKeys.slice(0, 3).map((k) => card.use.details[k] as string))}` : "",
  ].filter(Boolean).join(" ");
  const first = make ? `make more ${make} designs` : colours.length ? `use ${list(colours.slice(0, 3))}` : "keep making it";
  const sentence = [first, how].filter(Boolean).join(", ") + (stop ? `, and stop adding ${stop} designs` : "");
  sections.push({ heading: "In one sentence", points: [`For ${sc}${card.category ? ` (${card.category})` : ""} in ${seasonLabel}: ${sentence}.`] });
  return { title: `About this card: ${sc}`, sections };
}

function ProductCard({ card, rank, reference, seasonLabel }: { card: DesignProductCard; rank: number; reference: string; seasonLabel: string }) {
  const more = new Set(card.makeMore.map((m) => `${m.attribute}:${m.value}`));
  const details = DETAIL_ORDER.map((k) => card.use.details[k]).filter((v) => v && v !== "Unknown");
  const facts = [
    `${card.nowShare}% of all sales`,
    `${card.activeStyles} designs selling`,
    card.vsAverage !== null ? `each sells ${card.vsAverage.toFixed(1)}× the average design` : "",
    seasonText(card, reference),
  ].filter(Boolean);
  return (
    <Paper variant="outlined" sx={{ p: 2, flex: "1 1 460px", minWidth: 300, borderLeft: 4, borderLeftColor: ACCENT }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="body2" sx={{ color: MUTED, fontWeight: 800, width: 18 }}>{rank}</Typography>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          {card.category && (
            <Typography variant="caption" sx={{ display: "block", color: MUTED, fontWeight: 700, letterSpacing: 0.4, lineHeight: 1.2 }}>
              {card.category.toUpperCase()}
            </Typography>
          )}
          <Typography variant="subtitle1" sx={{ fontWeight: 800, lineHeight: 1.3 }} noWrap>{card.subCategory}</Typography>
        </Box>
        <TrendBadge value={card.trend} stage={card.stage} />
        <Level level={card.level} />
        <AboutButton content={cardAbout(card, rank, reference, seasonLabel)} />
      </Stack>
      <Typography variant="caption" sx={{ color: MUTED, display: "block", ml: "26px" }}>{facts.join(" · ")}</Typography>

      <Part title="USE THESE">
        {card.use.colour.length > 0 && (
          <Row label="Colours">
            {card.use.colour.map((u) => <ValueChip key={u.value} value={u.value} stage={u.stage} more={more.has(`colour:${u.value}`)} />)}
          </Row>
        )}
        {card.use.fabric.length > 0 && (
          <Row label="Fabrics">
            {card.use.fabric.map((u) => <ValueChip key={u.value} value={u.value} stage={u.stage} more={more.has(`fabric:${u.value}`)} />)}
          </Row>
        )}
        {details.length > 0 && (
          <Row label="Details">
            <Typography variant="body2">{details.join(" · ")}</Typography>
          </Row>
        )}
        {card.use.colour.length === 0 && card.use.fabric.length === 0 && (
          <Typography variant="body2" sx={{ color: MUTED, py: 0.5 }}>Too few designs selling to compare colours and fabrics yet.</Typography>
        )}
      </Part>

      {card.mixes.length > 0 && (
        <Part title="BEST MIXES">
          {card.mixes.map((m) => (
            <Stack key={`${m.colour}-${m.fabric}`} direction="row" spacing={1} sx={{ alignItems: "center", py: 0.4 }}>
              <Typography variant="body2" sx={{ fontWeight: 700, flexGrow: 1 }}>{m.colour} + {m.fabric}</Typography>
              <Chip
                size="small"
                label={m.tried ? "Made before" : "New idea"}
                sx={{ height: 20, fontSize: "0.68rem", fontWeight: 700, ...(m.tried ? { bgcolor: ACCENT_SOFT, color: ACCENT } : { bgcolor: "rgba(21,128,61,0.12)", color: UP }) }}
              />
              <Tooltip title={`${m.vsAveragePct >= 0 ? "+" : ""}${m.vsAveragePct.toFixed(0)}% vs an average ${card.subCategory} mix (model estimate)`}>
                <Box sx={{ width: 118, display: "flex", justifyContent: "flex-end" }}>
                  <Level level={m.level} suffix={!m.tried && m.level !== "trySmall" ? " – start small" : ""} />
                </Box>
              </Tooltip>
            </Stack>
          ))}
        </Part>
      )}

      {(card.makeMore.length > 0 || card.cutBack.length > 0) && (
        <Part title="MAKE MORE / CUT BACK">
          {card.makeMore.map((m) => (
            <Typography key={`m-${m.attribute}-${m.value}`} variant="body2" sx={{ py: 0.25 }}>
              <Box component="span" sx={{ color: UP, fontWeight: 800 }}>＋ </Box>
              <b>{m.value}</b> ({ATTR[m.attribute]}) — sells {m.times.toFixed(1)}× the average design here, only {m.styles} design{m.styles === 1 ? "" : "s"}
            </Typography>
          ))}
          {card.cutBack.map((c) => (
            <Typography key={`c-${c.attribute}-${c.value}`} variant="body2" sx={{ py: 0.25 }}>
              <Box component="span" sx={{ color: DOWN, fontWeight: 800 }}>－ </Box>
              <b>{c.value}</b> ({ATTR[c.attribute]}) — {CUT_REASON[c.reason] ?? c.reason} · {c.styles} designs
            </Typography>
          ))}
        </Part>
      )}
    </Paper>
  );
}

/** Suggestion tab: the top line + one card per product, all from one ranking. */
export default function ProductCards({ season }: { season: DesignSeason }) {
  const [all, setAll] = useState(false);
  const cards = season.products ?? [];
  const o = season.overview;
  const shown = all ? cards : cards.slice(0, SHOW);
  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2, borderLeft: 4, borderLeftColor: ACCENT, bgcolor: "#fbfaff" }}>
        <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mb: 1 }}>
          <LightbulbOutlinedIcon fontSize="small" sx={{ color: ACCENT }} />
          <Typography variant="subtitle1" sx={{ fontWeight: 800, flexGrow: 1 }}>In short — {season.label}</Typography>
          <AboutButton topic="products" />
        </Stack>
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, columnGap: 4, rowGap: 0.5 }}>
          {o?.focus?.length ? <Typography variant="body2"><Box component="span" sx={{ color: MUTED }}>Focus on: </Box><b>{o.focus.join(", ")}</b></Typography> : null}
          {o?.colours?.length ? <Typography variant="body2"><Box component="span" sx={{ color: MUTED }}>Top colours: </Box><b>{o.colours.join(", ")}</b></Typography> : null}
          {o?.fabrics?.length ? <Typography variant="body2"><Box component="span" sx={{ color: MUTED }}>Top fabrics: </Box><b>{o.fabrics.join(", ")}</b></Typography> : null}
        </Box>
      </Paper>
      {cards.length === 0 && <Paper variant="outlined" sx={{ p: 3, color: MUTED }}>No product is trending or in trend for this season yet.</Paper>}
      <Stack direction="row" sx={{ flexWrap: "wrap", gap: 2 }}>
        {shown.map((c, i) => <ProductCard key={c.subCategory} card={c} rank={i + 1} reference={season.reference} seasonLabel={season.label} />)}
      </Stack>
      {cards.length > SHOW && (
        <Box>
          <Button onClick={() => setAll((v) => !v)} sx={{ textTransform: "none", fontWeight: 700 }}>
            {all ? "Show fewer products" : `Show all ${cards.length} products`}
          </Button>
        </Box>
      )}
    </Stack>
  );
}
