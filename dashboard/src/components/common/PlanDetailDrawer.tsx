import { useMemo } from "react";
import {
  Box,
  Dialog,
  DialogContent,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { LineChart } from "@mui/x-charts/LineChart";
import { useSkuBreakdown, useSkuTopRegions } from "@/hooks/useDashboardData";
import type { PlanningRow, TopRegionRow } from "@/types";
import { formatNumber } from "@/utils/format";

function SummaryChip({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Box
      sx={{
        px: 1.5,
        py: 1,
        borderRadius: 1.5,
        border: "1px solid",
        borderColor: "divider",
        minWidth: 100,
      }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 700, color: color ?? "text.primary" }}>
        {value}
      </Typography>
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        {label}
      </Typography>
    </Box>
  );
}

function MiniRegionTable({ title, rows }: { title: string; rows: TopRegionRow[] }) {
  return (
    <Box sx={{ flex: 1, minWidth: 0, width: "100%" }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
        {title}
      </Typography>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{title}</TableCell>
              <TableCell align="right">Units (90d)</TableCell>
              <TableCell align="right">Revenue (90d)</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={3} sx={{ textAlign: "center", color: "text.disabled" }}>
                  No sales data
                </TableCell>
              </TableRow>
            ) : (
              rows.map((r) => (
                <TableRow key={r.name}>
                  <TableCell>{r.name}</TableCell>
                  <TableCell align="right">{formatNumber(r.units90)}</TableCell>
                  <TableCell align="right">₹{formatNumber(r.revenue90)}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}

interface PlanDetailDrawerProps {
  sku: string | null;
  row: PlanningRow | null;
  onClose: () => void;
}

export default function PlanDetailDrawer({ sku, row, onClose }: PlanDetailDrawerProps) {
  const { data: bd, isLoading } = useSkuBreakdown(sku, 5);
  const { data: topRegions, isLoading: topRegionsLoading } = useSkuTopRegions(sku);
  const theme = { palette: { success: { main: "#22c55e" }, warning: { main: "#f97316" }, error: { main: "#ef4444" } } };

  const accuracy = bd?.overallAccuracyPct ?? 0;
  const actualTotal = (bd?.historical ?? []).reduce((a, p) => a + p.actual, 0);
  const fcTotal = (bd?.forecast ?? []).reduce((a, p) => a + p.qty, 0);

  const chart = useMemo(() => {
    if (!bd) return null;
    const labels = [...bd.historical.map((p) => p.date), ...bd.forecast.map((p) => p.date)];
    const actualSeries = [
      ...bd.historical.map((p) => p.actual),
      ...bd.forecast.map(() => null),
    ];
    const forecastSeries = [
      ...bd.historical.map((p) => p.forecast),
      ...bd.forecast.map((p) => p.qty),
    ];
    return { labels, actualSeries, forecastSeries };
  }, [bd]);

  return (
    <Dialog
      open={!!sku}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{ sx: { maxHeight: "90vh" } }}
    >
      <DialogContent sx={{ p: 3 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 800 }}>
            {sku}
          </Typography>
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>

        {row && (
          <Typography variant="body2" sx={{ color: "text.secondary", mb: 2 }}>
            Design {row.designNo} · {row.lifecycleStage}
          </Typography>
        )}

        {isLoading || !bd ? (
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            Loading breakdown…
          </Typography>
        ) : (
          <>
            <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap sx={{ mb: 3 }}>
              <SummaryChip label="Forecast (5w)" value={formatNumber(fcTotal)} color={theme.palette.warning.main} />
              <SummaryChip label="Actual (recent)" value={formatNumber(actualTotal)} />
              <SummaryChip
                label="Model Accuracy"
                value={`${accuracy}%`}
                color={accuracy >= 85 ? theme.palette.success.main : accuracy >= 70 ? theme.palette.warning.main : theme.palette.error.main}
              />
              {row && (
                <SummaryChip
                  label="Price"
                  value={row.price > 0 ? `₹${formatNumber(row.price)}` : "—"}
                />
              )}
              {row && <SummaryChip label="Inventory" value={formatNumber(row.inventoryQty)} />}
              {row && <SummaryChip label="WIP" value={formatNumber(row.wipQty)} />}
            </Stack>

            {chart && (
              <Box sx={{ mb: 3 }}>
                <LineChart
                  height={320}
                  xAxis={[{ scaleType: "point", data: chart.labels, tickLabelStyle: { fontSize: 11 } }]}
                  series={[
                    {
                      label: "Actual",
                      data: chart.actualSeries,
                      color: "#312c5c",
                      connectNulls: false,
                    },
                    {
                      label: "Forecast",
                      data: chart.forecastSeries,
                      color: "#f97316",
                      curve: "linear",
                    },
                  ]}
                  grid={{ horizontal: true }}
                  margin={{ left: 60, right: 30, top: 20, bottom: 40 }}
                  slotProps={{ legend: { direction: "row", position: { vertical: "top", horizontal: "middle" } } }}
                />
              </Box>
            )}

            <Box>
              <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                Recent weeks: forecast vs actual
              </Typography>
              <Box sx={{ overflowX: "auto" }}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Week</TableCell>
                      <TableCell align="right">Forecast</TableCell>
                      <TableCell align="right">Actual</TableCell>
                      <TableCell align="right">Variance</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {bd.historical.map((p) => (
                      <TableRow key={p.date}>
                        <TableCell>
                          {p.date}
                          {p.partial && (
                            <Typography component="span" variant="caption" sx={{ color: "text.secondary", ml: 0.5 }}>
                              (this week, so far)
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell align="right">{formatNumber(p.forecast)}</TableCell>
                        <TableCell align="right">{formatNumber(p.actual)}</TableCell>
                        <TableCell
                          align="right"
                          sx={
                            p.partial
                              ? { color: "text.secondary", fontWeight: 600 }
                              : { color: p.variance >= 0 ? "#22c55e" : "#ef4444", fontWeight: 600 }
                          }
                        >
                          {p.partial ? "—" : `${p.variance >= 0 ? "+" : ""}${formatNumber(p.variance)}`}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            </Box>

            <Typography variant="subtitle2" sx={{ fontWeight: 700, mt: 3, mb: 1 }}>
              Where this SKU sells
            </Typography>
            {topRegionsLoading ? (
              <Typography variant="body2" sx={{ color: "text.secondary" }}>
                Loading regional breakdown…
              </Typography>
            ) : (
              <Stack direction={{ xs: "column", md: "row" }} spacing={3} sx={{ alignItems: "flex-start" }}>
                <MiniRegionTable title="Top States" rows={topRegions?.topStates ?? []} />
                <MiniRegionTable title="Top Cities" rows={topRegions?.topCities ?? []} />
                <MiniRegionTable title="Top Warehouses" rows={topRegions?.topWarehouses ?? []} />
              </Stack>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
