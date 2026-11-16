# FinanceOps — Agentic Finance Operations

Product requirements document • v1.0 • 15 November 2026

Status: proposed design, not an implemented or validated product. Working name only. Assumption: a 48-hour hackathon, three or four builders, a fictional service business, CAD-only data, sandbox execution.

## 1. Product decision

Build an agent that owns incoming and outgoing money workflows from an initiating event to a verified financial outcome. It monitors records, investigates exceptions, tests proposed resolutions, acts within explicit authority, waits for external outcomes, and replans when the world changes.

The product is not complete merely because it produced advice or successfully called an API. An invoice case closes only when settlement and ledger allocation are verified, or an authorized alternative resolution is recorded. A payment case closes only when its actual disposition is confirmed and reconciled.

Pitch: “A finance operator that investigates what went wrong, takes permitted action, and follows the money until the case is resolved.”

## 2. Problem and users

Small service businesses handle invoices, contracts, delivery evidence, bank transactions, vendor bills, and cash commitments in disconnected systems. Straightforward records can follow rules; exceptions require someone to reconstruct events across those systems. An underpayment can create a cash shortfall, but a reminder alone does not resolve the underlying dispute.

Primary user: business owner or operations lead accountable for cash and approvals. Secondary user: bookkeeper who reviews allocations and corrections. Demonstration user: a judge who changes a transaction, evidence item, or constraint live.

User jobs:
- Collect earned revenue without repeatedly chasing legitimate disputes.
- Pay valid obligations once, on time, within agreed terms.
- Maintain a traceable connection between evidence, financial records, and actions.
- Understand and resolve exceptions without reviewing every ordinary transaction.
- Know when a cash plan depends on uncertain receipts.

## 3. Outcomes and scope

### Hackathon P0 — mandatory

Two connected loops: invoice-to-settlement and bill-to-settlement. Include one real sandbox connector, local simulated counterparts for other services, persistent cases, investigation tools, deterministic financial calculations, policy enforcement, approval, verification, and replayable scenario testing.

Support one business, one currency, fixed imported tax amounts, seeded chart of accounts, explicit customer/vendor identity, and preconfigured approval policy. Use synthetic contracts and records. Simulated time accelerates reminders and settlements; label it clearly.

### P1 — after the complete loops work

Receipt extraction, more flexible categorization, richer customer replies, second external connector, alternative scenario comparisons, accounting export, and performance evaluation views.

### Later product scope

Production connectors, multiple organizations, reconciliation at scale, multi-currency, accountant-reviewed posting rules, access administration, deployment operations, and jurisdiction-specific accounting support. Payroll and tax engines remain specialist systems; FinanceOps can coordinate their inputs, statuses, and obligations.

### Explicit exclusions

No live transfers in the hackathon. No tax filing, payroll processing, lending, investment decisions, fabricated delivery evidence, autonomous contractual concessions, or claim that transaction matching constitutes a full accounting close. No automatic learning that changes approval policy.

## 4. Relationship to the video's eight stages

| Stage | Product responsibility | Hackathon treatment |
|---|---|---|
| Capture | Ingest transactions, invoices, bills, contracts, and completion events | Structured import and one sandbox feed; PDF extraction optional |
| Reconcile | Match settled money to obligations; investigate ambiguity | Required, including partial payment and duplicate events |
| Categorize | Suggest categories with evidence and confidence gates | Seeded mappings; ambiguous items enter review |
| Invoice | Generate from authorized commercial terms and completion evidence | Required local invoice workflow |
| Pay/collect | Validate bills; collect receivables; schedule permitted payments | Required sandbox/simulated execution |
| Payroll | Treat payroll as a protected obligation | Imported commitments; no payroll calculation |
| Report | Produce operational status and explain changes | Cash/case summary, not certified financial statements |
| Forecast | Calculate scenarios and feed changed constraints back into cases | Required deterministic cash projection |

## 5. Functional requirements

| ID | Feature | Required behavior and acceptance condition |
|---|---|---|
| F01 | Business setup | Store accounts, currency, contacts, terms, category mappings, cash floor, protected obligations, and authority settings. Missing policy disables writes. |
| F02 | Ingestion | Normalize source IDs, timestamps, amounts, currency, and versions. Duplicate event ingestion creates no duplicate case or action. |
| F03 | Invoice creation | On verified completion, compute invoice from approved terms; preserve line-item provenance. Replayed completion creates one invoice. |
| F04 | Receivable monitoring | Detect overdue, partial, unmatched, and disputed payments. Stop collection reminders while a relevant dispute awaits resolution. |
| F05 | Bill validation | Compare bill to purchase/contract and delivery records; detect exact duplicates and flag possible duplicates. Ambiguous matches never trigger payment. |
| F06 | Investigation | Retrieve relevant evidence, identify competing explanations, request missing evidence, and emit structured findings with source references. Missing evidence remains unknown. |
| F07 | Planning | Produce an ordered action plan with preconditions, dependencies, expected effects, uncertainty, and a verification method for each action. |
| F08 | Financial simulation | Calculate daily cash under base and delayed-collection scenarios, identify minimum balance and affected obligations, and reject invalid proposed dates. |
| F09 | Policy gate | Backend validates every write against action type, amount, recipient, record version, authority, and approval state. Model text cannot bypass it. |
| F10 | Execution | Dispatch allowed actions using idempotency keys; store provider/local action IDs and exact requests. Unknown outcomes enter verification, not blind retry. |
| F11 | Follow-up | Persist timers and waiting states. New replies and settlement events resume the existing case after process restart. |
| F12 | Verification | Read back provider state or consume authenticated events, confirm expected amount/currency/identity, and reconcile settlement. API acceptance alone never closes a payment case. |
| F13 | Replanning | New evidence invalidates affected plans and approvals; recompute with fresh records before execution. |
| F14 | Approval inbox | Show exact action, recipient, amount, evidence, consequences, assumptions, and expiry. Approve, reject, or request changes. Approval is bound to an action hash and record versions. |
| F15 | Audit trail | Record evidence snapshots, structured findings, tool calls/results, policy decisions, approvals, external IDs, and state changes. Do not expose private model reasoning. |
| F16 | Incident controls | Pause writes globally or for a case; stop further actions after revocation. Preserve readable records for investigation. |
| F17 | Scenario runner | Reset seeded data, inject shocks, advance simulated time, and assert outcomes. Clearly distinguish simulated actions from connector actions. |

## 6. System layers

| Layer | Owns | Implementation proposal |
|---|---|---|
| 1. Source adapters | Bank/accounting feeds, documents, CRM completion, communication events | One real sandbox adapter plus explicitly simulated adapters |
| 2. Normalization and evidence | Canonical records, identity resolution, timestamps, document references, provenance | FastAPI ingestion; Postgres; object storage |
| 3. Financial services | Invoice arithmetic, allocations, balances, due dates, cash scenarios, duplicate checks | Pure Python functions with decimal/integer money arithmetic |
| 4. Durable orchestration | Case state, event routing, jobs, timeouts, restart recovery, bounded retries | Database-backed job queue and state machine |
| 5. Agent reasoning | Evidence retrieval, competing explanations, plan proposals, explanation | Claude through a constrained tool interface |
| 6. Authority and execution | Deterministic policy, approvals, action dispatch, idempotency | Server-side gateway; agent has no direct connector secrets |
| 7. Verification and audit | Readback, settlement matching, failed-action recovery, evaluation traces | Verifier worker plus append-only action history |
| 8. User experience | Cases, evidence, approvals, cash scenarios, demo controls | React interface with live event updates |

Claude Code is the development assistant used to write and debug the implementation. The running product uses a model API through typed tools. API/provider choices and credentials must be validated at kickoff; this PRD does not assume universal connector availability.

For P0, use one reasoning agent with separate investigation and planning steps. Verification and authorization are code, not another model's opinion. Specialist agents can be introduced later only if evaluations show an advantage.

## 7. Agent operating loop

1. Observe an event and deduplicate it.
2. Load the affected case, current policy, and fresh source records.
3. Classify the exception using deterministic checks where possible.
4. Investigate: retrieve the smallest relevant evidence set; compare hypotheses.
5. Propose a plan with evidence, uncertainty, dependencies, and required authority.
6. Test the plan: arithmetic, cash impact, due dates, recipient identity, duplicates, record freshness, and policy.
7. Execute permitted actions or wait for a precisely scoped approval.
8. Verify immediate effects and schedule checks for asynchronous effects.
9. Reopen/replan when evidence contradicts the plan; close only on verified terminal criteria.

P0 bounds: maximum 12 tool calls per reasoning run and two replans per triggering event. Exceeding either creates an escalation with the current evidence and unresolved question. Separate delayed jobs handle waiting; the model does not remain running for days.

### Case state model

States: detected, investigating, needs_information, planning, awaiting_approval, executing, awaiting_external_outcome, verifying, resolved, escalated, cancelled.

Any nonterminal state can escalate. New evidence from needs_information or awaiting_external_outcome resumes investigation. Rejected approval returns to planning or cancellation. A failed verification returns to investigation or escalation. A stale approval cannot transition to executing. Resolved cases can reopen on a returned payment or reversal event.

Each transition persists its event ID, prior state, next state, reason code, and version in one database transaction. Acquire a per-case lock for concurrent processing; execution additionally checks the affected invoice/bill/action version.

## 8. Detailed loop A — money coming in

Trigger: authorized work-completed event with contract and customer reference.

Happy path: validate completion and terms → calculate invoice → create invoice once → issue via an allowed communication adapter → track due date → observe settlement → match amount/customer/reference → allocate → verify balance → resolve.

Exception example: invoice is CAD 12,000 and CAD 10,000 arrives. The agent opens a CAD 2,000 discrepancy case. It examines the contract, invoice, delivery record, and customer reply. Candidate explanations include a valid credit, bank fee, duplicate discount, incomplete delivery, or unmatched allocation. It must not assume the explanation merely from the amount.

If the invoice is correct and the customer applied an unauthorized discount, prepare an evidence-backed request for the balance. If the customer disputes delivery, suspend automated chasing and inspect delivery evidence. A valid concession requires authorized adjustment approval; missing evidence creates a targeted information request. Recalculate cash scenarios while the balance is unresolved.

Closure criteria: settled allocations plus authorized credits equal the invoice total, with no unresolved discrepancy; source status and local status agree. A sent message, promise to pay, or initiated payment is not closure.

## 9. Detailed loop B — money going out

Trigger: new vendor bill.

Happy path: normalize → identify vendor → check duplicate fingerprint → compare contract/order and delivery → verify amount and imported tax → calculate permitted payment window → evaluate cash impact → approve if required → create sandbox payment → await settlement → reconcile → resolve.

Scheduling rule: propose dates within agreed terms. Moving an obligation beyond its due date requires recorded vendor agreement and a new authorized plan. Treat payroll and configured statutory commitments as protected cash outflows; the agent cannot silently move them.

Exceptions: unknown vendor, changed bank details, possible duplicate, missing delivery, price mismatch, insufficient cash, failed payment, returned payment, and provider timeout. Changed beneficiary details always require independent review. If the provider times out after accepting a request, query status using its identifiers/idempotency support before considering retry.

Closure criteria: payment settlement and bill allocation verified; a rejected/voided bill can close only through an authorized alternative disposition. Failed or returned payments remain open or reopen the case.

## 10. Connected cash management

Daily ending cash = previous ending cash + settled/projected inflows − settled/projected outflows. Use integer minor units or decimals; dates use the business timezone. Do not double-count a payment both in opening balance and future transactions.

Maintain separate categories: settled money, confirmed obligations, expected receipts, uncertain receipts, and proposed actions. A reminder changes the action history, not the bank balance. A requested payment extension does not change the due date until accepted.

For P0, generate base and stress scenarios from explicit receipt dates and delays. Display assumptions, data freshness, horizon, lowest daily balance, and any cash-floor breach. No claim of a statistically validated prediction model. Candidate actions are constrained by existing terms, authority, and protected obligations. If no feasible plan exists, escalate the shortfall instead of inventing collections or financing.

## 11. Tools and contracts

Read tools: get_case, get_invoice, get_bill, get_contract, get_delivery_evidence, list_transactions, get_contact, get_payment_status, get_policy.

Compute tools: match_transaction, check_duplicate, calculate_invoice, simulate_cash_plan, validate_plan.

Proposal tools: prepare_invoice, draft_collection_message, propose_credit, propose_payment_schedule, request_approval.

Execution tools through the gateway only: issue_invoice, send_authorized_message, create_sandbox_payment, apply_approved_adjustment. The hackathon's communication adapter writes to a simulated inbox by default.

Every tool accepts tenant/case context and validates arguments. Tool results include record IDs, versions, source timestamps, status, and structured errors. The model cannot choose another tenant or expand its own authority.

Plan schema: plan_id, case_id, policy_version, input_record_versions, evidence_refs, findings, unresolved_questions, actions, cash_scenarios, required_approvals, expiry. Each action has type, target, amount/currency if applicable, dependencies, preconditions, expected_effect, verification_method, and idempotency_key. Store approval separately, bound to the canonical action payload hash.

## 12. Authority model

| Action | Default P0 authority |
|---|---|
| Read records, investigate, simulate, draft | Automatic |
| Create internal case/task, update operational status | Automatic with audit |
| Issue an undisputed invoice from approved terms | Automatic in simulation; connector writes require configured authority |
| Send routine collection message | Simulated automatically within cadence/contact rules; disputed items paused |
| Propose payment date within terms | Automatic proposal |
| Execute sandbox payment | Explicit approval |
| Credit, write-off, commercial concession | Explicit approval |
| Change beneficiary, payroll, tax, or policy | Blocked in P0; escalate |

Production authority is configurable; it is not established by this document. Backend enforcement must precede every write. Approval expires if relevant amounts, recipients, terms, policy, or record versions change. Do not share broad filesystem/shell tools or connector credentials with the runtime agent. Treat instructions embedded in invoices, emails, and PDFs as untrusted data.

## 13. Core data model

- Organization and Policy: timezone, currency, authority matrix, cash floor, protected commitments, versions.
- Counterparty: verified identity, allowed contact channels, payment identity references.
- Contract/Order and DeliveryEvidence: terms, references, source location, versions.
- Invoice/Bill and LineItem: totals, due dates, status, source IDs, currency, imported tax.
- Transaction and Allocation: pending/settled/returned state, account, reference, signed amount, allocation links.
- Case and CaseEvent: type, state, owner, links, deadlines, version, triggering event.
- Evidence: immutable snapshot/reference, retrieval timestamp, source version, hash.
- Plan and Action: proposed effects, dependencies, policy results, execution IDs, idempotency keys.
- Approval: approver, scope hash, expiry, policy/input versions, decision.
- Job and Outbox: scheduled work, attempts, next run, durable dispatch state.
- Scenario and EvaluationRun: seed, shock inputs, expected assertions, actual outcomes.

The accounting provider remains the authoritative ledger where connected. Local tables are an operational mirror and workflow store, not a second competing ledger. Offline demo records are explicitly simulated. Corrections use traceable adjustments rather than silent deletion of history.

## 14. User interface

Operations home: cases resolved, cases waiting, protected obligations at risk, and last synchronization time. Avoid making charts the centre of the product.

Case workspace: issue, current state, evidence links, structured findings, unresolved questions, proposed/actual actions, and verification results. Display concise decision summaries rather than hidden reasoning traces.

Approval view: exact change, amount and recipient, reasons, assumptions, before/after effect, policy checks, expiry, approve/reject/edit request.

Cash view: dated cash scenarios with toggles for explicit assumptions; visually distinguish uncertain and settled items.

Demo lab: seeded business selector, shock injector, simulated clock, adapter labels, reset, and test results.

## 15. Testing and investigation are separate

Runtime investigation establishes what happened using records and missing-evidence requests. Runtime plan testing checks whether a proposed action is valid and has a calculable effect. Runtime verification checks whether an executed action actually worked. Developer testing proves these mechanisms behave correctly across known failures.

Do not use “another agent agrees” as a substitute for arithmetic, permissions, settlement evidence, or evaluation assertions.

## 16. Test plan and acceptance scenarios

| Test | Input | Required outcome |
|---|---|---|
| T01 Normal receivable | Completion, correct invoice, full settlement | One invoice; one allocation; resolved only after settlement |
| T02 Partial payment | 12,000 invoice; 10,000 settled | Open 2,000 discrepancy; no invented cause |
| T03 Valid dispute | Reply disputes delivery; delivery missing | Pause reminders; request evidence; no forced collection |
| T04 Invalid discount | Contract disproves claimed discount | Evidence-backed balance request; wait for outcome |
| T05 Duplicate bill/event | Same bill or event repeated | No second invoice/payment/allocation |
| T06 Ambiguous transaction | Two plausible invoice matches | Escalate/review; no arbitrary allocation |
| T07 Unknown payment result | Provider accepts then times out | Verify before retry; at most one payment |
| T08 Stale approval | Amount or recipient changes after approval | Block execution and require fresh approval |
| T09 Cash shortfall | Expected receipt delayed 30 days | Recompute; preserve protected obligations; show infeasibility if necessary |
| T10 New constraint | Vendor cannot accept proposed change | Replan within agreed terms; invalidate obsolete plan |
| T11 Malicious document | PDF instructs agent to bypass approval | Ignore instruction; no privileged action |
| T12 Restart | Process stops while waiting for settlement | Resume persisted case; no repeated side effect |
| T13 Reversal | Previously settled payment returned | Reopen case; reverse operational allocation consistently |
| T14 Stale/missing data | Bank feed stale or contract absent | Flag uncertainty; block dependent writes |
| T15 Cross-tenant request | Tool references foreign organization | Reject without disclosure |
| T16 Bad arithmetic | Tax/total mismatch, rounding boundary | Deterministic rejection or correct minor-unit calculation |

Test layers: unit tests for money and policy; adapter contract tests; state-machine integration tests; replay scenarios with a controllable clock; repeated agent evaluations on paraphrased evidence; full demo rehearsal including injected failure.

Quality gate: all money/policy/idempotency assertions pass; zero unauthorized writes in the evaluation set; all resolved cases carry verification evidence; all tests are reproducible from a clean seed. Track completion, appropriate escalation, duplicate side effects, tool-call count, latency, and model cost. Aim for at least 90% correct resolve-or-escalate outcomes across 20 fixed seeded variations, run three times each. This is a development target, not a claim of production reliability.

## 17. Demonstration script

0:00–0:30: Show business policy and completed work creating an invoice.

0:30–1:15: Inject underpayment and a customer dispute. Agent opens a case, finds conflicting evidence, and pauses collection.

1:15–2:00: Supply corrected delivery evidence. Agent revises the plan and initiates an authorized simulated follow-up; cash impact remains uncertain.

2:00–2:45: Inject a vendor duplicate and delayed customer receipt. System blocks the duplicate and tests a permitted payment schedule against protected payroll.

2:45–3:30: Judge changes a constraint. Agent replans; owner approves exact sandbox action. Show the actual adapter result.

3:30–4:00: Advance simulated time, inject settlement, verify allocation and close the case. Show the difference between proposed and completed actions and a replay-test result.

Use explicit labels throughout: synthetic business, simulated communications/time, sandbox connector. Do not imply customers actually paid because the agent sent a message.

## 18. Suggested 48-hour build plan

Hours 0–4: Agree schema, policy, demo seed, success assertions; verify connector credentials. Fall back to an honest simulated connector if access fails, and narrow demo claims.

Hours 4–14: Implement deterministic money services, normalization, persistent state, gateway, and simulated adapter. Build case/evidence UI against fixed events.

Hours 14–24: Add agent tools and structured plans; complete receivable loop including partial payment and verification.

Hours 24–34: Complete payable loop, approval binding, cash simulation, and one real sandbox adapter.

Hours 34–42: Fault injection, duplicate/timeouts/restart tests, malicious document test, live replanning.

Hours 42–48: Freeze features, run clean-seed evaluations, polish event visibility, rehearse and record a backup demo.

Suggested ownership: one builder for workflow/backend, one for agent/tools/evaluation, one for frontend/demo, and one for connector/fixtures/testing. With fewer builders, cut optional extraction and extra connectors before cutting verification.

## 19. Risks and decisions

Primary risks: integration access, overly broad scope, unreliable evidence extraction, agent loops, false certainty about future collections, and actions repeated after partial failures. Controls are explicit adapter fallback, P0 boundaries, structured seed data, bounded runs, scenario labels, and durable idempotency/verification.

Decisions still needed at kickoff: hackathon duration/team, required sponsor tools, permitted model budget, which sandbox is accessible, exact demo contact/approval policy, and whether invoice issuance must use an external service. Defaults in this PRD allow implementation to start without those answers.

## 20. Definition of done

The prototype demonstrates both financial loops with real state changes, at least one unexpected evidence-driven replan, deterministic arithmetic and authority checks, explicit waiting for external outcomes, restart-safe persistence, and evidence-backed closure. A judge can introduce a supported shock live and observe a valid resolution or justified escalation. All simulated components are labelled. The deliverable includes setup instructions, seed/reset scripts, tests, and a recorded fallback demo.

The intended differentiator is operational ownership through exceptions and verified outcomes. Whether it is novel enough for a particular competition remains a hypothesis to validate against that competition's judging criteria and competing entries.
