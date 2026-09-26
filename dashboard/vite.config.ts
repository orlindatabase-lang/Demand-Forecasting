import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // Bind to all interfaces (not just loopback) so colleagues on the same
  // office network can reach this dev server via this machine's LAN IP.
  server: {
    host: true,
    port: 5180,
  },
})
