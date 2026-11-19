// Starts the real FastAPI backend for the browser tests, on any OS: a fresh
// temporary database, demo routes on, a throwaway owner token. Playwright
// runs this as its webServer command and stops it when the tests end.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const port = process.argv[2] ?? '8765';
const here = path.dirname(fileURLToPath(import.meta.url));
const starter = path.resolve(here, '..', '..'); // financeops_starter
const uvicorn =
  process.platform === 'win32'
    ? path.join(starter, '.venv', 'Scripts', 'uvicorn.exe')
    : path.join(starter, '.venv', 'bin', 'uvicorn');

if (!fs.existsSync(uvicorn)) {
  console.error(`uvicorn not found at ${uvicorn}. Create the Python venv in financeops_starter first.`);
  process.exit(1);
}

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fo-e2e-'));
const env = {
  ...process.env,
  FINANCEOPS_DB: path.join(dir, 'e2e.sqlite3'),
  FINANCEOPS_ENABLE_DEMO: '1',
  FINANCEOPS_OWNER_TOKEN: 'e2e-owner-token',
};

const child = spawn(uvicorn, ['api:create_app', '--factory', '--host', '127.0.0.1', '--port', port], {
  cwd: starter,
  env,
  stdio: 'inherit',
});

// Pass Playwright's stop signal on, and leave with the server's exit code.
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal));
}
child.on('error', (err) => {
  console.error(`Could not start uvicorn: ${err.message}`);
  process.exit(1);
});
child.on('exit', (code, signal) => {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    // SQLite may still hold the file on Windows; the OS temp cleaner gets it later.
  }
  process.exit(code ?? (signal ? 1 : 0));
});
