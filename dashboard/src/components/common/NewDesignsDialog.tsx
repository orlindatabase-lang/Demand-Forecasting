import { useMemo } from "react";
import { Dialog, DialogContent, IconButton, Stack, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "@/components/tables/DataTable";
import { useNewDesigns } from "@/hooks/useDashboardData";
import type { NewDesignRow } from "@/types";

interface NewDesignsDialogProps {
  open: boolean;
  onClose: () => void;
  maxAgeDays: number;
}

export default function NewDesignsDialog({ open, onClose, maxAgeDays }: NewDesignsDialogProps) {
  const { data, isLoading } = useNewDesigns(open, maxAgeDays);

  const columns = useMemo<ColumnDef<NewDesignRow, any>[]>(
    () => [
      { accessorKey: "designNo", header: "Design No", size: 140 },
      { accessorKey: "launchDate", header: "Launch Date", size: 120 },
      { accessorKey: "daysSinceLaunch", header: "Days Ago", size: 100 },
      { accessorKey: "skuCount", header: "SKU Variants", size: 100 },
    ],
    [],
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth PaperProps={{ sx: { maxHeight: "85vh" } }}>
      <DialogContent sx={{ p: 3 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 800 }}>
            Newly Launched Designs
          </Typography>
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>

        {isLoading || !data ? (
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            Loading…
          </Typography>
        ) : (
          <DataTable
            data={data.items}
            columns={columns}
            initialPageSize={10}
            globalSearchPlaceholder="Search design…"
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
