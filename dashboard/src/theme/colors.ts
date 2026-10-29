// ─── Single source of truth for app colors (same palette as the Orlin portal) ─
// Change a value here → it updates everywhere it is used.

export const COLORS = {
  // Brand blue
  primary:      '#272d4f',
  primaryHover: '#545b91',
  primaryDark:  '#272d4f',
  primaryBg:      '#dbdff2',
  primaryBgLight: '#eef0fa',
  primaryBgSubtle: '#f6f7fc',

  // Page & surface backgrounds
  bgDefault: '#f9fafb',
  bgMuted:   '#f3f4f6',
  bgSection: '#f3f5f9',
  bgWhite:   '#ffffff',

  // Text
  textPrimary:   '#111827',
  textDark:      '#374151',
  textSecondary: '#6b7280',
  textMuted:     '#9ca3af',

  // Borders
  border:     '#e5e7eb',
  borderDark: '#d1d5db',

  // Status — success
  successText: '#166534',
  successBg:   '#dcfce7',
  successDot:  '#16a34a',

  // Status — error
  errorText: '#dc2626',
  errorBg:   '#fef2f2',

  // Status — warning
  warningText: '#92400e',
  warningBg:   '#fef3c7',
  warningBgFaint: '#fffbeb',

  // Status — in transit
  transitText: '#6d28d9',
  transitBg:   '#f3e8ff',

  // Status — info
  infoText:    '#1e40af',
  infoBg:      '#dbeafe',
  infoAccent:  '#0369a1',
  infoBgLight: '#eff6ff',

  // Subtle row hover (tables)
  bgHover: '#fafafa',

  // Sticky column highlight (identity columns in forecast tables)
  stickyColHeader: '#f5f9ff',
  stickyColCell:   '#fafcff',
  stageCol: '#f5f9ff',

  // Avatar palette — cycled by first char of name
  avatarPalette: ['#2d5016', '#0369a1', '#7c3aed', '#b45309', '#0f766e'],
}

// ─── Weekly Sales Report table look, shared by every page's tables ─────────
// (2026-10-29, user-requested: the report's UI everywhere). Values copied from
// pages/WeeklySalesGrid.tsx so all tables read the same.
export const REPORT = {
  actual: '#312c5c',        // sold / actual numbers, tier chips
  forecast: '#f97316',      // forecast accent (card borders, chart lines)
  forecastText: '#ea580c',  // forecast numbers under each actual
  headBg: COLORS.stickyColHeader,  // table header + pinned TOTAL row
  totalBg: COLORS.bgMuted,         // month TOTAL columns
  summaryBg: COLORS.warningBgFaint, // right-hand summary column (Suggested Production)
  rowAltBg: '#f8fafc',      // expanded sub-rows
  festival: '#d97706',
  sale: '#2563eb',
  both: '#be185d',
  festivalTint: '#fff7e6',
  saleTint: '#eef4ff',
  bothTint: '#fdf0f6',
  good: '#16a34a',
  bad: '#dc2626',
}

// Header cell: small grey uppercase label on the pale blue header, 2px rule below
export const REPORT_HEAD_SX = {
  bgcolor: REPORT.headBg,
  fontWeight: 700,
  textTransform: 'uppercase',
  fontSize: '0.72rem',
  letterSpacing: 0.4,
  color: 'text.secondary',
  borderBottom: `2px solid ${COLORS.borderDark}`,
} as const

// Month / group title in the header's first row
export const REPORT_GROUP_HEAD_SX = {
  bgcolor: REPORT.headBg,
  fontWeight: 700,
  fontSize: '0.78rem',
  color: COLORS.primary,
} as const

// Pinned TOTAL row cell
export const REPORT_TOTAL_SX = {
  bgcolor: REPORT.headBg,
  borderTop: `2px solid ${COLORS.borderDark}`,
} as const

// Right edge of a pinned first column (inset shadow, as in the report)
export const STICKY_EDGE_SX = {
  borderRight: `2px solid ${COLORS.borderDark}`,
  boxShadow: 'inset -4px 0 6px -4px rgba(0, 0, 0, 0.18)',
} as const

export function eventAccent(category: string): string | undefined {
  if (category === 'festival') return REPORT.festival
  if (category === 'sale') return REPORT.sale
  if (category === 'both') return REPORT.both
  return undefined
}

export function eventTintOf(category: string): string | undefined {
  if (category === 'festival') return REPORT.festivalTint
  if (category === 'sale') return REPORT.saleTint
  if (category === 'both') return REPORT.bothTint
  return undefined
}
