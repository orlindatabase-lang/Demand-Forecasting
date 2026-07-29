import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Box,
  Collapse,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
} from "@mui/material";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import HomeIcon from "@mui/icons-material/Home";
import FactoryIcon from "@mui/icons-material/Factory";

export const DRAWER_WIDTH = 260;
const SIDEBAR_BG = "#1c1a35";
const SIDEBAR_BG_ACTIVE = "#2c2a52";
const SIDEBAR_TEXT = "#b7b4cf";
const SIDEBAR_TEXT_ACTIVE = "#ffffff";

interface NavLeaf {
  to: string;
  label: string;
  end?: boolean;
}

interface NavSection {
  label: string;
  icon: React.ReactNode;
  items: NavLeaf[];
}

const SECTIONS: NavSection[] = [
  {
    label: "Production",
    icon: <FactoryIcon fontSize="small" />,
    items: [
      { to: "/bottleneck", label: "Bottleneck Detection" },
      { to: "/inventory", label: "Inventory Planning" },
    ],
  },
];

function SectionHeader({
  icon,
  label,
  open,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  open: boolean;
  onClick: () => void;
}) {
  return (
    <ListItemButton onClick={onClick} sx={{ color: SIDEBAR_TEXT_ACTIVE, py: 1 }}>
      <ListItemIcon sx={{ minWidth: 36, color: SIDEBAR_TEXT_ACTIVE }}>{icon}</ListItemIcon>
      <ListItemText primary={label} primaryTypographyProps={{ fontWeight: 700, fontSize: "0.88rem" }} />
      {open ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
    </ListItemButton>
  );
}

export default function Sidebar({ open }: { open: boolean }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ Production: true });

  return (
    <Drawer
      variant="persistent"
      open={open}
      sx={{
        width: open ? DRAWER_WIDTH : 0,
        flexShrink: 0,
        transition: "width 0.2s",
        [`& .MuiDrawer-paper`]: {
          width: DRAWER_WIDTH,
          boxSizing: "border-box",
          bgcolor: SIDEBAR_BG,
          borderRight: "none",
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      <Box sx={{ px: 3, py: 3, display: "flex", alignItems: "center", gap: 1.5 }}>
        <Box
          sx={{
            width: 34,
            height: 34,
            borderRadius: "50%",
            border: `1.5px solid ${SIDEBAR_TEXT_ACTIVE}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Typography sx={{ color: SIDEBAR_TEXT_ACTIVE, fontWeight: 800, fontSize: "1rem" }}>O</Typography>
        </Box>
        <Box>
          <Typography sx={{ color: SIDEBAR_TEXT_ACTIVE, fontWeight: 700, fontSize: "1.15rem", lineHeight: 1.1 }}>
            Orlin
          </Typography>
          <Typography sx={{ color: SIDEBAR_TEXT, fontSize: "0.62rem", letterSpacing: 2 }}>APPAREL</Typography>
        </Box>
      </Box>

      <Typography sx={{ color: SIDEBAR_TEXT, fontSize: "0.68rem", letterSpacing: 1.5, px: 3, pb: 1, fontWeight: 700 }}>
        NAVIGATION
      </Typography>

      <List sx={{ flex: 1, px: 1 }}>
        <ListItemButton
          component={NavLink}
          to="/"
          end
          sx={{
            color: SIDEBAR_TEXT,
            borderRadius: 1.5,
            mb: 0.5,
            "&.active": { bgcolor: SIDEBAR_BG_ACTIVE, color: SIDEBAR_TEXT_ACTIVE },
            "&:hover": { bgcolor: SIDEBAR_BG_ACTIVE },
          }}
        >
          <ListItemIcon sx={{ minWidth: 36, color: "inherit" }}>
            <HomeIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="Demand Forecasting" primaryTypographyProps={{ fontWeight: 600, fontSize: "0.88rem" }} />
        </ListItemButton>

        {SECTIONS.map((section) => (
          <Box key={section.label} sx={{ mb: 0.5 }}>
            <SectionHeader
              icon={section.icon}
              label={section.label}
              open={expanded[section.label]}
              onClick={() => setExpanded((e) => ({ ...e, [section.label]: !e[section.label] }))}
            />
            <Collapse in={expanded[section.label]} timeout="auto">
              <List component="div" disablePadding>
                {section.items.map((item) => (
                  <ListItemButton
                    key={item.to}
                    component={NavLink}
                    to={item.to}
                    end={item.end}
                    sx={{
                      pl: 5.5,
                      py: 0.75,
                      color: SIDEBAR_TEXT,
                      borderRadius: 1.5,
                      mx: 1,
                      width: "auto",
                      "&.active": { bgcolor: SIDEBAR_BG_ACTIVE, color: SIDEBAR_TEXT_ACTIVE },
                      "&:hover": { bgcolor: SIDEBAR_BG_ACTIVE },
                    }}
                  >
                    <ListItemText primary={item.label} primaryTypographyProps={{ fontSize: "0.84rem", fontWeight: 500 }} />
                  </ListItemButton>
                ))}
              </List>
            </Collapse>
          </Box>
        ))}
      </List>

      <Typography sx={{ color: SIDEBAR_TEXT, opacity: 0.5, fontSize: "0.68rem", textAlign: "center", py: 2 }}>
        Orlin Apparel Internal Portal
      </Typography>
    </Drawer>
  );
}
