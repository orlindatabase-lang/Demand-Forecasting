import dayjs from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat";
import type { SortingFn } from "@tanstack/react-table";

dayjs.extend(customParseFormat);

export function formatNumber(v: number | string | undefined | null): string {
  const n = typeof v === "number" ? v : Number(v ?? 0);
  if (Number.isNaN(n)) return "0";
  return n.toLocaleString("en-IN");
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
