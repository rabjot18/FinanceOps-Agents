// Synthetic fixtures typed from the generated contract, so a contract change
// that breaks a shape the UI relies on also breaks these tests at compile time.
import { vi } from 'vitest';
import type {
  Action,
  ActivityItem,
  ActivityOut,
  AgentList,
  Attention,
  AutonomyStatus,
  Briefing,
  CaseListOut,
  Clock,
  Escalation,
  Inbox,
  Policy,
  ScenarioList,
  Summary,
  Workspace,
} from '../api/client';

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
      due_at: '2026-11-10T00:00:00+00:00',
      days_overdue: 6,
      open_escalations: 0,
      needs_owner: true,
      handled_by: 'owner',
    },
  ],
  total: 1,
  next_cursor: null,
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
  autonomy: null,
  superseded_by: null,
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
      due_at: '2026-11-10T00:00:00+00:00',
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
      autonomy_rules: [],
      cadence_days: [],
      daily_model_budget_cents: 0,
      created_at: '2026-11-16T09:00:00Z',
      created_by: 'owner',
    },
    contact_on_file: true,
    sending_enabled: true,
    reminders_sent: 0,
    escalations: [],
    autonomy: { paused: false, changed_by: null, changed_at: null, reason: null },
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
type Handler = (init: RequestInit, url: string) => Reply;

/**
 * Replaces global fetch with a tiny router keyed by "METHOD /path".
 * A route can be a reply, a handler, or a list of replies used in order
 * (the last one repeats). Returns the vi.fn so tests can inspect calls.
 */
export function mockFetch(routes: Record<string, Reply | Handler | Reply[]>) {
  const counts: Record<string, number> = {};
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    // Exact "METHOD /path?query" first, then the same path without its query.
    const key = `${init.method ?? 'GET'} ${String(input)}`;
    const route = routes[key] ?? routes[key.split('?')[0] as string];
    if (route === undefined) {
      return new Response(JSON.stringify({ detail: `no mock for ${key}` }), { status: 404 });
    }
    let reply: Reply;
    if (typeof route === 'function') reply = route(init, String(input));
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
    .filter(([input, init]) => {
      const full = `${init?.method ?? 'GET'} ${String(input)}`;
      return full === key || (!key.includes('?') && full.split('?')[0] === key);
    })
    .map(([input, init]) => ({
      url: String(input),
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined,
    }));
}

// --- Milestone 5 ----------------------------------------------------------

export const autonomyOn: AutonomyStatus = { paused: false, changed_by: null, changed_at: null, reason: null };
export const autonomyPaused: AutonomyStatus = {
  paused: true,
  changed_by: 'owner',
  changed_at: '2026-11-16T10:00:00Z',
  reason: null,
};

export const summary: Summary = {
  currency: 'CAD',
  outstanding_cents: 845000,
  overdue_cents: 125000,
  open_cases: 4,
  overdue_cases: 1,
  awaiting_owner: 2,
  auto_handled_7d: 7,
  resolved_7d: 2,
  model_spend_today_cents: null,
  autonomy_paused: false,
  now: '2026-11-16T10:00:00Z',
  simulated_time: true,
};

export const escalation: Escalation = {
  id: 'esc-1',
  case_id: 'case-INV-2002',
  created_at: '2026-11-16T09:30:00Z',
  evidence_ids: ['ev-9'],
  question: 'Customer says the delivery was <b>incomplete</b>. Offer a credit?',
  raised_by: 'agent:collections',
  reason: 'dispute',
  resolution_note: null,
  resolved_at: null,
  resolved_by: null,
};

export const secondAction: Action = {
  ...proposedAction,
  id: 'act-2',
  invoice_id: 'INV-3003',
  payload_hash: 'aa11bb22cc33dd44ee55ff66aa11bb22cc33dd44ee55ff66aa11bb22cc33dd44',
  payload: { ...(proposedAction.payload as NonNullable<Action['payload']>), to: 'ap@birch.example', invoice_id: 'INV-3003' },
};

export function attention(overrides: Partial<Attention> = {}): Attention {
  return {
    approvals: [
      { action: proposedAction, case_id: 'case-INV-1001', customer: 'Maple Synthetic Ltd', currency: 'CAD', outstanding_cents: 125000 },
      { action: secondAction, case_id: 'case-INV-3003', customer: 'Birch Synthetic Co', currency: 'CAD', outstanding_cents: 90000 },
    ],
    escalations: [{ escalation, customer: 'Cedar Synthetic Inc', currency: 'CAD', outstanding_cents: 320000 }],
    autonomy: autonomyOn,
    ...overrides,
  };
}

export const emptyAttention: Attention = { approvals: [], escalations: [], autonomy: autonomyOn };

export function activityItem(seq: number, overrides: Partial<ActivityItem> = {}): ActivityItem {
  return {
    seq,
    kind: 'message_sent',
    actor: 'policy:v2/rule:routine-reminders',
    case_id: 'case-INV-1001',
    ref: null,
    summary: `Reminder ${seq} sent to Maple automatically (policy v2, rule routine-reminders)`,
    synthetic: true,
    created_at: '2026-11-16T10:00:00Z',
    ...overrides,
  };
}

export function activity(items: ActivityItem[], after = 0): ActivityOut {
  return { items, cursor: items.length ? (items[items.length - 1] as ActivityItem).seq : after };
}

export const agents: AgentList = {
  agents: [
    {
      name: 'collections',
      title: 'Collections agent',
      model: 'claude-opus-5-5',
      can_write: true,
      paused: false,
      runs_today: 3,
      last_run_at: '2026-11-16T09:00:00Z',
      outcomes_7d: { proposed: 4, escalated: 1 },
    },
    {
      name: 'briefing',
      title: 'Owner briefing agent',
      model: 'claude-haiku-5-5',
      can_write: false,
      paused: true,
      runs_today: 0,
      last_run_at: null,
      outcomes_7d: {},
    },
  ],
};

export const policyV1: Policy = {
  version: 1,
  approval_ttl_minutes: 60,
  follow_up_days: 7,
  max_reminders: 3,
  autonomy_rules: [
    {
      id: 'small',
      description: 'Small reminders run automatically',
      action_kind: 'collection_reminder',
      mode: 'auto',
      max_amount_cents: 100000,
      require_evidence: true,
      require_undisputed: true,
    },
    {
      id: 'info',
      description: 'Information requests need me',
      action_kind: 'request_information',
      mode: 'approve',
      max_amount_cents: null,
      require_evidence: false,
      require_undisputed: false,
    },
  ],
  cadence_days: [3, 10],
  daily_model_budget_cents: 500,
  created_at: '2026-11-16T09:00:00Z',
  created_by: 'owner',
};

export const clock: Clock = {
  now: '2026-11-16T10:00:00Z',
  simulated: true,
  playing: false,
  speed: 60,
  offset_seconds: 0,
};

export const briefing: Briefing = {
  id: 'brf-1',
  created_at: '2026-11-16T08:00:00Z',
  period_start: '2026-11-15T08:00:00Z',
  period_end: '2026-11-16T08:00:00Z',
  run_id: 'run-9',
  headline: 'Two reminders went out; one dispute needs you.',
  sections: [{ title: 'Needs you', items: [{ text: 'Cedar disputes delivery', case_ids: ['case-INV-2002'], evidence_ids: ['ev-9'] }] }],
};

const notFound = { status: 404, body: { detail: 'No briefing yet' } };

/** What the app shell asks for on every page (queue, KPIs, feed, kill switch). */
export function shellRoutes() {
  return {
    'GET /attention': { body: emptyAttention },
    'GET /summary': { body: summary },
    'GET /activity': { body: activity([]) },
    'GET /autonomy': { body: autonomyOn },
    'GET /briefings/latest': notFound,
  };
}

/** Stores the owner token and opens the app at a route, e.g. "/cases". */
export function signedInAt(path = '/') {
  sessionStorage.setItem('financeops.ownerToken', TOKEN);
  window.location.hash = `#${path}`;
}
