# FinanceOps owner console (Milestones 4–6)

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

The sidebar has three primary pages; the rest of the console sits under a
collapsible **Advanced** group (it opens by itself on an advanced route).

| Route | Page | Backend |
|---|---|---|
| `#/` | **Flow** (default): a React Flow board, one node per stage in the order the backend sends (side stages Needs you and Disputed below), case chips (customer + outstanding, up to 4, "+N more"), each edge labelled with the actor who moves cases there (most frequent in `recent`, else a fixed default), edges into a stage with a new step since the last poll animate. Agent team legend, Latest steps, and a drawer per case: its path, the exact message (with "Written by Collections agent" when `written_by` is `agent`) and the customer's reply as untrusted plain text. Empty: a big "Run the month" | `/flow`, `/cases/{id}/flow`, `/cases/{id}/workspace` |
| `#/decisions` | **Your decisions**: escalations lead with the agent's recommendation, then question, customer, amount, evidence and Resolve; approvals show the full exact message with Approve (exact hash) / Reject / Execute | `/attention` |
| `#/outcomes` | **Outcomes**: collected of invoiced, cases closed, closed without you, your decisions, avg days to pay, unauthorized actions (green at 0, red above); agent work counts; spend vs caps; the reporter's summary; per-customer results with the hidden persona and ✓/✗/in progress | `/outcomes` |
| `#/home` | Advanced · **Needs you**: KPI strip, approval queue (approve/reject/execute; keys `j`/`k`, `a`, `r`, each with a confirm), escalations with Resolve, latest briefing | `/attention`, `/summary`, `/briefings/latest` |
| `#/cases` | Searchable, sortable, filterable, paged case table | `/cases?state=&q=&sort=&limit=&cursor=` |
| `#/cases/:id` | Case detail: header with amounts, tabs Overview / Evidence / Timeline / Messages / Agent runs, sticky decision bar, autonomy decisions, escalations | `/cases/{id}/workspace` |
| `#/activity` | Live feed of what the system did | `/activity?after=` |
| `#/agents` | Each agent's runs and outcomes; pause/resume | `/agents` |
| `#/policy` | Approval window, reminders, autonomy rules, cadence, model budget, preview | `/policy`, `/policy/preview` |
| `#/demo` | Demo Lab: scenarios, generator, simulated clock (play/pause/speed), worker run, simulated inbox | `/demo/*` |

The top bar holds **Run the month** (a small dialog: seed 42, 1–30
customers, speed 1 day / 10 s, 4 s or 2 s → `POST /demo/run`; a 404 says demo
routes are off, a 409 says a run is already going), the **spend meter** from
`/summary` (model spend in US dollars, "today of the daily cap · total of the
total cap"; "Cap reached — agents paused, decisions come to you" when
`model_calls_allowed` is false; invoices stay in CAD), the autonomy kill
switch (`/autonomy/pause|resume`, with a confirm) and Clear token. Every page
shows the "Synthetic data · simulated delivery" banner (bottom of the sidebar).

The Flow board uses `@xyflow/react` 12.12.1 (pinned). Nodes are kept in
state and updated through `onNodesChange`, because React Flow 12 only shows a
controlled node once its measured size comes back that way.

## How it stays live

- `@tanstack/react-query` fetches and caches everything (`src/api/client.ts`
  is the typed fetch wrapper).
- `/activity`, `/summary`, `/flow`, `/outcomes` (and an open case drawer's
  `/cases/{id}/flow`) are polled about every 3 s while the tab is
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
- The Flow, Your decisions and Outcomes pages never call `/cases/{id}/investigate`.
- The owner token lives in memory and sessionStorage: local demo only.
- `.npmrc` sets `legacy-peer-deps` because npm 10 crashes resolving Vitest's
  optional peers; every peer we need is listed in package.json.

## Tests

- `src/pages/*.test.tsx`, `src/components/Shell.test.tsx` and
  `src/App.test.tsx`: mocked fetch per page, including 401, 409, 503
  not-built, polling, keyboard approval, the Flow board (stages, chips, edge
  labels, edge animation, drawer), Your decisions, Outcomes, the spend meter
  and Run the month. React Flow runs in jsdom thanks to the `ResizeObserver` /
  `DOMMatrixReadOnly` stubs in `src/test/setup.ts`.
- `e2e/owner-flow.spec.ts`: checks Flow is the default page and the three
  primary pages load; a real browser loads a scenario, proposes, approves the
  exact message, queues it and checks the simulated inbox; and checks a
  malicious document renders as plain text. No paid model calls.
  `e2e/start-server.mjs` starts uvicorn from `../.venv` (`Scripts/uvicorn.exe`
  on Windows, `bin/uvicorn` elsewhere) with a fresh temp database, so
  `npm run e2e` also works on Windows.

Known gaps (contract, not UI): `/activity` reads forward only, so the first
load pages through history (capped); there is no "next step" field (Overview
shows scheduled jobs); rules and the case list carry no currency (CAD assumed).
Milestone 6: a `FlowStep` carries no customer name (Latest steps joins it from
`/flow` cases); `MessagePayload.written_by` says "agent" but not which agent
(the badge reads `proposed_by`); evidence has no kind, so a reply is labelled
"Customer reply" when the step's actor is `customer`; the Reporter appears in
the legend only (it is not a `FlowActor`).
