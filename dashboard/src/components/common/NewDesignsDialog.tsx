import { useMemo } from "react";
import { Box, Dialog, DialogContent, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "@/components/tables/DataTable";
import { useNewDesigns, useNewDesignFestivalSpikes } from "@/hooks/useDashboardData";
import type { NewDesignRow, NewDesignFestivalSpike } from "@/types";

interface NewDesignsDialogProps {
  open: boolean;
  onClose: () => void;
  maxAgeDays: number;
}

type Row = NewDesignRow & { spike?: NewDesignFestivalSpike };

function SpikeBadge({ spike }: { spike: NewDesignFestivalSpike }) {
  const tooltip = (
    <Box sx={{ p: 0.5 }}>
      <Typography variant="caption" sx={{ display: "block", fontWeight: 700, mb: 0.5 }}>
        Borrowed demand shape from:
      </Typography>
      {spike.similarDesigns.length > 0 ? (
        spike.similarDesigns.map((s) => (
          <Typography key={s.design} variant="caption" sx={{ display: "block" }}>
            {s.design} · {Math.round(s.similarity * 100)}% material match
          </Typography>
        ))
      ) : (
        <Typography variant="caption" sx={{ display: "block", fontStyle: "italic" }}>
          No material-similar design old enough to donate yet — this leans on the
          model/naive baseline instead.
        </Typography>
      )}
    </Box>
  );
  return (
    <Tooltip title={tooltip} arrow placement="top">
      <Box
        component="span"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          fontSize: "0.72rem",
          fontWeight: 700,
          color: "#f97316",
          bgcolor: "#f9731622",
          borderRadius: 1,
          px: 1,
          py: 0.4,
          cursor: "default",
        }}
      >
        🎉 {spike.event} · {spike.weekStart} · {spike.predictedQty} units (+{spike.upliftPct}%)
      </Box>
    </Tooltip>
  );
}

export default function NewDesignsDialog({ open, onClose, maxAgeDays }: NewDesignsDialogProps) {
  const { data, isLoading } = useNewDesigns(open, maxAgeDays);
  const { data: spikeData } = useNewDesignFestivalSpikes(open, maxAgeDays);

  const rows = useMemo<Row[]>(() => {
    if (!data) return [];
    const spikeByDesign = new Map(spikeData?.items.map((s) => [s.designNo, s]) ?? []);
    return data.items.map((r) => ({ ...r, spike: spikeByDesign.get(r.designNo) }));
  }, [data, spikeData]);

  const columns = useMemo<ColumnDef<Row, any>[]>(
    () => [
      { accessorKey: "designNo", header: "Design No", size: 140 },
      { accessorKey: "launchDate", header: "Launch Date", size: 120 },
      { accessorKey: "daysSinceLaunch", header: "Days Ago", size: 100 },
      { accessorKey: "skuCount", header: "SKU Variants", size: 100 },
      {
        id: "spike",
        header: "Predicted Festival Spike",
        cell: ({ row }) => {
          const spike = row.original.spike;
          if (!spike) return <Typography variant="body2" sx={{ color: "text.disabled" }}>—</Typography>;
          return <SpikeBadge spike={spike} />;
        },
        size: 220,
      },
    ],
    [],
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth PaperProps={{ sx: { maxHeight: "85vh" } }}>
      <DialogContent sx={{ p: 3 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
          <Typography variant="h6" sx={{ fontWeight: 800 }}>
            Newly Launched Designs
          </Typography>
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>
        <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 2 }}>
          🎉 badges predict a festival/sale-week spike. These designs haven't lived through a festival
          themselves yet — the prediction is borrowed from material-similar designs (hover the badge to see which).
        </Typography>

        {isLoading || !data ? (
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            Loading…
          </Typography>
        ) : (
          <DataTable
            data={rows}
            columns={columns}
            initialPageSize={10}
            globalSearchPlaceholder="Search design…"
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
