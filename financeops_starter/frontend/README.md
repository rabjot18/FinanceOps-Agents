# FinanceOps owner console (Milestone 4)

React + TypeScript + Vite. It only shows what the backend computes; it never
re-derives amounts, states or approval checks. All data is synthetic and all
delivery is simulated.

```bash
npm ci                 # install exact versions from package-lock.json
npm run gen:api        # regenerate src/api/schema.d.ts from ../contracts/openapi.json
npm run typecheck      # tsc --noEmit (strict)
npm test               # Vitest + React Testing Library, mocked fetch
npm run build          # writes dist/, which FastAPI serves at /app
npm run dev            # http://localhost:5173/app/ ; API calls proxy to 127.0.0.1:8000
```

- `src/api/client.ts`: small typed fetch wrapper; errors become `ApiError`
  (401 → back to token entry, 409 → "this case changed — review again" and a
  refetch, 422 → the server's detail, network → retry).
- The owner token lives in memory and sessionStorage: local demo only.
- Evidence, replies and model text render as plain text (no `innerHTML`).
- "Investigate with AI" calls a paid model and always needs a confirm click.
  The demo worker always sends `run_investigations: false`.
- `.npmrc` sets `legacy-peer-deps` because npm 10 crashes resolving Vitest's
  optional peers; every peer we need is listed in package.json.
