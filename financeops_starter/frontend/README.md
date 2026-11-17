# FinanceOps owner console (Milestones 4–5)

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
npx playwright install chromium   # once: ~100 MB test browser
npm run e2e            # build, then Playwright drives the real backend (own temp DB, port 8765)
```

## Pages

Hash routes (`#/…`), so FastAPI only ever serves `/app`. Old `#case=<id>`
links redirect to the case page.

| Route | Page | Backend |
|---|---|---|
| `#/` | **Needs you**: KPI strip, approval queue (approve/reject/execute; keys `j`/`k`, `a`, `r`, each with a confirm), escalations with Resolve, latest briefing | `/attention`, `/summary`, `/briefings/latest` |
| `#/cases` | Searchable, sortable, filterable, paged case table | `/cases?state=&q=&sort=&limit=&cursor=` |
| `#/cases/:id` | Case detail: header with amounts, tabs Overview / Evidence / Timeline / Messages / Agent runs, sticky decision bar, autonomy decisions, escalations | `/cases/{id}/workspace` |
| `#/activity` | Live feed of what the system did | `/activity?after=` |
| `#/agents` | Each agent's runs and outcomes; pause/resume | `/agents` |
| `#/policy` | Approval window, reminders, autonomy rules, cadence, model budget, preview | `/policy`, `/policy/preview` |
| `#/demo` | Demo Lab: scenarios, generator, simulated clock (play/pause/speed), worker run, simulated inbox | `/demo/*` |

The header holds the owner token and the autonomy kill switch
(`/autonomy/pause|resume`, with a confirm). Every page shows the
"Synthetic data · simulated delivery" banner.

## How it stays live

- `@tanstack/react-query` fetches and caches everything (`src/api/client.ts`
  is the typed fetch wrapper).
- `/activity` and `/summary` are polled about every 3 s while the tab is
  visible; new activity invalidates the queries it affects and shows a toast
  for anything that needs the owner. No server push yet.

## Rules this console keeps

- Errors become `ApiError`: 401 → back to token entry; 409 → "This case
  changed — here is the latest" and a refetch, never a blind retry; 422 → the
  server's detail; 503 "Not built yet" → a notice, the page keeps working.
- Approve sends the exact `payload_hash` the owner was shown.
- Evidence, replies, briefings and model text render as plain text (no
  `innerHTML`, no `dangerouslySetInnerHTML`).
- "Investigate with AI" calls a paid model and always needs a confirm click.
  The demo worker always sends `run_investigations: false`; e2e tests never
  click it.
- The owner token lives in memory and sessionStorage: local demo only.
- `.npmrc` sets `legacy-peer-deps` because npm 10 crashes resolving Vitest's
  optional peers; every peer we need is listed in package.json.

## Tests

- `src/pages/*.test.tsx` and `src/App.test.tsx`: mocked fetch per page,
  including 401, 409, 503 not-built, polling and keyboard approval.
- `e2e/owner-flow.spec.ts`: a real browser loads a scenario, proposes,
  approves the exact message, queues it and checks the simulated inbox; and
  checks a malicious document renders as plain text. No paid model calls.

Known gaps (contract, not UI): `/activity` reads forward only, so the first
load pages through history (capped); there is no "next step" field (Overview
shows scheduled jobs); rules and the case list carry no currency (CAD assumed).
