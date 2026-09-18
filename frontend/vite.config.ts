import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// VITE_API_TARGET lets the dev server proxy to a container ("http://api:8080")
// instead of localhost. It is read here, at config time, because the proxy runs
// in the dev server process rather than in the browser.
const apiTarget = process.env.VITE_API_TARGET || 'http://localhost:8080'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
