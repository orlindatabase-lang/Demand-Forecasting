import { useState } from "react";
import { CircularProgress, IconButton, Snackbar, Alert, Tooltip } from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { dataService } from "@/services/dataService";

// Refresh button (2026-09-17, user-requested: "give the user access to
// restart the API any time") - calls the existing /admin/refresh endpoint,
// which re-fetches BigQuery/ERP and rebuilds every in-memory table with no
// downtime. This is deliberately NOT an actual OS-level process restart
// (the API is watchdog-managed and already self-restarts on crash) - a data
// rebuild is the safe, always-available action a user should be able to
// trigger themselves; killing the process is not. Moved out of the removed
// TopBar (2026-09-24, user-requested: drop the top bar entirely) to sit next
// to the page title instead.
export default function RefreshButton() {
  const queryClient = useQueryClient();
  const [toast, setToast] = useState<{ severity: "info" | "success" | "error"; message: string } | null>(null);

  const mutation = useMutation({
    mutationFn: () => dataService.adminRefresh(),
    // The real BigQuery/ERP fetch this triggers has been observed to take
    // 2.5+ minutes (2026-09-17) - a bare spinner for that long looks frozen,
    // so an immediate, non-auto-hiding toast sets the right expectation
    // right away rather than leaving the user guessing.
    onMutate: () => {
      setToast({ severity: "info", message: "Refreshing data from BigQuery/ERP — this can take a few minutes…" });
    },
    onSuccess: (result) => {
      // Every polled query (weekly grid, production trackers, ...) now
      // re-fetches against the freshly rebuilt data instead of waiting out
      // its own refetchInterval.
      queryClient.invalidateQueries();
      setToast({
        severity: "success",
        message: `Refreshed — ${result.rows.toLocaleString()} rows, snapshot ${result.snapshot} (${result.source}).`,
      });
    },
    onError: (err) => {
      setToast({ severity: "error", message: err instanceof Error ? err.message : "Refresh failed." });
    },
  });

  return (
    <>
      <Tooltip
        title={
          mutation.isPending
            ? "Refreshing — this can take a few minutes"
            : "Refresh data — re-fetches from BigQuery/ERP and rebuilds the dashboard"
        }
      >
        <span>
          <IconButton
            size="small"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            sx={{ color: "text.secondary" }}
          >
            {mutation.isPending ? <CircularProgress size={20} /> : <RefreshIcon fontSize="small" />}
          </IconButton>
        </span>
      </Tooltip>
      <Snackbar
        open={!!toast}
        autoHideDuration={mutation.isPending ? null : 6000}
        onClose={(_, reason) => {
          if (reason === "clickaway" || mutation.isPending) return;
          setToast(null);
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        {toast ? (
          <Alert
            severity={toast.severity}
            variant="filled"
            onClose={mutation.isPending ? undefined : () => setToast(null)}
          >
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </>
  );
}
