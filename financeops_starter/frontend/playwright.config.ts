import { defineConfig, devices } from '@playwright/test';

// End-to-end: the built app (npm run build) served by the real FastAPI backend.
// Each run starts uvicorn on its own port with a fresh temporary database,
// demo routes on and a throwaway owner token. No paid model is ever called.
const port = 8765;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1, // one SQLite database per run, so tests run one after another
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
  },
  // PLAYWRIGHT_CHANNEL=msedge (or chrome) uses an installed browser instead of
  // Playwright's download, e.g. behind a corporate proxy that blocks it.
  projects: [{
    name: 'chromium',
    use: { ...devices['Desktop Chrome'], channel: process.env.PLAYWRIGHT_CHANNEL || undefined },
  }],
  webServer: {
    // Cross-platform launcher (Windows, macOS, Linux): fresh temp DB, demo routes on,
    // throwaway owner token, uvicorn from ../.venv (Scripts\ on Windows, bin/ elsewhere).
    command: `node e2e/start-server.mjs ${port}`,
    url: `http://127.0.0.1:${port}/app/`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
