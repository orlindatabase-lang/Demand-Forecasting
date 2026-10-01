import dayjs from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat";
import type { SortingFn } from "@tanstack/react-table";

dayjs.extend(customParseFormat);

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

/** ISO date range as a compact string, e.g. "8-15 Aug 2026" / "28 Aug-6 Sep 2026" / "27 Dec 2026-2 Jan 2027". */
export function formatDateRange(startIso: string, endIso: string): string {
  const start = dayjs(startIso);
  const end = dayjs(endIso);
  if (!start.isValid() || !end.isValid()) return startIso;
  if (start.isSame(end, "day")) return start.format("D MMM YYYY");
  if (start.isSame(end, "month")) return `${start.format("D")}-${end.format("D MMM YYYY")}`;
  if (start.isSame(end, "year")) return `${start.format("D MMM")}-${end.format("D MMM YYYY")}`;
  return `${start.format("D MMM YYYY")}-${end.format("D MMM YYYY")}`;
}

/**
 * Date columns are display strings like "D MMM YYYY" (e.g. "9 Jul 2026"),
 * which sort alphabetically by default (wrong order). Parse and compare
 * as real dates instead.
 */
export const dateSortingFn: SortingFn<any> = (rowA, rowB, columnId) => {
  const a = rowA.getValue<string>(columnId);
  const b = rowB.getValue<string>(columnId);
  const da = a ? dayjs(a, "D MMM YYYY") : null;
  const db = b ? dayjs(b, "D MMM YYYY") : null;
  const va = da?.isValid() ? da.valueOf() : -Infinity;
  const vb = db?.isValid() ? db.valueOf() : -Infinity;
  return va - vb;
};
