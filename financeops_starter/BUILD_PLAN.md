# FinanceOps: build with Claude Code and ECC

## Actual status (updated 16 November 2026)

| Phase | Status | Notes |
|---|---|---|
| 0 — scope and interfaces | Done (lean) | HTTP contract and case states below; seed in `seed.py` |
| 1 — deterministic core and persistence | **Done** | `cases` + append-only `case_events`, schema migrations, FastAPI `api.py`, owner-token approval, `/demo/*` synthetic routes |
| 2 — agent investigation | **Done (live check pending)** | `agent.py` Claude investigator: read tools + `propose_action`, case-bound, 12-call / 5-minute budget, stored traces, `POST /cases/{id}/investigate` (owner token) |
| 3 — actions, waiting, verification | **Done** | Code-written messages, hash-bound expiring approvals, stored policy + contacts, transactional outbox, `worker.py`, simulated inbox, follow-up and re-investigation jobs, proven closure |
| 4 — visible product and parallel development | **Done** | Owner console (React + TypeScript + Vite), six synthetic demo scenarios, Playwright e2e, layout redesign; commits 7704fc9, 8a382e4, d931d48, 5f0b13d |
| 5 — autonomous receivables operator | **In progress: step 0 done** | See `MILESTONE_5_PLAN.md`. Step 0: contract frozen (15 new routes; 3 are 503 stubs for steps 1a/1b), schema V5 (events, activity, escalations, kill switch, agent controls, briefings, demo playback, generator runs; invoice due dates; policy autonomy rules), 16 new tests. Next: 1a orchestrator/autonomy, 1b generator, 1c UI, in parallel. Vendor bills and cash move to later milestones. |
| 6–7 | Not started | |

Milestone 3 evidence: 142 tests pass (91 earlier ones, a handful updated for the
two deliberate contract changes below; 23 new in `test_outbox.py`, 25 new in
`test_worker.py`, 3 new HTTP tests). Covered: the owner approves the exact
message (hash required over HTTP); approval expires and is re-checked at first
delivery; a changed recipient, policy or evidence blocks execution; no policy or
no contact disables sending; policy reminder limit; `execute` only queues;
delivery never changes the balance; timeout after accept, timeout before accept
(with backoff), crash mid-send and restart all deliver exactly one message;
rejected messages fail without retry; payment or new evidence before delivery
cancels the send; follow-up date without payment reopens the case; a customer
reply creates one re-investigation job, which runs only with a model client and
retries after a failed model run; resolution stores which settlements prove it.
Also checked by hand: real uvicorn server, a separate `worker.py` process, and a
server restart between execute and delivery (one message in the inbox).
Reviewed with ECC security-reviewer, code-reviewer and python-reviewer; all
high and medium findings were fixed.

Contract changes: `POST /actions/{id}/execute` now returns `queued` (the worker
delivers); `POST /cases/{id}/actions` and approve need the owner token, and
approve needs `{"payload_hash": ...}` from `GET /actions/{id}`. IDs accept only
letters, digits and `_ . : -`.

Known gaps carried forward: the simulated clock is chosen per process by
`FINANCEOPS_ENABLE_DEMO`, so the API and worker must use the same setting; a
queued message cannot be cancelled by the owner before delivery (only by new
evidence, payment or expiry); one worker per database; the simulated inbox
shares the database file; pre-Milestone 3 pending actions must be rejected and
proposed again; the live model check (`eval_agent.py --run`) has still not run.

Milestone 2 evidence: 91 tests pass (47 from Milestone 1 unchanged, 44 new in
`test_agent.py` using a scripted fake model built from real SDK message types):
the agent cannot approve, execute, or reach another case; model text cannot
close a case; proposals must cite existing evidence (including in findings)
and are refused if the evidence changed since the model last read it; every
run ends with a stored outcome (including crashes, refusals, max_tokens,
timeouts, budget exhaustion); thinking is never stored; one investigation per
case at a time. Reviewed with ECC security-reviewer, code-reviewer and
python-reviewer; their high and medium findings were fixed. The live model
check (`eval_agent.py --run`, BUILD_PLAN acceptance: different evidence →
different justified proposal) has **not** been run yet; it needs Anthropic
credentials and costs money.

Known gaps carried forward: `POST /cases/{id}/actions` (manual proposals) needs
no token; the owner sees the agent's summary via `/agent-runs/{id}`, not on the
action itself; a case with no evidence can only end `no_proposal`; no stored
policy or `get_policy` tool yet; investigations are synchronous.

Milestone 1 evidence: 47 tests pass (`.venv/bin/python -m unittest`): the 11
original core tests unchanged, 16 case/migration tests, 20 HTTP contract tests
(full CAD 12,000 → 10,000 → reminder → CAD 2,000 story across a restart,
duplicate and concurrent settlement events, replayed disputes, missing/wrong
token → 401, stale approval → 409, dispute blocks reminders, busy database →
503, unexpected errors → detail-free 500). Also checked manually against a
running uvicorn server with a restart midway through the case. Reviewed with
ECC code-reviewer and python-reviewer; their medium findings were fixed.

Known gaps carried forward: case events do not record whether an input was
synthetic (only the `/demo` route prefix does); `invoice()["state"]` is a
financial status while case state is the workflow status, so they can differ
while an action is pending; no test yet for two processes migrating a fresh
database at the same moment.

Case states in use: `open`, `needs_information`, `awaiting_approval`,
`awaiting_external_outcome`, `resolved` (PRD §7 subset). Not yet: approval
payload hash/expiry, per-user approvers, outbox/scheduler, Postgres.

HTTP contract (see `/docs` when running): `GET /invoices/{id}`,
`GET /cases/{id}` (includes `verification` once resolved); owner token:
`POST /cases/{id}/actions`, `GET /actions/{id}`, `POST /actions/{id}/approve`
(body `payload_hash`), `/reject`, `/execute`, `GET`/`POST /policy`,
`POST /cases/{id}/investigate`, `GET /agent-runs/{id}`; demo only:
`POST /demo/{invoices,evidence,settlements,disputes,contacts,replies}`,
`GET /demo/inbox`, `POST /demo/worker/run` (`advance_minutes`; owner token
for `run_investigations`).

## What this starter includes

Runnable Python + SQLite receivables core, a synthetic demo, and 11 tests. It proves
partial payment handling, duplicate settlement protection, evidence references,
owner approval, stale-plan rejection, and settlement-based closure. It is a seed
for the project, not a completed app or a running AI agent. SQLite is used to make
the first example dependency-free; migrate persistence to Postgres for the shared
application. Existing code is original project code, not copied from ECC.

Run from this directory:

```bash
python3 -m unittest -v
python3 demo.py
```

## Tool choices

Claude Code: coding assistant. ECC: optional development workflows for planning,
testing, and review. Claude API: reasoning inside the application. FastAPI:
application endpoints and tool gateway. React + TypeScript: interface. Postgres:
shared persistent data. Start with one backend and worker, not microservices.

ECC is not the runtime finance agent. Its security/review helpers do not replace
application permissions or financial tests. Inspect the installed hooks and scope;
do not enable features merely because they are bundled. The plugin is third-party.

The current ECC README documents these commands inside Claude Code:

```text
/plugin marketplace add https://github.com/affaan-m/ECC
/plugin install ecc@ecc
/plugin list
```

Use one installation route. Do not combine the plugin with a full manual install.
Record the installed version for your team. Project instructions still belong in
CLAUDE.md. Start with planner, test guidance, and code review; add other workflows
when a specific task needs them. No ECC installation was performed for this kit.

## Phase 0 — scope and interfaces

Goal: agree what one complete case looks like. Use one Claude Code session.

Prompt:
“Read the PRD, BUILD_PLAN.md, and existing code. Use the ECC planner if available.
Produce a small implementation plan with dependencies, file ownership, tool/API
schemas, and acceptance tests. Start with a CAD 12,000 invoice paid CAD 10,000.
Show the remaining CAD 2,000, evidence investigation, an approved simulated
action, and verified settlement. Do not implement the entire PRD at once.”

Deliver: schema, HTTP contract, typed tool contract, status list, seed data, and
current milestone file. Check provider sandbox credentials now, not at demo time.

## Phase 1 — deterministic core and persistence

The supplied core is a starting point. Extend it with a Case table and event log,
source/version references, migrations, HTTP endpoints, and authenticated approval.
Keep trusted demo events separate from provider webhook routes.

Prompt:
“Extend the existing core into a FastAPI backend with persistent cases. Preserve
the existing tests. Add contract tests for invoice retrieval, case events,
approval, stale approval, and settlement ingestion. Do not add a model yet.
Show one complete case through HTTP, including process restart.”

Acceptance: repeated events have one effect; cases persist; only settled money
changes outstanding balance; an unauthenticated approval request fails.

## Phase 2 — agent investigation

Add a runtime model with three read tools and one proposal tool:

```python
# Dispatch sketch; the HTTP backend supplies the case-bound invoice ID.
# Do not let model arguments select arbitrary organizations or approve actions.
def dispatch(core, invoice_id, name, args):
    if name == "get_invoice":
        return core.invoice(invoice_id)
    if name == "get_evidence":
        return core.evidence(invoice_id)
    if name == "propose_action":
        return core.propose(
            args["action_id"], invoice_id,
            args["kind"], args["evidence_ids"]
        )
    raise ValueError("Tool not allowed")
```

The dispatch sketch needs typed input validation, server-generated action IDs,
durable tool traces, and run limits before integration. Expose get_policy as the
third read tool once policy is stored. Agent actions are proposals initially.

Agent loop: call Messages API with tool schemas; execute allowlisted tool_use
blocks; return tool_result blocks; repeat until end_turn or budget exhausted.
Validate inputs in code and handle truncated/failed responses explicitly.
Use an environment-configured available model ID and server-only API key.

Prompt:
“Add Claude tool use to investigate a case. It must cite stored evidence and
choose collection_reminder or request_information. Missing evidence must remain
unknown. No approval or settlement tool may be available to the model. Bound
the run to 12 tool calls and persist tool outcomes. Use fake model responses in
unit tests; run live API tests only with explicit credentials and cost awareness.”

Acceptance: change the evidence from a valid invoice to disputed delivery and
observe a different justified proposal. Model text alone cannot close a case.

## Phase 3 — actions, waiting, and verification

Add exact message payload/recipient, policy version, expiring approval bound to
payload hash, durable outbox, dispatch result, and follow-up jobs. Default to a
simulated inbox. On reply or settlement, resume the existing case. The supplied
execute_simulated method is only a local stand-in, not an outbox implementation.

Prompt:
“Complete the receivables loop. Separate proposal, approval, dispatch, waiting,
and settlement verification. Restart the worker mid-case. Test stale approvals,
duplicate events, timeout with unknown dispatch result, and new evidence after
approval. Never infer settlement from dispatch success.”

Acceptance: case survives restart and a new reply triggers fresh investigation;
no duplicate message on retry; settlement is separately verified.

## Phase 4 — visible product and parallel development

Only now split work: lead owns orchestration and shared contracts; frontend
worker owns React case/evidence/approval screens; integration worker owns adapter
and fixture files. Each gets its own worktree. Merge one task at a time and run
integration tests on the merged result. Use a fresh review context after changes.

Prompt for frontend:
“Implement the case workspace against the frozen API schema. Show evidence,
current state, proposed versus executed actions, and approval. Handle loading,
failure, and stale data. Do not duplicate business logic in the frontend.”

Acceptance: a user can inspect and approve a real backend case from the UI.

**Status: done.** Acceptance met: in a real browser, the owner loads a
synthetic case, proposes a reminder, approves the exact message (its payload
hash), queues it, and the worker delivers it to the simulated inbox
(`frontend/e2e/owner-flow.spec.ts`, Playwright, own server and database).

- A (lead, contract): owner-only `GET /cases` and `GET /cases/{id}/workspace`;
  typed responses for every route (`schemas.py`, `extra="forbid"`); the
  contract frozen in `contracts/openapi.json` with a drift test; the built UI
  served at `/app`.
- B (frontend worktree, `frontend/`): React + TypeScript + Vite owner console
  with case list, workspace, evidence (plain text, labelled untrusted),
  actions with exact message, approval and delivery, a paid-call confirm for
  investigations, and a demo lab. Types generated from the contract.
- C (scenarios worktree): six synthetic scenarios (`happy_path`,
  `partial_payment`, `dispute`, `missing_evidence`, `malicious_document`,
  `already_resolved`), additive and repeatable.
- D (lead, after merging B and C one at a time): invoices carry `total_cents`
  and `currency`; every route declares `ErrorOut` (404/409/422/503, plus 401
  on owner routes); the API only answers to local host names (blocks DNS
  rebinding); scenario reloads never overwrite a contact or dispute flag;
  review fixes in the UI (buttons wait for the refreshed case, network errors
  refetch, a failed paid investigation needs a new confirm, bad links no
  longer crash). Reviews: React, security, FastAPI (no critical findings).

Tests: 179 backend (`.venv/bin/python -m unittest`), 17 frontend (Vitest),
2 browser (Playwright), contract check unchanged. No paid model call was made.

Follow-ups (not blocking, local-demo threat model):
- `/demo/*` responses and `/demo/inbox` return evidence and contact text
  without the owner token; trim them or put the inbox behind the token.
- `GET /invoices/{id}` and `GET /cases/{id}` are token-free and include the
  customer name; decide whether they become owner-only.
- Add a Content-Security-Policy for `/app`; consider keeping the token in
  memory only.
- UI: an investigation in flight is forgotten when switching cases; repeated
  identical 409s do not re-announce; demo lab 409s show only the raw detail.
- Declare `Retry-After` (503) and `WWW-Authenticate` (401) headers in the
  contract; scenario preflight could also check settlement IDs.

## Phase 5 — connected vendor payments and cash

Add bill validation, duplicate detection, protected obligations, and daily cash
calculations. A changed receipt date affects proposed vendor payment scheduling.
Payments stay inside contractual dates unless a recorded agreement changes them.
Start with one simple vendor workflow; do not generalize all accounting domains.

Prompt:
“Add a vendor bill case and deterministic cash simulation. Preserve protected
payroll. Let the agent compare valid dates using the calculator tool. Block
duplicate bills. If no plan meets the constraints, escalate the shortfall.
Execute only in the simulated payment adapter with explicit approval.”

Acceptance: a delayed incoming payment changes the outgoing plan, or produces
an honest infeasibility result. A proposed collection never increases actual cash.

## Phase 6 — one real sandbox connector

Replace one adapter, not the whole backend. Choose based on accessible credentials
and required operations. A bank-feed integration supplies transactions; it does
not automatically provide invoicing or outgoing payments. Test authentication,
normalization, event deduplication, provider status readback, timeouts, and retries.
Never label a simulated operation as sandbox-backed.

## Phase 7 — evaluation and demo

Test at minimum: happy path, partial payment, valid dispute, missing evidence,
duplicate bill, stale approval, unknown action outcome, restart, and cash shortfall.
Use assertions against database/provider state, not only the model's explanation.
Track resolve-or-escalate correctness, unauthorized actions, tool calls, latency,
and cost. Keep fixed synthetic fixtures and test unseen wording/amount variations.
Review model-facing evidence for prompt injection attempts.

Demo: invoice created → partial payment → investigation → evidence changes →
agent replans → user approves → simulated action → settlement event → resolved.
Add the connected vendor-payment story once the first loop is reliable.

## Session handoff template

Task; owned paths; dependency contracts; current commit; acceptance tests;
forbidden changes; commands; known limitations. End every task with changed files,
checks actually run, unresolved failures, and next step. Avoid several sessions
editing shared migrations or tool schemas simultaneously.

## Sources reviewed

- https://github.com/affaan-m/ECC
- https://github.com/affaan-m/ECC/blob/main/agents/planner.md
- https://github.com/affaan-m/ECC/blob/main/agents/code-reviewer.md
- https://academy.claude.com/courses/ai-native-sdlc-playbook/parallel-sessions-and-subagents
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls

These are development references, not evidence that this starter is a production
finance system. The supplied code has no live-model integration or external API.
