import { useMemo, useState } from "react";
import {
  Box,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TableSortLabel,
  TextField,
  Typography,
  InputAdornment,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";

interface DataTableProps<T> {
  title?: string;
  data: T[];
  columns: ColumnDef<T, any>[];
  initialPageSize?: number;
  globalSearchPlaceholder?: string;
  toolbarActions?: React.ReactNode;
  /** Optional: observe the live search text (e.g. to drive a detail view
   * elsewhere on the page). Unused by existing callers — purely additive. */
  onSearchChange?: (value: string) => void;
}

export default function DataTable<T>({
  title,
  data,
  columns,
  initialPageSize = 10,
  globalSearchPlaceholder = "Search…",
  toolbarActions,
  onSearchChange,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [pageSize, setPageSize] = useState(initialPageSize);
  const [pageIndex, setPageIndex] = useState(0);

  const table = useReactTable({
    data,
    columns,
    state: {
      sorting,
      globalFilter,
      pagination: { pageIndex, pageSize },
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: (v) => {
      setGlobalFilter(v);
      setPageIndex(0);
      onSearchChange?.(v);
    },
    onPaginationChange: (updater) => {
      const next =
        typeof updater === "function"
          ? updater({ pageIndex, pageSize })
          : updater;
      setPageIndex(next.pageIndex);
      setPageSize(next.pageSize);
    },
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  const rows = useMemo(() => table.getRowModel().rows, [table, data, sorting, globalFilter, pageIndex, pageSize]);

  return (
    <Paper variant="outlined" sx={{ overflow: "hidden" }}>
      {(title || toolbarActions || globalSearchPlaceholder) && (
        <Stack
          direction={{ xs: "column", sm: "row" }}
          alignItems={{ sm: "center" }}
          justifyContent="space-between"
          spacing={1.5}
          sx={{ p: 1.5, borderBottom: 1, borderColor: "divider" }}
        >
          {title && (
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
              {title}
            </Typography>
          )}
          <Stack direction="row" spacing={1} alignItems="center" sx={{ ml: "auto" }}>
            <TextField
              size="small"
              placeholder={globalSearchPlaceholder}
              value={globalFilter}
              onChange={(e) => table.setGlobalFilter(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
              sx={{ minWidth: 220 }}
            />
            {toolbarActions}
          </Stack>
        </Stack>
      )}

      <TableContainer sx={{ maxWidth: "100%", overflowX: "auto" }}>
        <Table size="small" stickyHeader>
          <TableHead>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id}>
                {hg.headers.map((header) => (
                  <TableCell
                    key={header.id}
                    sx={{
                      fontWeight: 700,
                      whiteSpace: "nowrap",
                      bgcolor: "background.paper",
                      textTransform: "uppercase",
                      fontSize: "0.72rem",
                      letterSpacing: 0.4,
                      color: "text.secondary",
                      borderBottom: "2px solid",
                      borderColor: "divider",
                    }}
                    style={{ width: header.getSize() }}
                  >
                    {header.isPlaceholder ? null : header.column.getCanSort() ? (
                      <TableSortLabel
                        active={!!header.column.getIsSorted()}
                        direction={header.column.getIsSorted() || "asc"}
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </TableSortLabel>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableHead>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} sx={{ textAlign: "center", py: 4 }}>
                  <Typography variant="body2" sx={{ color: "text.secondary" }}>
                    No rows to show.
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => (
                <TableRow key={row.id} hover>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} style={{ width: cell.column.getSize() }}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Box sx={{ borderTop: 1, borderColor: "divider" }}>
        <TablePagination
          component="div"
          count={table.getFilteredRowModel().rows.length}
          page={pageIndex}
          onPageChange={(_, p) => setPageIndex(p)}
          rowsPerPage={pageSize}
          onRowsPerPageChange={(e) => {
            setPageSize(Number(e.target.value));
            setPageIndex(0);
          }}
          rowsPerPageOptions={[10, 20, 50, 100]}
        />
      </Box>
    </Paper>
  );
}
