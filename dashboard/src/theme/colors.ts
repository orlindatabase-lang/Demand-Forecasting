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
