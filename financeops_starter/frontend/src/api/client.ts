// A small typed wrapper around fetch. Every shape comes from the generated
// contract types (schema.d.ts, made by `npm run gen:api` from the frozen
// openapi.json), so a backend shape change shows up as a type error here.
import type { components } from './schema';

type Schemas = components['schemas'];
export type CaseSummary = Schemas['CaseSummary'];
export type CaseListOut = Schemas['CaseListOut'];
export type Workspace = Schemas['WorkspaceOut'];
export type Action = Schemas['ActionOut'];
export type AgentRunSummary = Schemas['AgentRunSummary'];
export type AgentRun = Schemas['AgentRunOut'];
export type CaseState = Schemas['CaseOut']['state'];
export type ProposalIn = Schemas['ProposalIn'];
export type ScenarioList = Schemas['ScenarioListOut'];
export type ScenarioLoad = Schemas['ScenarioLoadOut'];
export type InvoiceOut = Schemas['InvoiceOut'];
export type ReplyOut = Schemas['ReplyOut'];
export type Inbox = Schemas['InboxOut'];
export type WorkerRunIn = Schemas['WorkerRunIn'];
export type WorkerRunOut = Schemas['WorkerRunOut'];
export type SettlementIn = Schemas['SettlementIn'];
export type CaseListParams = {
  state?: CaseState | null;
  q?: string | null;
  sort?: 'updated' | 'outstanding' | 'overdue' | 'customer';
  limit?: number;
  cursor?: string | null;
};
export type Attention = Schemas['AttentionOut'];
export type AttentionApproval = Schemas['AttentionApproval'];
export type AttentionEscalation = Schemas['AttentionEscalation'];
export type Summary = Schemas['SummaryOut'];
export type ActivityOut = Schemas['ActivityOut'];
export type ActivityItem = Schemas['ActivityItem'];
export type Escalation = Schemas['EscalationOut'];
export type AutonomyStatus = Schemas['AutonomyStatus'];
export type AgentInfo = Schemas['AgentInfo'];
export type AgentList = Schemas['AgentListOut'];
export type Briefing = Schemas['BriefingOut'];
export type Policy = Schemas['PolicyOut'];
export type PolicyIn = Schemas['PolicyIn'];
export type AutonomyRule = Schemas['AutonomyRule'];
export type AutonomyRuleIn = Schemas['AutonomyRuleIn'];
export type PolicyPreview = Schemas['PolicyPreviewOut'];
export type Clock = Schemas['ClockOut'];
export type ClockIn = Schemas['ClockIn'];
export type GeneratorIn = Schemas['GeneratorIn'];
export type GeneratorRun = Schemas['GeneratorRunOut'];
// Milestone 6: the flow board, outcomes and the one-click demo run.
export type FlowOut = Schemas['FlowOut'];
export type FlowCase = Schemas['FlowCase'];
export type FlowStep = Schemas['FlowStep'];
export type FlowStageOut = Schemas['FlowStageOut'];
export type FlowStage = FlowStageOut['id'];
export type FlowActor = FlowStep['actor'];
export type CaseFlowOut = Schemas['CaseFlowOut'];
export type OutcomesOut = Schemas['OutcomesOut'];
export type PersonaResult = Schemas['PersonaResult'];
export type SpendOut = Schemas['SpendOut'];
export type DemoRunIn = Schemas['DemoRunIn'];
export type DemoRunOut = Schemas['DemoRunOut'];

export type ErrorKind =
  | 'unauthorized' // 401: token missing or wrong
  | 'conflict' // 409: the case changed since it was read
  | 'invalid' // 422: the server rejected the input; `detail` says why
  | 'not_found' // 404
  | 'unavailable' // 503: e.g. no owner token configured, model not configured
  | 'server' // any other HTTP error
  | 'network'; // the request never got an HTTP answer

export class ApiError extends Error {
  constructor(
    readonly kind: ErrorKind,
    readonly status: number | null,
    readonly detail: string,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

function kindFor(status: number): ErrorKind {
  if (status === 401) return 'unauthorized';
  if (status === 409) return 'conflict';
  if (status === 422) return 'invalid';
  if (status === 404) return 'not_found';
  if (status === 503) return 'unavailable';
  return 'server';
}

// FastAPI sends `detail` as a string for domain errors and as a list of
// {loc, msg} objects for request validation errors. Show either as text.
function detailText(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: unknown };
        const where = Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body').join('.') : '';
        return where ? `${where}: ${String(item.msg)}` : String(item.msg);
      })
      .join('; ');
  }
  return `Request failed (HTTP ${status})`;
}

async function request<T>(
  method: 'GET' | 'POST',
  path: string,
  token: string | null,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('network', null, 'Could not reach the FinanceOps server.');
  }

  let parsed: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!response.ok) {
    throw new ApiError(kindFor(response.status), response.status, detailText(parsed, response.status));
  }
  return parsed as T;
}

const seg = encodeURIComponent;

/** All calls the owner console makes. `token` is the owner's bearer token. */
/** "?a=1&b=2" from the set (non-empty) values, in the order given. */
function query(params: Record<string, string | number | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

export function api(token: string | null) {
  return {
    listCases: (params: CaseListParams = {}) =>
      request<CaseListOut>(
        'GET',
        `/cases${query({ state: params.state, q: params.q, sort: params.sort, limit: params.limit, cursor: params.cursor })}`,
        token,
      ),
    workspace: (caseId: string) => request<Workspace>('GET', `/cases/${seg(caseId)}/workspace`, token),
    propose: (caseId: string, body: ProposalIn) =>
      request<Action>('POST', `/cases/${seg(caseId)}/actions`, token, body),
    approve: (actionId: string, payloadHash: string) =>
      request<Action>('POST', `/actions/${seg(actionId)}/approve`, token, { payload_hash: payloadHash }),
    reject: (actionId: string, note: string | null) =>
      request<Action>('POST', `/actions/${seg(actionId)}/reject`, token, { note }),
    execute: (actionId: string) => request<Action>('POST', `/actions/${seg(actionId)}/execute`, token),
    /** Calls a PAID model on the server. Only after an explicit owner confirm. */
    investigate: (caseId: string) => request<AgentRun>('POST', `/cases/${seg(caseId)}/investigate`, token),
    agentRun: (runId: string) => request<AgentRun>('GET', `/agent-runs/${seg(runId)}`, token),

    // Milestone 5: the owner's overview, live feed and controls.
    attention: () => request<Attention>('GET', '/attention', token),
    summary: () => request<Summary>('GET', '/summary', token),
    activity: (after: number, limit = 200) => request<ActivityOut>('GET', `/activity${query({ after, limit })}`, token),
    resolveEscalation: (escalationId: string, note: string | null) =>
      request<Escalation>('POST', `/escalations/${seg(escalationId)}/resolve`, token, { note }),
    autonomy: () => request<AutonomyStatus>('GET', '/autonomy', token),
    pauseAutonomy: (reason: string | null) => request<AutonomyStatus>('POST', '/autonomy/pause', token, { reason }),
    resumeAutonomy: () => request<AutonomyStatus>('POST', '/autonomy/resume', token),
    agents: () => request<AgentList>('GET', '/agents', token),
    pauseAgent: (name: string) => request<AgentInfo>('POST', `/agents/${seg(name)}/pause`, token),
    resumeAgent: (name: string) => request<AgentInfo>('POST', `/agents/${seg(name)}/resume`, token),
    latestBriefing: () => request<Briefing>('GET', '/briefings/latest', token),
    policy: () => request<Policy>('GET', '/policy', token),
    savePolicy: (body: PolicyIn) => request<Policy>('POST', '/policy', token, body),
    previewPolicy: (body: PolicyIn) => request<PolicyPreview>('POST', '/policy/preview', token, body),

    // Milestone 6: every case's stage, one case's path, and the run's results.
    flow: () => request<FlowOut>('GET', '/flow', token),
    caseFlow: (caseId: string) => request<CaseFlowOut>('GET', `/cases/${seg(caseId)}/flow`, token),
    outcomes: () => request<OutcomesOut>('GET', '/outcomes', token),

    // Demo lab: synthetic events, simulated delivery. Only exists when the
    // server runs with FINANCEOPS_ENABLE_DEMO=1.
    scenarios: () => request<ScenarioList>('GET', '/demo/scenarios', token),
    loadScenario: (name: string) => request<ScenarioLoad>('POST', `/demo/scenarios/${seg(name)}`, token),
    settle: (body: SettlementIn) => request<InvoiceOut>('POST', '/demo/settlements', token, body),
    reply: (invoiceId: string, evidenceId: string, text: string) =>
      request<ReplyOut>('POST', '/demo/replies', token, {
        invoice_id: invoiceId,
        evidence_id: evidenceId,
        body: text,
      }),
    dispute: (invoiceId: string, disputed: boolean) =>
      request<InvoiceOut>('POST', '/demo/disputes', token, { invoice_id: invoiceId, disputed }),
    runWorker: (advanceMinutes: number) =>
      // run_investigations stays false: re-investigation would call the paid model.
      request<WorkerRunOut>('POST', '/demo/worker/run', token, {
        advance_minutes: advanceMinutes,
        run_investigations: false,
      } satisfies WorkerRunIn),
    inbox: () => request<Inbox>('GET', '/demo/inbox', token),
    clock: () => request<Clock>('GET', '/demo/clock', token),
    setClock: (body: ClockIn) => request<Clock>('POST', '/demo/clock', token, body),
    runGenerator: (body: GeneratorIn) => request<GeneratorRun>('POST', '/demo/generator/run', token, body),
    /** One click: synthetic customers, a month of invoices, the clock playing. */
    runDemo: (body: DemoRunIn) => request<DemoRunOut>('POST', '/demo/run', token, body),
  };
}

export type Api = ReturnType<typeof api>;

/** A short random id for things the client must name (actions, settlements). */
export function newId(prefix: string): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 10)
      : Math.random().toString(36).slice(2, 12);
  return `${prefix}-${random}`;
}

/** Any thrown value as an ApiError (unexpected ones count as server errors). */
export function asApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError('server', null, String(err));
}

/** A frozen Milestone 5 route whose logic has not landed yet (503 "Not built yet ..."). */
export function isNotBuilt(error: ApiError | null | undefined): boolean {
  return !!error && error.kind === 'unavailable' && /not built yet/i.test(error.detail);
}
