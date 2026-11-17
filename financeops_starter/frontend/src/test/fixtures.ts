// Synthetic fixtures typed from the generated contract, so a contract change
// that breaks a shape the UI relies on also breaks these tests at compile time.
import { vi } from 'vitest';
import type { Action, CaseListOut, Inbox, ScenarioList, Workspace } from '../api/client';

export const TOKEN = 'test-owner-token';
export const HASH = '9f2c1e7ab4d05566778899aabbccddeeff00112233445566778899aabbccddee';

export const caseList: CaseListOut = {
  cases: [
    {
      id: 'case-INV-1001',
      invoice_id: 'INV-1001',
      customer: 'Maple Synthetic Ltd',
      currency: 'CAD',
      outstanding_cents: 125000,
      total_cents: 125000,
      pending_actions: 1,
      state: 'awaiting_approval',
      updated_at: '2026-11-16T10:00:00Z',
      version: 3,
    },
  ],
};

export const proposedAction: Action = {
  id: 'act-1',
  invoice_id: 'INV-1001',
  kind: 'collection_reminder',
  status: 'proposed',
  proposed_by: 'owner',
  rationale: 'Invoice is overdue.',
  evidence_ids: ['ev-1'],
  approval: null,
  approved_at: null,
  approved_by: null,
  executed_at: null,
  executed_by: null,
  delivery: null,
  payload_hash: HASH,
  payload: {
    to: 'ap@maple.example',
    subject: 'Reminder: invoice INV-1001',
    body: 'Hello, invoice INV-1001 for $1,250.00 CAD is outstanding.',
    amount_cents: 125000,
    currency: 'CAD',
    channel: 'email',
    evidence_ids: ['ev-1'],
    invoice_id: 'INV-1001',
    invoice_version: 3,
    kind: 'collection_reminder',
    policy_version: 1,
    simulated: true,
  },
  simulated: true,
  version: 1,
};

export function workspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    case: {
      id: 'case-INV-1001',
      invoice_id: 'INV-1001',
      kind: 'collection',
      state: 'awaiting_approval',
      version: 3,
      created_at: '2026-11-16T09:00:00Z',
      updated_at: '2026-11-16T10:00:00Z',
      verification: null,
      events: [
        {
          event_id: 'e1',
          created_at: '2026-11-16T09:00:00Z',
          invoice_version: 1,
          prior_state: null,
          next_state: 'open',
          reason: 'invoice created',
        },
      ],
    },
    invoice: {
      id: 'INV-1001',
      customer: 'Maple Synthetic Ltd',
      total_cents: 125000,
      currency: 'CAD',
      settled_cents: 0,
      outstanding_cents: 125000,
      disputed: false,
      state: 'open',
      version: 3,
      case_id: 'case-INV-1001',
    },
    evidence: [
      { id: 'ev-1', invoice_id: 'INV-1001', body: 'Customer note: <script>alert("x")</script> pay later', untrusted: true },
    ],
    actions: [proposedAction],
    agent_runs: [],
    jobs: [],
    policy: {
      version: 1,
      approval_ttl_minutes: 60,
      follow_up_days: 7,
      max_reminders: 3,
      created_at: '2026-11-16T09:00:00Z',
      created_by: 'owner',
    },
    contact_on_file: true,
    sending_enabled: true,
    reminders_sent: 0,
    now: '2026-11-16T10:00:00Z',
    simulated_time: true,
    ...overrides,
  };
}

export const scenarios: ScenarioList = {
  scenarios: [{ name: 'baseline', title: 'Baseline', description: 'Two synthetic overdue invoices.' }],
};

export const inbox: Inbox = {
  simulated: true,
  messages: [
    {
      external_id: 'sim-1',
      idempotency_key: 'act-1',
      recipient: 'ap@maple.example',
      subject: 'Reminder: invoice INV-1001',
      body: 'Hello',
      received_at: '2026-11-16T10:05:00Z',
    },
  ],
};

/** `wait` holds the answer back until the promise resolves (for slow-server tests). */
type Reply = { status?: number; body?: unknown; wait?: Promise<void> } | 'network-error';
type Handler = (init: RequestInit) => Reply;

/**
 * Replaces global fetch with a tiny router keyed by "METHOD /path".
 * A route can be a reply, a handler, or a list of replies used in order
 * (the last one repeats). Returns the vi.fn so tests can inspect calls.
 */
export function mockFetch(routes: Record<string, Reply | Handler | Reply[]>) {
  const counts: Record<string, number> = {};
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const key = `${init.method ?? 'GET'} ${String(input)}`;
    const route = routes[key];
    if (route === undefined) {
      return new Response(JSON.stringify({ detail: `no mock for ${key}` }), { status: 404 });
    }
    let reply: Reply;
    if (typeof route === 'function') reply = route(init);
    else if (Array.isArray(route)) {
      const n = counts[key] ?? 0;
      counts[key] = n + 1;
      reply = route[Math.min(n, route.length - 1)] as Reply;
    } else reply = route;
    if (reply === 'network-error') throw new TypeError('Failed to fetch');
    if (reply.wait) await reply.wait;
    return new Response(reply.body === undefined ? '' : JSON.stringify(reply.body), {
      status: reply.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

/** The calls made to one route, with parsed JSON bodies. */
export function callsTo(fn: ReturnType<typeof mockFetch>, key: string) {
  return fn.mock.calls
    .filter(([input, init]) => `${init?.method ?? 'GET'} ${String(input)}` === key)
    .map(([, init]) => ({
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined,
    }));
}
