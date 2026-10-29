import { Box, Stack, Typography } from "@mui/material";
import type { ReactNode } from "react";
import { COLORS } from "@/theme/colors";

interface PageHeaderProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}

export default function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      // One row on every screen (title left, actions right), wrapping only if the actions don't fit
      sx={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", rowGap: 1, mb: 1.5 }}
    >
      <Box>
        {/* Page title: 15px / 700 / #111827, as in the portal. */}
        <Typography sx={{ fontSize: "15px", fontWeight: 700, lineHeight: 1.4, color: COLORS.textPrimary }}>
          {title}
        </Typography>
        {subtitle && (
          <Typography variant="body2" sx={{ color: "text.secondary", mt: 0.25 }}>
            {subtitle}
          </Typography>
        )}
      </Box>
      {actions}
    </Stack>
  );
}
