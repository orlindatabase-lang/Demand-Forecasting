import { Box } from "@mui/material";
import PageHeader from "@/components/common/PageHeader";
import BottleneckPanel from "@/components/common/BottleneckPanel";

export default function Bottleneck() {
  return (
    <Box>
      <PageHeader
        title="Bottleneck Detection"
        subtitle="Which process is holding up production right now — Inhouse + Job Work combined"
      />
      <BottleneckPanel />
    </Box>
  );
}
