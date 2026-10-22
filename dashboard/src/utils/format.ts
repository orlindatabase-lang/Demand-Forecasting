// Every caller of this displays a unit/piece COUNT (Actual/Forecast qty,
// SKU counts, pending pieces, etc.) - never money or a percentage - so
// there's no legitimate case for a fractional display here. Some call
// sites (e.g. WeeklySalesGrid's month-total forecast cells) already wrapped
// their own value in Math.round() before calling this; most others (any
// server value that's genuinely fractional) did not, and were showing raw
// decimals like "125.31"
// (2026-09-18, user-requested: integers only). Rounding once here, always,
// covers every caller uniformly instead of hunting down each display site.
// Reused across every call instead of letting toLocaleString() re-resolve
// "en-IN" locale data each time - this is called thousands of times per
// Weekly Sales Report render (every Actual/Forecast/total cell across every
// visible row and week) and was showing up as a real render-time hotspot.
const NUMBER_FORMAT_EN_IN = new Intl.NumberFormat("en-IN");

export function formatNumber(v: number | string | undefined | null): string {
  const n = typeof v === "number" ? v : Number(v ?? 0);
  if (Number.isNaN(n)) return "0";
  return NUMBER_FORMAT_EN_IN.format(Math.round(n));
}
