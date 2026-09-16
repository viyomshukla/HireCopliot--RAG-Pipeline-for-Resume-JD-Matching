import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Port 3000 is not a preference, it is a requirement.
// The backend's CORS middleware (backend/api/main.py) allows exactly
// http://localhost:3000 and http://127.0.0.1:3000. Vite's own default of 5173
// is NOT in that list, so serving from it makes every request fail as an opaque
// "Failed to fetch" with nothing useful in the console. strictPort makes the
// dev server refuse to start on a fallback port rather than start on 3001 and
// break the API silently.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 3000, strictPort: true },
  preview: { port: 3000, strictPort: true },
})
