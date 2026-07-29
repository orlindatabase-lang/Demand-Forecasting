import { Box } from "@mui/material";
import { formatDateRange } from "@/utils/format";

interface FestivalSpikeBadgeProps {
  event: string;
  eventStart: string;
  eventEnd: string;
  qty: number;
  upliftPct: number;
}

export default function FestivalSpikeBadge({ event, eventStart, eventEnd, qty, upliftPct }: FestivalSpikeBadgeProps) {
  return (
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
      🎉 {event} · {formatDateRange(eventStart, eventEnd)} · {qty} units (+{upliftPct}%)
    </Box>
  );
}
