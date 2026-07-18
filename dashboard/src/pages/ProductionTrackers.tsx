import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Box, Tab, Tabs } from "@mui/material";
import PageHeader from "@/components/common/PageHeader";
import InhousePanel from "@/components/common/InhousePanel";
import JobWorkPanel from "@/components/common/JobWorkPanel";
import PurchaseOrderPanel from "@/components/common/PurchaseOrderPanel";
import EmbroideryPanel from "@/components/common/EmbroideryPanel";
import FOBPanel from "@/components/common/FOBPanel";

const TAB_LABELS = ["Inhouse", "Job Work", "Purchase Order", "Embroidery", "FOB"];

function tabIndexFromLabel(label: string | null): number {
  if (!label) return 0;
  const idx = TAB_LABELS.findIndex((l) => l.toLowerCase() === label.toLowerCase());
  return idx >= 0 ? idx : 0;
}

export default function ProductionTrackers() {
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const processParam = searchParams.get("process") ?? undefined;

  const [tab, setTab] = useState(() => tabIndexFromLabel(tabParam));

  // A link from Bottleneck Detection to a DIFFERENT process while this page is
  // already mounted only changes the URL's query string (same route), which
  // doesn't remount the component — re-sync the active tab when that happens.
  useEffect(() => {
    if (tabParam) setTab(tabIndexFromLabel(tabParam));
  }, [tabParam]);

  const TABS = [
    { label: "Inhouse", panel: <InhousePanel initialProcess={tabParam?.toLowerCase() === "inhouse" ? processParam : undefined} /> },
    { label: "Job Work", panel: <JobWorkPanel initialProcess={tabParam?.toLowerCase() === "job work" ? processParam : undefined} /> },
    { label: "Purchase Order", panel: <PurchaseOrderPanel /> },
    { label: "Embroidery", panel: <EmbroideryPanel /> },
    { label: "FOB", panel: <FOBPanel /> },
  ];

  return (
    <Box>
      <PageHeader
        title="Production Trackers"
        subtitle="Inhouse · Job Work · Purchase Order · Embroidery · FOB — with AI delay-risk scoring"
      />
      <Box sx={{ borderBottom: 1, borderColor: "divider", bgcolor: "background.paper", borderRadius: "8px 8px 0 0", px: 1 }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" scrollButtons="auto">
          {TABS.map((t) => (
            <Tab key={t.label} label={t.label} sx={{ textTransform: "none", fontWeight: 600 }} />
          ))}
        </Tabs>
      </Box>
      {TABS[tab].panel}
    </Box>
  );
}
