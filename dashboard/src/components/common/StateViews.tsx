import { Box, Skeleton, Stack, Typography } from "@mui/material";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlineOutlined";

export function LoadingSkeleton({ variant = "table" }: { variant?: "table" | "page" }) {
  const rows = variant === "page" ? 8 : 6;
  return (
    <Box>
      {variant === "page" && (
        <>
          <Skeleton variant="text" width={260} height={40} sx={{ mb: 0.5 }} />
          <Skeleton variant="text" width={380} height={24} sx={{ mb: 2 }} />
        </>
      )}
      <Stack spacing={1.25}>
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} variant="rounded" height={38} />
        ))}
      </Stack>
    </Box>
  );
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        py: 8,
        color: "text.secondary",
      }}
    >
      <ErrorOutlineIcon sx={{ fontSize: 40, mb: 1, color: "#ef4444" }} />
      <Typography variant="body1" sx={{ fontWeight: 600 }}>
        Something went wrong.
      </Typography>
      {message && (
        <Typography variant="body2" sx={{ mt: 0.5 }}>
          {message}
        </Typography>
      )}
      {onRetry && (
        <Typography
          variant="body2"
          onClick={onRetry}
          sx={{ mt: 1.5, color: "primary.main", cursor: "pointer", fontWeight: 600 }}
        >
          Retry
        </Typography>
      )}
    </Box>
  );
}
