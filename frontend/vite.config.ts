import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const API = 'http://127.0.0.1:8000'

// The dev server forwards /api to the API service, so the browser sees one
// origin and no cross-origin rules apply.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: API, changeOrigin: true },
    },
  },
})
