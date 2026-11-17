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
export function api(token: string | null) {
  return {
    listCases: () => request<CaseListOut>('GET', '/cases', token),
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
