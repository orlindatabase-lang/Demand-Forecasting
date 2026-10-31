import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Box, CssBaseline, ThemeProvider, createTheme } from "@mui/material";

import CategoryAnalysis from "@/pages/CategoryAnalysis";
import ProductionLog from "@/pages/ProductionLog";
import DesignSuggestions from "@/pages/DesignSuggestions";
import WeeklySalesGrid, { SharedOverview } from "@/pages/WeeklySalesGrid";
import { ApiError } from "@/services/api";
import { COLORS } from "@/theme/colors";

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
      refetchOnReconnect: false,   // fetch only when the user asks (no automatic re-fetching)
    },
  },
});

// Same look as the Orlin portal theme, for every page (2026-10-29, user-requested:
// the Weekly Sales Report's look everywhere in the UI)
const portalTheme = createTheme({
  palette: {
    mode: "light",
    primary: { main: COLORS.primary, light: COLORS.primaryHover, dark: COLORS.primaryDark, contrastText: "#ffffff" },
    background: { default: COLORS.bgDefault, paper: COLORS.bgWhite },
    text: { primary: COLORS.textPrimary, secondary: COLORS.textSecondary },
    divider: COLORS.border,
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    fontSize: 13,
    body1: { fontSize: "0.8125rem" },
    body2: { fontSize: "0.75rem" },
    caption: { fontSize: "0.6875rem" },
    button: { textTransform: "none", fontWeight: 600, fontSize: "0.8125rem" },
  },
  components: {
    MuiButton: { styleOverrides: { root: { textTransform: "none", borderRadius: 8, fontWeight: 600, boxShadow: "none" } } },
    MuiTab: { styleOverrides: { root: { textTransform: "none", fontWeight: 600, fontSize: "0.8125rem" } } },
    // Row lines as light as the portal tables
    MuiTableCell: { styleOverrides: { root: { borderBottomColor: COLORS.bgMuted } } },
    MuiTextField: { defaultProps: { size: "small" } },
    MuiSelect: { defaultProps: { size: "small" } },
  },
});

/** Forecast overview (summary + festival/sale cards) and the tab bar above a
 * page. The Weekly Sales Report renders its own, filter-aware version. */
function WithOverview({ children }: { children: ReactNode }) {
  return (
    <>
      <SharedOverview />
      {children}
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={portalTheme}>
        <CssBaseline />
        <BrowserRouter>
          <Box sx={{ display: "flex" }}>
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>
              <Box component="main" sx={{ p: { xs: 1.5, sm: 2, md: 3 }, maxWidth: "100vw", overflowX: "clip" }}>
                <Routes>
                  <Route path="/" element={<WeeklySalesGrid />} />
                  <Route path="/analysis/sub-category" element={<WithOverview><CategoryAnalysis key="subCategory" level="subCategory" /></WithOverview>} />
                  <Route path="/analysis/category" element={<WithOverview><CategoryAnalysis key="category" level="category" /></WithOverview>} />
                  <Route path="/analysis/tiers" element={<WithOverview><CategoryAnalysis key="tier" level="tier" /></WithOverview>} />
                  <Route path="/production-log" element={<WithOverview><ProductionLog /></WithOverview>} />
                  <Route path="/design-suggestions" element={<WithOverview><DesignSuggestions /></WithOverview>} />
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
