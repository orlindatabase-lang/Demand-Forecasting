import { useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import CloseIcon from "@mui/icons-material/Close";
import { ABOUT, type AboutContent, type AboutKey } from "./aboutContent";

const ACCENT = "#312c5c";

/** Small "About" button next to a Design Suggestions section title; opens a
 * dialog explaining what the section shows, how it is worked out and how to use it.
 * ``content`` = text built for one item (e.g. a product card) instead of a fixed topic. */
export default function AboutButton({ topic, content }: { topic?: AboutKey; content?: AboutContent }) {
  const [open, setOpen] = useState(false);
  const about = content ?? ABOUT[topic ?? "products"];
  return (
    <>
      <Button
        size="small"
        onClick={() => setOpen(true)}
        startIcon={<InfoOutlinedIcon sx={{ fontSize: "16px !important" }} />}
        aria-label={`About ${about.title}`}
        sx={{
          minWidth: 0, px: 0.75, py: 0, ml: 0.5, height: 22, fontSize: "0.7rem", fontWeight: 700,
          textTransform: "none", color: "text.secondary", "&:hover": { color: ACCENT, bgcolor: "rgba(49,44,92,0.06)" },
        }}
      >
        About
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ pr: 6, fontWeight: 800, color: ACCENT }}>
          {about.title}
          <IconButton aria-label="Close" onClick={() => setOpen(false)} sx={{ position: "absolute", right: 8, top: 8 }}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2}>
            {about.sections.map((s) => (
              <Box key={s.heading}>
                <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 0.5 }}>{s.heading}</Typography>
                <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                  {s.points.map((p) => (
                    <Typography component="li" key={p} variant="body2" sx={{ mb: 0.5, lineHeight: 1.5 }}>{p}</Typography>
                  ))}
                </Box>
              </Box>
            ))}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} sx={{ fontWeight: 700 }}>Got it</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
