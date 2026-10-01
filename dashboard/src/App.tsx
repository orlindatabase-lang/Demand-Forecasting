import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Box, CssBaseline, ThemeProvider, createTheme } from "@mui/material";

import InventoryPlanning from "@/pages/InventoryPlanning";
import WeeklySalesGrid from "@/pages/WeeklySalesGrid";
import { ApiError } from "@/services/api";

const isLoadingResponse = (error: unknown) => error instanceof ApiError && error.status === 503;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 503 = the API is still loading its data after a (re)start, which can
      // take several minutes - keep waiting (up to ~15 min) instead of
      // giving up after the normal 10 retries.
      retry: (failureCount, error) => failureCount < (isLoadingResponse(error) ? 180 : 10),
      retryDelay: (attempt, error) =>
        isLoadingResponse(error) ? 5_000 : Math.min(1_000 * 2 ** attempt, 30_000),
      refetchOnWindowFocus: false,
    },
  },
});

const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#312c5c" },
    background: { default: "#f6f7fa", paper: "#ffffff" },
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: '"Segoe UI", Roboto, system-ui, sans-serif',
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <BrowserRouter>
          <Box sx={{ display: "flex" }}>
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>
              <Box component="main" sx={{ p: 3 }}>
                <Routes>
                  <Route path="/" element={<WeeklySalesGrid />} />
                  <Route path="/inventory" element={<InventoryPlanning />} />
                  <Route path="/reports/weekly-sales" element={<WeeklySalesGrid />} />
                </Routes>
              </Box>
            </Box>
          </Box>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
