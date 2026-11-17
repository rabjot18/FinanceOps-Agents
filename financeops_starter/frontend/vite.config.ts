import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// The API lives at the site root (same origin as the built app, which FastAPI
// serves under /app). In development, Vite forwards these paths to uvicorn.
const backend = 'http://127.0.0.1:8000';
const apiPrefixes = ['/cases', '/actions', '/policy', '/invoices', '/agent-runs', '/demo'];

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(apiPrefixes.map((prefix) => [prefix, { target: backend }])),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
