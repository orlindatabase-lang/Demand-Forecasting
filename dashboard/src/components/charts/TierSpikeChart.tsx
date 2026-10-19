import { useMemo, useState } from "react";
import { Autocomplete, Box, Button, FormControl, InputLabel, MenuItem, Select, Stack, TextField, Typography } from "@mui/material";
import CategoryAnalysisCharts, { type ChartCell } from "./CategoryAnalysisCharts";
import { useCategoryAnalysisFiltered } from "@/hooks/useDashboardData";
import { formatNumber } from "@/utils/format";
import type { CategoryStyleOption } from "@/types";

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

function FilterSelect({ id, label, value, options, onChange }: { id: string; label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <FormControl size="small" sx={{ minWidth: 150 }}>
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

/** The Tiers Spike Rate chart with its own filters (2026-10-13,
 * user-requested): Tier, Category, Sub Category and a searchable Style. Each
 * list only offers what fits the filters before it; the numbers are summed on
 * the server for exactly the matching styles (/api/reports/category-analysis/filtered). */
export default function TierSpikeChart({ months, partialMonth }: { months: string[]; partialMonth: string }) {
  const [brand, setBrand] = useState(ALL);
  const [tier, setTier] = useState(ALL);
  const [category, setCategory] = useState(ALL);
  const [subCategory, setSubCategory] = useState(ALL);
  const [style, setStyle] = useState(ALL);
  const { data, isFetching } = useCategoryAnalysisFiltered({ brand, tier, category, subCategory, style });
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
    if (level === "brand") {
      setBrand(v);
      setTier(ALL);
    }
    if (level === "tier") setTier(v);
    if (level === "brand" || level === "tier" || level === "category") {
      if (level === "category") setCategory(v);
      else setCategory(ALL);
      setSubCategory(ALL);
    } else setSubCategory(v);
    setStyle(ALL);
  };

  const totals: Record<string, ChartCell> = useMemo(() => {
    const out: Record<string, ChartCell> = {};
    for (const r of data?.rows ?? []) out[r.month] = { units: r.units, prevUnits: r.prevUnits, changePct: r.changePct, sameChangePct: r.sameChangePct };
    return out;
  }, [data]);

  const chosen = [brand, tier, category, subCategory, style].filter(Boolean);
  const selectedStyle = styleOptions.find((o) => o.style === style) ?? null;

  const filters = (
    <Stack spacing={1}>
      <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.25 }}>
        <FilterSelect id="tsr-brand" label="Brand" value={brand} options={brandOptions} onChange={(v) => pick("brand", v)} />
        <FilterSelect id="tsr-tier" label="Tier" value={tier} options={tierOptions} onChange={(v) => pick("tier", v)} />
        <FilterSelect id="tsr-category" label="Category" value={category} options={categoryOptions} onChange={(v) => pick("category", v)} />
        <FilterSelect id="tsr-subcategory" label="Sub Category" value={subCategory} options={subCategoryOptions} onChange={(v) => pick("subCategory", v)} />
        <Autocomplete
          id="tsr-style"
          size="small"
          sx={{ minWidth: 230 }}
          options={styleOptions}
          value={selectedStyle}
          onChange={(_e, o) => setStyle(o?.style ?? ALL)}
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
        {chosen.length > 0 && (
          <Button
            size="small"
            onClick={() => {
              setBrand(ALL);
              setTier(ALL);
              setCategory(ALL);
              setSubCategory(ALL);
              setStyle(ALL);
            }}
            sx={{ textTransform: "none", fontWeight: 700 }}
          >
            Clear filters
          </Button>
        )}
      </Stack>
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        {chosen.length === 0 ? "All styles" : chosen.join(" · ")} — {formatNumber(data?.styleCount ?? 0)} styles
        {isFetching ? " · updating…" : ""}
      </Typography>
    </Stack>
  );

  return (
    <CategoryAnalysisCharts
      months={months}
      groups={[]}
      totals={totals}
      partialMonth={data?.partialMonth || partialMonth}
      groupName="Tier"
      filters={filters}
    />
  );
}
