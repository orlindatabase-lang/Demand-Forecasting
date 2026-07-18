import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Box, CssBaseline, ThemeProvider, createTheme } from "@mui/material";

import Sidebar, { DRAWER_WIDTH } from "@/components/common/Sidebar";
import TopBar from "@/components/common/TopBar";
import Dashboard from "@/pages/Dashboard";
import InventoryPlanning from "@/pages/InventoryPlanning";
import Bottleneck from "@/pages/Bottleneck";
import ProductionTrackers from "@/pages/ProductionTrackers";
import VerticalPerformance from "@/pages/VerticalPerformance";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 10,
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
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <BrowserRouter>
          <Box sx={{ display: "flex" }}>
            <Sidebar open={sidebarOpen} />
            <Box
              sx={{
                flexGrow: 1,
                minWidth: 0,
                transition: "margin 0.2s",
                ml: sidebarOpen ? 0 : `-${DRAWER_WIDTH}px`,
              }}
            >
              <TopBar onToggleSidebar={() => setSidebarOpen((o) => !o)} />
              <Box component="main" sx={{ p: 3 }}>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/verticals" element={<VerticalPerformance />} />
                  <Route path="/inventory" element={<InventoryPlanning />} />
                  <Route path="/bottleneck" element={<Bottleneck />} />
                  <Route path="/production" element={<ProductionTrackers />} />
                </Routes>
              </Box>
            </Box>
          </Box>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
