import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The build goes to dashboard/static/react so Flask can serve it at /app.
export default defineConfig({
  plugins: [react()],
  base: '/static/react/',
  build: { outDir: '../static/react', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:5000',
      '/report': 'http://127.0.0.1:5000',
    },
  },
})