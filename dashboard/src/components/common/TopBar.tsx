import { AppBar, Avatar, Box, IconButton, Stack, Toolbar, Typography } from "@mui/material";
import MenuOpenIcon from "@mui/icons-material/MenuOpen";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

export default function TopBar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  return (
    <AppBar
      position="sticky"
      elevation={0}
      sx={{
        bgcolor: "background.paper",
        color: "text.primary",
        borderBottom: 1,
        borderColor: "divider",
      }}
    >
      <Toolbar sx={{ justifyContent: "space-between" }}>
        <IconButton onClick={onToggleSidebar} size="small">
          <MenuOpenIcon />
        </IconButton>

        <Stack direction="row" alignItems="center" spacing={1}>
          <Avatar sx={{ width: 34, height: 34, bgcolor: "#1c1a35", fontSize: "0.8rem", fontWeight: 700 }}>
            OA
          </Avatar>
          <Box sx={{ textAlign: "left" }}>
            <Typography variant="body2" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
              Orlin Apparel
            </Typography>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              User
            </Typography>
          </Box>
          <ExpandMoreIcon fontSize="small" sx={{ color: "text.secondary" }} />
        </Stack>
      </Toolbar>
    </AppBar>
  );
}
