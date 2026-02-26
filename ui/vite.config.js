import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../app/static',
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    proxy: {
      '/receipt': 'http://localhost:8000',
      '/search': 'http://localhost:8000',
      '/cs': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/audit': 'http://localhost:8000',
    },
  },
})
