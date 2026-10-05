import { Link, useLocation } from "react-router-dom";
import { Tab, Tabs } from "@mui/material";

// One tab per page, in this order. "/" and "/reports/weekly-sales" both show
// the Weekly Sales Report.
const PAGES = [
  { path: "/", label: "Weekly Sales Report" },
  { path: "/analysis/sub-category", label: "Sub Category" },
  { path: "/analysis/category", label: "Category" },
  { path: "/analysis/tiers", label: "Tiers Spike Rate" },
  { path: "/production-log", label: "Production Log" },
];

/** App-wide tab bar, shown right below the forecast overview cards. */
export default function PageTabs() {
  const { pathname } = useLocation();
  const current = PAGES.find((p) => p.path !== "/" && pathname.startsWith(p.path))?.path ?? "/";
  return (
    <Tabs value={current} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }} variant="scrollable" scrollButtons="auto">
      {PAGES.map((p) => (
        <Tab key={p.path} value={p.path} label={p.label} component={Link} to={p.path} sx={{ fontWeight: 700 }} />
      ))}
    </Tabs>
  );
}
