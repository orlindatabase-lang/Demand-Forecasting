import { Button } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";

interface ExportButtonProps {
  rows: Record<string, unknown>[];
  filename: string;
}

function toCsv(rows: Record<string, unknown>[]): string {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]);
  const escape = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(headers.map((h) => escape(row[h])).join(","));
  }
  return lines.join("\n");
}

export default function ExportButton({ rows, filename }: ExportButtonProps) {
  const handleExport = () => {
    const csv = toCsv(rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filename}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Button
      size="small"
      variant="outlined"
      startIcon={<DownloadIcon fontSize="small" />}
      onClick={handleExport}
      disabled={rows.length === 0}
    >
      Export
    </Button>
  );
}
