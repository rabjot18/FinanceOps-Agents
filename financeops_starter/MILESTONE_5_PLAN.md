# Milestone 5 plan: autonomous receivables operator

Status: **step 0 done** (contract frozen, schema V5) · written 17 November 2026

## Goal

Turn FinanceOps from "an assistant you click" into "an operator that works and
shows you only the exceptions", which is the first real step toward the
multi-agent finance department in the original vision.

At the end of Milestone 5 you should be able to: generate a fictional business
with six months of activity, press **Play**, and watch the system ingest events,
investigate, send routine reminders by itself under policy, replan when replies
or payments arrive, and stop to ask the owner only for disputes, large amounts and
anything risky. The console becomes a multi-page app that updates live.

Out of scope for this milestone (planned for later, see the roadmap at the end):
vendor bills, cash forecasting, bank matching, document capture, real provider
connectors. Never in the prototype: live payments, payroll, tax filing.

## What changes, in one picture

```
 sources                  ingestion (code)              orchestrator (code)        agents (Claude)
 ────────────             ───────────────────           ───────────────────        ────────────────
 scenarios.py   ─┐                                        route by event type   ─►  collections agent
 generator.py   ─┼─►  normalize · dedupe · label   ─►     autonomy check       ─►  owner briefing agent
 /demo routes   ─┘    store in `events` (source ref)      budgets · escalation         (read-only)
                                                               │
                                     policy rules ─────────────┤
                                     auto │ needs approval │ blocked
                                          ▼
                       existing approve → execute → outbox → worker → verify  (unchanged guarantees)
```

The guarantees from Milestones 1–3 stay as they are: code writes every message,
approvals are bound to a payload hash and expire, delivery never changes money,
and a case closes only when settlements prove it. Autonomy is added **in front
of** that pipeline, not instead of it.

---

## Workstream A: synthetic business generator and evaluation

**Why first:** every later agent needs realistic data with known right answers.

- **`generator.py`.** `generate(seed, months=6, customers=25, anomaly_rate=…)`
  returns a timeline of events for a fictional service company: customers,
  contacts, contracts, invoices, due dates, payments (on time, late, partial,
  overpaid), customer replies, and disputes. The same seed always produces the
  same data.
- **Planted problems**, each recorded in a ground-truth file
  (`truth.json`: event, expected outcome):
  late payer, partial payment with no reason, partial payment explained by an
  agreed credit, disputed work, missing contract, mistyped payment reference,
  reply promising to pay on a date, reply claiming already paid, and a reply or
  document containing instructions aimed at the AI (prompt injection).
- **Wording variation.** Replies come from templates with varied phrasing,
  amounts and names, so agents can't memorize the fixed scenarios.
- **Replay.** `POST /demo/generator/run {seed, months}` loads the timeline. The
  simulated clock replays it: **Play, Pause, Speed (1 day = 2 s … 1 day = 0.2 s)**.
  All records are labelled synthetic.
- **`eval_run.py`.** Replays a generated business and scores the result
  against `truth.json`:
  resolve-or-escalate correctness, **unauthorized actions (must be 0)**,
  auto vs. owner-handled counts, tool calls, latency, model cost.
  The default run uses the fake model client (free, deterministic);
  `--live` uses the real model, prints the expected cost first, and needs a
  confirmation.

## Workstream B: ingestion and events

- New **`events`** table: `id, kind, source (scenario|generator|demo|…),
  source_ref, dedupe_key UNIQUE, synthetic, payload, received_at,
  routed_at`. This closes the known gap that case events don't record
  whether an input was synthetic.
- Every existing `/demo/*` input and the generator write through one
  `ingest(event)` function: normalize, then dedupe (a replayed event is a no-op),
  then store, then hand off to the orchestrator.
- Adapters are a small interface (`to_events(raw) -> list[Event]`), so the
  Stripe test-mode adapter (Milestone 6) plugs in without touching agents.

## Workstream C: orchestrator (code, not AI)

- **`orchestrator.py`**, called by `worker.py` each cycle. A **routing table**
  maps event kinds to handlers:

  | Event | Handler |
  |---|---|
  | invoice created | schedule reminder cadence (day +3, +7, +14 after due) |
  | due date passed, no payment | cadence step: collections agent, or code if routine |
  | settlement received | recompute; full = verify and close; partial = collections agent |
  | customer reply | collections agent (replan) |
  | dispute raised | pause cadence, escalate to owner |
  | daily tick | owner briefing agent |

- **Routine work is code, not AI.** A plain overdue reminder on an undisputed
  invoice with a contract on file needs no model call. The agent is called
  only when there's something to judge (partial payment, reply, missing
  evidence). This keeps cost and risk down.
- **Budgets:** at most 12 tool calls per run (existing), at most **2 replans
  per triggering event** (PRD §7), and a per-day model spend cap in the policy.
  Going over any of these escalates the case to the owner with the evidence so far.
- **Owner kill switch:** "Pause autonomy" stops all automatic execution
  immediately; proposals still queue for approval.

## Workstream D: autonomy policy (the "shared brain")

- **Policy v2 schema** (new `_SCHEMA_V5`): `autonomy_rules` per action kind:
  `mode (auto | approve | blocked)`, `max_amount_cents`, conditions
  (`undisputed`, `contract_on_file`, `reminders_sent < max`), plus the
  reminder cadence days and the daily model budget.
- **Suggested defaults:**

  | Action | Default |
  |---|---|
  | Reminder, undisputed, under CAD 5,000, within cadence | **auto** |
  | Reminder, CAD 5,000 or more | approve |
  | Request for information (missing evidence) | **auto** |
  | Anything on a disputed invoice | approve |
  | Credit, write-off, concession | blocked in M5 (escalate) |
  | Change contact or policy | blocked (owner only) |

- **Auto still goes through the same checks.** An auto action is approved by
  `approver = "policy:v<n>/rule:<id>"` through the existing `approve()` and
  `execute()`, so hash binding, expiry, version checks and the outbox apply
  unchanged. The timeline shows *which rule* allowed it.
- **The model never decides its own autonomy.** Code evaluates the rules after
  the proposal is stored. New `CLAUDE.md` rule:
  *"Only actions whose rule evaluates to `auto` under the current policy
  version may execute without the owner. The model cannot read or change
  autonomy rules."*

## Workstream E: agent upgrades

- **Refactor** `Investigator` into a small `Agent` base with a per-agent tool
  set, budget and stored outcome. The tool rule stays: no tool takes an ID, and
  the server binds the case.
- **Collections agent** (upgraded investigator):
  - New read tools: `get_replies` (customer messages, untrusted),
    `get_cadence` (where the case is in the reminder schedule).
  - **Replanning:** a new proposal **supersedes** the pending one (the old one is
    marked `superseded`, linked through `superseded_by` and kept in the timeline).
  - Reply understanding: a promise to pay schedules a follow-up check on that date;
    a claim of having already paid triggers a request for remittance details;
    a dispute escalates.
- **Owner briefing agent** (new, read-only): once a simulated day, it writes a
  short briefing: what the system handled by itself, what needs the owner,
  slow payers, and outstanding totals with source links. It has no write tools,
  so it's a safe second agent that shows multi-agent coordination.
- **Models:** `claude-opus-5-5` for collections (judgement),
  `claude-haiku-5-5` for the briefing (cheap summarizing); both configurable.

## Workstream F: UI redesign (multi-page, live, friendlier)

**Problems today** (checked on the running console): one long page
(~2,600 px) with the case list, workspace and Demo Lab all stacked
together; the Demo Lab sits below the workspace; there's no overview of what
needs attention; data only refreshes after your own clicks; there's no history of
what the system did by itself.

**New structure** (hash routes, so FastAPI still serves only `/app`):

| Page | What it shows |
|---|---|
| **Home: "Needs you"** | KPI strip (outstanding, overdue, auto-handled this week, awaiting you); **approval queue** with inline Approve/Reject; escalations with the agent's question; today's briefing |
| **Cases** | Searchable, sortable table: customer, amount, days overdue, state chip, "handled by" (auto/agent/owner), next step; filters by state |
| **Case detail** | Header with amounts and progress bar, plus tabs: **Overview** (next step, latest agent summary), **Evidence**, **Timeline**, **Messages** (exact text sent), **Agent runs** (tool calls, outcome, cost); sticky action bar for approve/reject |
| **Activity** | Live feed of everything the system did, e.g. "Reminder 2 sent to Northwind automatically (policy v2, rule R1)" |
| **Agents** | Each agent: runs, outcomes, escalation rate, spend today; per-agent pause |
| **Policy** | Edit autonomy rules and cadence, with a preview such as "with these rules, 18 of 25 open cases would be handled automatically" |
| **Demo Lab** | Moved to its own page: scenarios, **generator (seed, months)**, **clock controls (Play/Pause/Speed)**, simulated inbox |

**Dynamic behaviour**
- **Live updates:** `GET /activity?after=<cursor>` polled every 2–3 s while
  the tab is visible, via **TanStack Query**, which also replaces the manual
  "revision" refetch. (Server-sent events can come later; polling is simpler
  to test and fits the frozen contract.)
- Toast notifications ("Case case-17 needs you"), loading skeletons instead
  of "Loading…", useful empty states, and a 409 conflict shown as "this case
  changed, here's what's new".
- Keyboard shortcuts on the approval queue (`j`/`k` to move, `a` approve,
  `r` reject, each with a confirmation).
- Responsive layout down to tablet width; keep light and dark themes; keep the
  "Synthetic · simulated" banner on every page.

**Libraries:** add `@tanstack/react-query` and `react-router` (HashRouter).
Keep the custom CSS with design tokens instead of a component library, so the
bundle stays small. Pin exact versions, as today.

**Unchanged rules:** the UI shows what the backend computes and never
recalculates amounts or states; untrusted text renders as plain text; approve
sends the exact `payload_hash`.

## HTTP contract (frozen in step 0)

The source of truth is `schemas.py` and `contracts/openapi.json`. Routes marked
*stub* answer 503 "Not built yet (Milestone 5 step N)" until that step lands.

Owner token:
- `GET /attention`: the "Needs you" queue (approvals to approve or execute, open escalations, kill-switch state)
- `GET /cases?state=&q=&sort=updated|outstanding|overdue|customer&limit=&cursor=`: filtered, sorted, paged; rows add `due_at`, `days_overdue`, `open_escalations`, `needs_owner`, `handled_by`
- `GET /summary`: KPI numbers (computed by the backend)
- `GET /activity?after=&limit=`: cursor feed of system activity (poll this)
- `POST /escalations/{id}/resolve`
- `GET /autonomy`, `POST /autonomy/pause`, `POST /autonomy/resume`: the kill switch
- `GET /agents`, `POST /agents/{name}/pause|resume`
- `GET /briefings/latest` (404 until the first briefing)
- `GET`/`POST /policy`: add `autonomy_rules`, `cadence_days`, `daily_model_budget_cents` (omitted = keep)
- `POST /policy/preview` (built in step 1a)
- `GET /cases/{id}/workspace`: adds `escalations` and `autonomy`; actions add `autonomy` and `superseded_by`; invoices add `due_at`; agent runs add `agent`

Demo only:
- `GET`/`POST /demo/clock` (`play`, `pause`, `speed` in simulated minutes per real second)
- `POST /demo/invoices` accepts `due_at`
- `POST /demo/generator/run`, `GET /demo/generator/truth?seed=`: *stubs, step 1b*

New enum values: case state `escalated`; action status `superseded`;
job kinds `cadence_step`, `briefing`.

## Build order and parallel work

| Step | Owner | Work | Done when |
|---|---|---|---|
| 0 ✅ | lead | Freeze the new contract (`schemas.py`, `openapi.json`, regenerated types); schema V5 migration; read-side routes and owner controls | Done 17 Nov 2026: 195 backend tests, 17 frontend, 2 browser |
| 1a ✅ | lead | Events + ingestion, autonomy rules, orchestrator, kill switch, policy preview, clock playback | Done 17 Nov 2026: `orchestrator.py`, 28 tests in `test_orchestrator.py`; 225 backend tests; reviewed (7 medium findings fixed). Not yet: model budget enforcement and replan counting (step 2); policy preview is an estimate (it guesses each case's next action) |
| 1b | worker (worktree) | `generator.py`, `truth.json`, `eval_run.py`, demo generator/clock routes | same seed gives same timeline; eval runs end to end with the fake client |
| 1c | worker (worktree) | Frontend redesign against the frozen contract with mocked fetch | Vitest and the e2e suite pass |
| 2 | lead | Agent refactor, replanning, collections tools, briefing agent | agent tests pass |
| 3 | lead | Merge 1b and 1c one at a time; full replay demo; reviews (security, code, python, react) | acceptance list below passes |
| 4 | you | Optional live eval (`eval_run.py --live`, costs money) | score recorded in `BUILD_PLAN.md` |

1a, 1b and 1c can run at the same time once step 0 is merged, because they own
separate files (same ownership rules as Milestone 4).

## Acceptance tests

1. A routine overdue reminder under the threshold is sent **without** the owner;
   the timeline names the policy rule; the inbox shows exactly one message.
2. The same reminder over the threshold waits in the approval queue.
3. A dispute raised mid-cadence pauses reminders and appears in "Needs you".
4. A customer reply makes the agent replan: the old proposal is superseded, not deleted.
5. A third replan for the same event escalates instead of looping.
6. "Pause autonomy" stops the next automatic send; resuming continues the cadence.
7. Injected instructions in a reply or document cause **zero** unauthorized actions.
8. Replaying the same generated timeline twice adds no duplicate events, jobs or messages.
9. A six-month generated business replays to the end with **unauthorized actions = 0**
   and every planted problem either resolved correctly or escalated.
10. Going over the daily model budget escalates instead of calling the model.
11. The UI shows a new activity item and updated KPIs within about 3 s of a
    worker cycle, without a page reload.
12. All existing tests (175 backend, 13 frontend) still pass.

## Demo script (about 3 minutes)

Generate a business (seed 42, 6 months), then Play. The Activity feed fills
with automatic reminders. A partial payment arrives and the agent investigates
and asks for remittance details automatically. A dispute appears in "Needs you":
the owner reads the evidence and approves a request for information. A reply
containing prompt injection is labelled untrusted and ignored. Pause and show the
briefing, the Agents page (spend, escalation rate) and the eval score.

## Decisions (made 17 November 2026: defaults accepted)

1. Automatic reminders up to **CAD 5,000** (`AUTO_REMINDER_LIMIT_CENTS`).
2. Live updates by **polling** `/activity`; server-sent events later if needed.
3. The **briefing agent is in** Milestone 5.

## Roadmap after Milestone 5

| Milestone | Adds | Article module |
|---|---|---|
| 6 | Stripe test-mode connector for invoices and payments; signed webhooks | Invoicing / AR |
| 7 | Vendor bills agent: duplicates, changed bank details blocked, payment scheduling, simulated payments | Bills / AP |
| 8 | Cash watch: deterministic projection, cash-floor alerts, feeds bill scheduling | Cash-flow watch |
| 9 | Bank matching + categorization agent; Plaid sandbox | Reconcile / categorize |
| 10 | Document capture (receipts, PDF bills) as untrusted drafts | Capture |
| 11 | Reporting agent; QuickBooks sandbox sync | Reporting |

Payroll and tax stay outside the prototype: FinanceOps may track their
deadlines and amounts as protected obligations, never calculate or file them.
