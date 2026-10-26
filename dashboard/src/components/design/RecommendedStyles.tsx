import { useMemo, useState } from "react";
import {
  Box,
  Chip,
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
import AboutButton from "./AboutButton";
import { UP } from "./TrendBadge";
import { useCatalogStyleTiers } from "@/hooks/useDashboardData";
import { formatNumber } from "@/utils/format";
import type { CatalogStyleImage, DesignLevel, DesignRecommendedStyle } from "@/types";

const ACCENT = "#312c5c";
const ACCENT_SOFT = "rgba(49,44,92,0.08)";
const MUTED = "#64748b";

const FEATURE_ORDER = ["category", "subCategory", "colour", "fabric", "embroidery", "neck", "sleeve", "topType"];
const FEATURE_LABEL: Record<string, string> = {
  category: "Category", subCategory: "Product", colour: "Colour", fabric: "Fabric",
  embroidery: "Embroidery", neck: "Neck", sleeve: "Sleeve", topType: "Top type",
};
const LEVEL_LABEL: Record<DesignLevel, string> = { strong: "Strong", good: "Good", trySmall: "Try small" };

/** One feature: green when the style matches the suggestion, grey and crossed
 * out (with what the style has instead) when it does not. */
function Feature({ attr, value, matched }: { attr: string; value: string; matched: boolean }) {
  return (
    <Tooltip title={`${FEATURE_LABEL[attr] ?? attr}${matched ? " — matches the suggestion" : " — not the suggested one"}`}>
      <Box
        component="span"
        sx={{
          px: 0.75, py: 0.1, borderRadius: 1, fontSize: "0.72rem", fontWeight: 700, whiteSpace: "nowrap",
          bgcolor: matched ? "rgba(21,128,61,0.1)" : "rgba(100,116,139,0.1)",
          color: matched ? UP : MUTED,
          textDecoration: matched ? "none" : "line-through",
        }}
      >
        {value || `no ${(FEATURE_LABEL[attr] ?? attr).toLowerCase()}`}
      </Box>
    </Tooltip>
  );
}

/** Matched / suggested features as a count and a small bar. */
function MatchBar({ count, of }: { count: number; of: number }) {
  return (
    <Stack spacing={0.4} sx={{ minWidth: 64 }}>
      <Typography variant="body2" sx={{ fontWeight: 800, color: count === of ? UP : ACCENT }}>
        {count}/{of}
      </Typography>
      <Box sx={{ height: 4, borderRadius: 2, bgcolor: ACCENT_SOFT }}>
        <Box sx={{ width: `${(count / Math.max(1, of)) * 100}%`, height: "100%", borderRadius: 2, bgcolor: count === of ? UP : ACCENT }} />
      </Box>
    </Stack>
  );
}

/** Suggestion tab: existing styles carrying the most suggested design features
 * (2026-10-26, user-requested) - the ones to push now. */
export default function RecommendedStyles({ styles }: { styles: DesignRecommendedStyle[] }) {
  const [product, setProduct] = useState("");
  const catalog = useCatalogStyleTiers();
  const info = useMemo(() => {
    const map = new Map<string, CatalogStyleImage>();
    for (const g of catalog.data?.tiers ?? []) for (const st of g.styles) map.set(st.name.toUpperCase(), st);
    return map;
  }, [catalog.data]);
  const products = useMemo(() => [...new Set(styles.map((s) => s.subCategory))], [styles]);
  const shown = product ? styles.filter((s) => s.subCategory === product) : styles;

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Box sx={{ mb: 1.25 }}>
        <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
          <StorefrontOutlinedIcon fontSize="small" sx={{ color: ACCENT }} />
          <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Styles to push</Typography>
          <AboutButton topic="styles" />
        </Stack>
        <Typography variant="body2" sx={{ color: MUTED }}>
          Existing designs that already have the suggested features of their product card. Most matching features on top; the same match goes to the bigger expected sale.
        </Typography>
      </Box>
      {styles.length === 0 ? (
        <Typography variant="body2" sx={{ color: MUTED }}>No existing design matches the suggested features yet.</Typography>
      ) : (
        <>
          <Stack direction="row" sx={{ flexWrap: "wrap", gap: 0.75, mb: 1.5 }}>
            {["", ...products].map((p) => (
              <Chip
                key={p || "all"}
                size="small"
                label={p ? `${p} (${styles.filter((s) => s.subCategory === p).length})` : `All products (${styles.length})`}
                onClick={() => setProduct(p)}
                sx={{
                  fontWeight: 700,
                  bgcolor: product === p ? ACCENT : ACCENT_SOFT,
                  color: product === p ? "#fff" : ACCENT,
                  "&:hover": { bgcolor: product === p ? ACCENT : "rgba(49,44,92,0.16)" },
                }}
              />
            ))}
          </Stack>
          <TableContainer sx={{ maxHeight: 560 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow sx={{ "& th": { fontWeight: 800, bgcolor: "#eef0f5", fontSize: "0.75rem" } }}>
                  <TableCell>#</TableCell>
                  <TableCell>DESIGN</TableCell>
                  <TableCell>PRODUCT</TableCell>
                  <TableCell>FEATURES</TableCell>
                  <TableCell>MATCHING / NOT MATCHING</TableCell>
                  <TableCell align="right">EXPECTED SALE (35 DAYS)</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {shown.map((s, i) => {
                  const cat = info.get(s.style.toUpperCase());
                  const attrs = FEATURE_ORDER.filter((a) => a in s.matchedFeatures || s.missingFeatures.includes(a));
                  return (
                    <TableRow key={`${s.subCategory}-${s.style}`} hover>
                      <TableCell sx={{ color: MUTED, fontVariantNumeric: "tabular-nums" }}>{product ? i + 1 : s.rank}</TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap" }}>
                        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                          {cat?.imageUrl ? (
                            <Box
                              component="img"
                              src={cat.imageUrl}
                              alt={s.style}
                              loading="lazy"
                              sx={{ width: 36, height: 36, borderRadius: 1, objectFit: "cover", border: "1px solid", borderColor: "divider", flexShrink: 0 }}
                            />
                          ) : (
                            <Box sx={{ width: 36, height: 36, borderRadius: 1, bgcolor: ACCENT_SOFT, flexShrink: 0 }} />
                          )}
                          <Box>
                            <Typography variant="body2" sx={{ fontWeight: 800 }}>{s.style}</Typography>
                            {cat?.tier && <Typography variant="caption" sx={{ color: MUTED, fontWeight: 700 }}>{cat.tier}</Typography>}
                          </Box>
                        </Stack>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.subCategory}</Typography>
                        <Typography variant="caption" sx={{ color: MUTED }}>{LEVEL_LABEL[s.productLevel] ?? s.productLevel} product</Typography>
                      </TableCell>
                      <TableCell><MatchBar count={s.featureMatchCount} of={s.suggestedFeatureCount} /></TableCell>
                      <TableCell>
                        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, maxWidth: 520 }}>
                          {attrs.filter((a) => a !== "category" && a !== "subCategory").map((a) =>
                            a in s.matchedFeatures
                              ? <Feature key={a} attr={a} value={s.matchedFeatures[a]} matched />
                              : <Feature key={a} attr={a} value={s.styleFeatures?.[a] ?? ""} matched={false} />,
                          )}
                        </Box>
                      </TableCell>
                      <TableCell align="right" sx={{ fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>{formatNumber(s.forecast35)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}
    </Paper>
  );
}
