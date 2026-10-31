import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' so the built app also works when FastAPI serves it at /ui/
export default defineConfig({
  plugins: [react()],
  base: './',
  server: { port: 5173, host: true },
})
