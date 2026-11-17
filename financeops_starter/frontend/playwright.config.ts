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
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command:
      'cd .. && FINANCEOPS_DB="$(mktemp -d)/e2e.sqlite3" FINANCEOPS_ENABLE_DEMO=1 ' +
      'FINANCEOPS_OWNER_TOKEN=e2e-owner-token ' +
      `exec .venv/bin/uvicorn api:create_app --factory --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}/app/`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
