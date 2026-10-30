import { useMemo, type ReactNode } from "react";
import { Autocomplete, Box, Button, FormControl, InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography } from "@mui/material";
import { useCategoryAnalysisFiltered } from "@/hooks/useDashboardData";
import { formatNumber } from "@/utils/format";
import type { CategoryStyleOption } from "@/types";
import { COLORS } from "@/theme/colors";

const ALL = "";
const TIER_ORDER = ["T0", "T1", "T2", "T3", "T11", "T12"];

const tierRank = (t: string) => {
  const i = TIER_ORDER.indexOf(t);
  return i === -1 ? TIER_ORDER.length : i;
};

function sortedUnique(values: string[], byTier = false): string[] {
  const out = [...new Set(values)];
  return byTier ? out.sort((a, b) => tierRank(a) - tierRank(b) || a.localeCompare(b)) : out.sort((a, b) => a.localeCompare(b));
}

export interface StyleFilters {
  brand: string;
  tier: string;
  category: string;
  subCategory: string;
  style: string;
}

export const NO_STYLE_FILTERS: StyleFilters = { brand: ALL, tier: ALL, category: ALL, subCategory: ALL, style: ALL };

function FilterSelect({ id, label, value, options, onChange }: { id: string; label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <FormControl size="small" sx={{ minWidth: { xs: "100%", sm: 150 } }}>
      <InputLabel id={`${id}-label`}>{label}</InputLabel>
      <Select labelId={`${id}-label`} id={id} label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        <MenuItem value={ALL}>All</MenuItem>
        {options.map((o) => (
          <MenuItem key={o} value={o}>{o}</MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}

/** Brand / Tier / Category / Sub Category / Style filters shared by the
 * Category, Sub Category and Tiers Spike Rate tables and their charts
 * (2026-10-30, user-requested; before, only the Tiers chart had them). Each
 * list only offers what fits the filters to its left. */
/** ``actions`` (e.g. Export CSV) sit at the right end of the filter row. */
export default function StyleFilterBar({ value, onChange, showStyle = true, actions }: {
  value: StyleFilters;
  onChange: (v: StyleFilters) => void;
  showStyle?: boolean;
  actions?: ReactNode;
}) {
  const { brand, tier, category, subCategory, style } = value;
  const { data, isFetching } = useCategoryAnalysisFiltered(value);
  const options: CategoryStyleOption[] = useMemo(() => data?.options ?? [], [data]);

  // Cascading choices: each list fits the filters to its left (Brand first).
  const brandOptions = useMemo(() => sortedUnique(options.map((o) => o.brand)), [options]);
  const inBrand = useMemo(() => options.filter((o) => !brand || o.brand === brand), [options, brand]);
  const tierOptions = useMemo(() => sortedUnique(inBrand.map((o) => o.tier), true), [inBrand]);
  const inTier = useMemo(() => inBrand.filter((o) => !tier || o.tier === tier), [inBrand, tier]);
  const categoryOptions = useMemo(() => sortedUnique(inTier.map((o) => o.category)), [inTier]);
  const inCategory = useMemo(() => inTier.filter((o) => !category || o.category === category), [inTier, category]);
  const subCategoryOptions = useMemo(() => sortedUnique(inCategory.map((o) => o.subCategory)), [inCategory]);
  const styleOptions = useMemo(
    () => inCategory.filter((o) => !subCategory || o.subCategory === subCategory),
    [inCategory, subCategory],
  );

  // A choice further right that no longer fits is cleared.
  const pick = (level: "brand" | "tier" | "category" | "subCategory", v: string) => {
    if (level === "brand") onChange({ ...NO_STYLE_FILTERS, brand: v });
    else if (level === "tier") onChange({ ...NO_STYLE_FILTERS, brand, tier: v });
    else if (level === "category") onChange({ ...NO_STYLE_FILTERS, brand, tier, category: v });
    else onChange({ ...value, subCategory: v, style: ALL });
  };

  const chosen = [brand, tier, category, subCategory, style].filter(Boolean);
  const selectedStyle = styleOptions.find((o) => o.style === style) ?? null;

  return (
    <Paper variant="outlined" sx={{ p: 1.5, mb: 2, borderRadius: 2, bgcolor: COLORS.bgWhite, borderColor: COLORS.border }}>
      <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.25, alignItems: "center" }}>
        <FilterSelect id="sf-brand" label="Brand" value={brand} options={brandOptions} onChange={(v) => pick("brand", v)} />
        <FilterSelect id="sf-tier" label="Tier" value={tier} options={tierOptions} onChange={(v) => pick("tier", v)} />
        <FilterSelect id="sf-category" label="Category" value={category} options={categoryOptions} onChange={(v) => pick("category", v)} />
        <FilterSelect id="sf-subcategory" label="Sub Category" value={subCategory} options={subCategoryOptions} onChange={(v) => pick("subCategory", v)} />
        {showStyle && (
        <Autocomplete
          id="sf-style"
          size="small"
          sx={{ minWidth: { xs: "100%", sm: 230 } }}
          options={styleOptions}
          value={selectedStyle}
          onChange={(_e, o) => onChange({ ...value, style: o?.style ?? ALL })}
          getOptionLabel={(o) => o.style}
          isOptionEqualToValue={(a, b) => a.style === b.style}
          filterOptions={(opts, state) => {
            const q = state.inputValue.trim().toLowerCase();
            return (q ? opts.filter((o) => o.style.toLowerCase().includes(q) || o.subCategory.toLowerCase().includes(q)) : opts).slice(0, 200);
          }}
          renderOption={(props, o) => (
            <li {...props} key={o.style}>
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{o.style}</Typography>
                <Typography variant="caption" sx={{ color: "text.secondary" }}>{o.brand} · {o.tier} · {o.subCategory}</Typography>
              </Box>
            </li>
          )}
          renderInput={(params) => <TextField {...params} label="Style" placeholder={`Search ${formatNumber(styleOptions.length)} styles…`} />}
        />
        )}
        {chosen.length > 0 && (
          <Button size="small" onClick={() => onChange(NO_STYLE_FILTERS)} sx={{ textTransform: "none", fontWeight: 700 }}>
            Clear filters
          </Button>
        )}
        {actions && (
          <Stack direction="row" sx={{ ml: { sm: "auto" }, gap: 1.5, alignItems: "center", flexWrap: "wrap" }}>
            {actions}
          </Stack>
        )}
      </Stack>
      <Typography variant="caption" sx={{ display: "block", mt: 1, color: "text.secondary" }}>
        {chosen.length === 0 ? "All styles" : chosen.join(" · ")} — {formatNumber(data?.styleCount ?? 0)} styles · table and chart
        {isFetching ? " · updating…" : ""}
      </Typography>
    </Paper>
  );
}
