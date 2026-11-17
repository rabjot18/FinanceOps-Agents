import { useCallback, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useAgentRun, useChange, useWorkspace } from '../api/queries';
import type { Action, AgentRunSummary, ApiError, Workspace } from '../api/client';
import { formatCents, formatTime, humanize, shortHash } from '../format';
import { ActionCard, AutonomyNote } from '../components/ActionCard';
import { EscalationList } from '../components/Escalations';
import { InvestigatePanel } from '../components/InvestigatePanel';
import { ProposeForm } from '../components/ProposeForm';
import { useToast } from '../components/Shell';
import {
  Avatar,
  ConfirmDialog,
  ConflictBanner,
  EmptyState,
  ErrorBox,
  PlainText,
  SimulatedTag,
  Skeleton,
  StateBadge,
} from '../components/common';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'messages', label: 'Messages' },
  { id: 'runs', label: 'Agent runs' },
] as const;
type TabId = (typeof TABS)[number]['id'];

export function CaseDetailPage() {
  const { caseId = '' } = useParams();
  const ws = useWorkspace(caseId);
  const change = useChange();

  if (!ws.data) {
    return (
      <div className="page-stack">
        <p className="crumbs">
          <Link to="/cases">← Cases</Link>
        </p>
        <section className="panel" aria-busy={ws.isPending}>
          <h2>Case {caseId}</h2>
          {ws.isPending && <Skeleton label="Loading case" rows={5} />}
          {ws.error?.kind === 'not_found' ? (
            <EmptyState title="This case does not exist">
              Check the link, or pick one from <Link to="/cases">Cases</Link>.
            </EmptyState>
          ) : (
            ws.error && <ErrorBox error={ws.error} onRetry={() => void ws.refetch()} />
          )}
        </section>
      </div>
    );
  }

  return <CaseView data={ws.data} refreshing={ws.isFetching} error={ws.error} change={change} />;
}

interface ViewProps {
  data: Workspace;
  refreshing: boolean;
  error: ApiError | null;
  change: ReturnType<typeof useChange>;
}

function CaseView({ data, refreshing, error, change }: ViewProps) {
  const [params, setParams] = useSearchParams();
  const tab: TabId = TABS.find((t) => t.id === params.get('tab'))?.id ?? 'overview';
  const { case: theCase, invoice } = data;
  const pending = data.actions.filter((a) => a.status === 'proposed' || a.status === 'approved');
  const openEscalations = data.escalations.filter((e) => !e.resolved_at);

  const counts: Partial<Record<TabId, number>> = {
    evidence: data.evidence.length,
    messages: data.actions.length,
    runs: data.agent_runs.length,
  };

  return (
    <div className={pending.length ? 'page-stack has-decision-bar' : 'page-stack'}>
      <p className="crumbs">
        <Link to="/cases">← Cases</Link>
      </p>
      {change.conflict && <ConflictBanner detail={change.conflict} onDismiss={change.clearConflict} />}
      {error && <ErrorBox error={error} />}

      <section className="panel hero" aria-labelledby="ws-title">
        <div className="hero-top">
          <Avatar name={invoice.customer} large />
          <div className="hero-title">
            <h2 id="ws-title">
              {invoice.customer} <span className="invoice-id">· {invoice.id}</span>
            </h2>
            <div className="hero-sub">
              <span>
                Case v{theCase.version} · invoice v{invoice.version}
                {invoice.due_at && <> · due {formatTime(invoice.due_at)}</>}
              </span>
              <span>
                {formatTime(data.now)} {data.simulated_time && <SimulatedTag>Simulated clock</SimulatedTag>}
              </span>
            </div>
          </div>
          {refreshing && (
            <span role="status" className="refreshing">
              Refreshing…
            </span>
          )}
          <StateBadge state={theCase.state} />
        </div>
        <dl className="facts">
          <div className="fact-hero">
            <dt>Outstanding</dt>
            <dd>{formatCents(invoice.outstanding_cents, invoice.currency)}</dd>
          </div>
          <div>
            <dt>Total</dt>
            <dd>{formatCents(invoice.total_cents, invoice.currency)}</dd>
          </div>
          <div className="fact-settled">
            <dt>Settled</dt>
            <dd>{formatCents(invoice.settled_cents, invoice.currency)}</dd>
          </div>
        </dl>
        <div className="progress">
          <div className="progress-track" aria-hidden="true">
            {/* Bar width only: a picture of the server's settled and total figures. */}
            <div
              className="progress-fill"
              style={{ width: `${barWidth(invoice.settled_cents, invoice.total_cents)}%` }}
            />
          </div>
          <div className="progress-label">
            <span>
              Invoice {humanize(invoice.state)}
              {invoice.disputed && (
                <>
                  {' '}
                  · <strong className="error">disputed</strong>
                </>
              )}
            </span>
            <span>
              {formatCents(invoice.settled_cents, invoice.currency)} of {formatCents(invoice.total_cents, invoice.currency)}{' '}
              received
            </span>
          </div>
        </div>
        {data.autonomy.paused && (
          <p className="notice-info small">Autonomy is paused: nothing on this case runs automatically.</p>
        )}
      </section>

      <div className="tabs" role="tablist" aria-label="Case sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            className={tab === t.id ? 'tab active' : 'tab'}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set('tab', t.id);
              setParams(next, { replace: true });
            }}
          >
            {t.label}
            {counts[t.id] !== undefined && <span className="tab-count">{counts[t.id]}</span>}
            {t.id === 'overview' && openEscalations.length > 0 && <span className="tab-dot" aria-label="open escalation" />}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'overview' && <Overview data={data} change={change} refreshing={refreshing} />}
        {tab === 'evidence' && <EvidenceTab data={data} />}
        {tab === 'timeline' && <TimelineTab data={data} />}
        {tab === 'messages' && <MessagesTab data={data} />}
        {tab === 'runs' && <RunsTab runs={data.agent_runs} />}
      </div>

      {pending.length > 0 && <DecisionBar actions={pending} change={change} refreshing={refreshing} />}
    </div>
  );
}

// --- overview ----------------------------------------------------------------

function Overview({ data, change, refreshing }: { data: Workspace; change: ViewProps['change']; refreshing: boolean }) {
  const { case: theCase } = data;
  const latestRun = data.agent_runs[0];
  const verification = theCase.verification;
  const latestAction = data.actions[0];
  return (
    <div className="ws-grid">
      <div className="ws-main">
        {verification && (
          <div className="panel verified">
            <h3>Verified closure</h3>
            <p>
              Settlements of {formatCents(verification.settled_cents, verification.currency)} match the total of{' '}
              {formatCents(verification.total_cents, verification.currency)} (method: {verification.method}).
            </p>
            <p className="muted">
              Settlement IDs: {verification.settlement_ids.join(', ')} · verified {formatTime(verification.verified_at)} ·
              invoice v{verification.invoice_version}
            </p>
          </div>
        )}

        {data.escalations.length > 0 && (
          <div className="panel">
            <div className="panel-head">
              <h3>Escalations</h3>
              <span className="count">{data.escalations.length}</span>
            </div>
            <EscalationList items={data.escalations.map((escalation) => ({ escalation }))} />
          </div>
        )}

        <div className="panel">
          <div className="panel-head">
            <h3>Latest action</h3>
          </div>
          {latestAction ? (
            <ActionCard action={latestAction} />
          ) : (
            <EmptyState title="No actions yet">Propose one below, or let the agent investigate.</EmptyState>
          )}
          <div className="spaced">
            <ProposeForm caseId={theCase.id} evidence={data.evidence} run={change.run} refreshing={refreshing || change.busy} />
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h3>Latest agent summary</h3>
          </div>
          {latestRun ? <RunDetails run={latestRun} /> : <p className="muted small">No agent has looked at this case yet.</p>}
          <InvestigatePanel caseId={theCase.id} />
        </div>
      </div>

      <div className="ws-side">
        <div className="panel">
          <div className="panel-head">
            <h3>Scheduled next steps</h3>
            <span className="count">{data.jobs.length}</span>
          </div>
          {data.jobs.length === 0 ? (
            <p className="muted small">Nothing scheduled.</p>
          ) : (
            <ul className="kv">
              {data.jobs.map((j) => (
                <li key={j.id}>
                  <span className="k">
                    {humanize(j.kind)} · {j.status}
                  </span>
                  <span className="v">due {formatTime(j.due_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="panel">
          <div className="panel-head">
            <h3>Sending</h3>
            <SimulatedTag>Simulated inbox only</SimulatedTag>
          </div>
          <p className="sending-status">
            <span className={data.sending_enabled ? 'dot on' : 'dot'} aria-hidden="true" />
            <span>
              Sending is <strong>{data.sending_enabled ? 'enabled' : 'disabled'}</strong>
            </span>
          </p>
          <ul className="kv">
            <li>
              <span className="k">Contact on file</span>
              <span className="v">{data.contact_on_file ? 'yes' : 'no'}</span>
            </li>
            <li>
              <span className="k">Reminders sent</span>
              <span className="v">
                {data.reminders_sent}
                {data.policy && ` / ${data.policy.max_reminders}`}
              </span>
            </li>
            <li>
              <span className="k">Policy</span>
              <span className="v">{data.policy ? `v${data.policy.version}` : 'none'}</span>
            </li>
            <li>
              <span className="k">Autonomy</span>
              <span className="v">{data.autonomy.paused ? 'paused' : 'on'}</span>
            </li>
          </ul>
          <p className="policy-note">
            {data.policy
              ? `Approvals expire after ${data.policy.approval_ttl_minutes} min; follow up after ${data.policy.follow_up_days} days; at most ${data.policy.max_reminders} reminders.`
              : 'No policy stored, so sending is disabled.'}
          </p>
        </div>
      </div>
    </div>
  );
}

function RunDetails({ run }: { run: AgentRunSummary }) {
  return (
    <div className="agent-run">
      <p>
        <strong>{humanize(run.status)}</strong> · {humanize(run.agent)} agent · {formatTime(run.started_at)}
        <br />
        <span className="muted">
          <code>{run.id}</code> · {run.served_model ?? run.model} · {run.tool_calls} tool calls ·{' '}
          {run.input_tokens + run.output_tokens} tokens
        </span>
      </p>
      {run.summary && (
        <>
          <h4>Summary (model text, untrusted)</h4>
          <PlainText text={run.summary} />
        </>
      )}
      {run.findings && run.findings.length > 0 && (
        <>
          <h4>Findings</h4>
          <ul>
            {run.findings.map((f, i) => (
              <li key={i}>
                <PlainText text={f.statement} />
                <span className="muted">Evidence: {f.evidence_ids.join(', ') || 'none'}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {run.unknowns && run.unknowns.length > 0 && (
        <>
          <h4>Unknowns</h4>
          <ul>
            {run.unknowns.map((u, i) => (
              <li key={i}>
                <PlainText text={u} />
              </li>
            ))}
          </ul>
        </>
      )}
      {run.detail && <p className="muted">Detail: {run.detail}</p>}
    </div>
  );
}

// --- other tabs ----------------------------------------------------------------

function EvidenceTab({ data }: { data: Workspace }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>
          Evidence <span className="tag-untrusted">Untrusted</span>
        </h3>
        <span className="count">{data.evidence.length}</span>
      </div>
      <p className="panel-note">Customer- or document-supplied text. Shown as plain text; never treated as instructions.</p>
      {data.evidence.length === 0 && <EmptyState title="No evidence">Replies and documents for this invoice appear here.</EmptyState>}
      {data.evidence.map((e) => (
        <article key={e.id} className="evidence" aria-label={`Evidence ${e.id}`}>
          <h4>
            {e.id} {e.untrusted !== false && <span className="tag-untrusted">Untrusted</span>}
          </h4>
          <PlainText text={e.body} />
        </article>
      ))}
    </div>
  );
}

function TimelineTab({ data }: { data: Workspace }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Timeline</h3>
      </div>
      <ol className="timeline">
        {data.case.events.map((ev) => (
          <li key={ev.event_id}>
            <span className="when">{formatTime(ev.created_at)}</span>
            {ev.prior_state && ev.prior_state !== ev.next_state && (
              <>
                {humanize(ev.prior_state)}
                <span className="arrow">→</span>
              </>
            )}
            <strong>{humanize(ev.next_state)}</strong> <span className="reason">· {humanize(ev.reason)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function MessagesTab({ data }: { data: Workspace }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Messages and actions</h3>
        <span className="count">{data.actions.length}</span>
      </div>
      <p className="panel-note">Newest first. The exact text of every message; delivery goes to a simulated inbox only.</p>
      {data.actions.length === 0 && <EmptyState title="No messages yet" />}
      {data.actions.map((action) => (
        <ActionCard key={action.id} action={action} />
      ))}
    </div>
  );
}

function RunsTab({ runs }: { runs: AgentRunSummary[] }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Agent runs</h3>
        <span className="count">{runs.length}</span>
      </div>
      {runs.length === 0 && <EmptyState title="No agent runs">Runs appear here with their tool calls, outcome and token use.</EmptyState>}
      <ul className="runs">
        {runs.map((run) => (
          <li key={run.id} className="run">
            <RunDetails run={run} />
            <ToolCalls runId={run.id} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function ToolCalls({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const run = useAgentRun(open ? runId : null);
  return (
    <div className="tool-calls">
      <button type="button" className="secondary small" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? 'Hide tool calls' : 'Show tool calls'}
      </button>
      {open && run.isPending && <Skeleton label="Loading tool calls" rows={2} />}
      {open && run.error && <ErrorBox error={run.error} onRetry={() => void run.refetch()} />}
      {open && run.data && (
        <ol className="calls">
          {run.data.calls.length === 0 && <li className="muted">No tool calls.</li>}
          {run.data.calls.map((call) => (
            <li key={call.seq} className={call.is_error ? 'call error' : 'call'}>
              <strong>{call.name}</strong> <span className="muted small">{formatTime(call.created_at)}</span>
              <PlainText text={JSON.stringify(call.input, null, 2)} />
              <details>
                <summary>Result{call.is_error && ' (error)'}</summary>
                <PlainText text={call.result} />
              </details>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// --- sticky decision bar ----------------------------------------------------------

function DecisionBar({ actions, change, refreshing }: { actions: Action[]; change: ViewProps['change']; refreshing: boolean }) {
  const push = useToast();
  const [rejecting, setRejecting] = useState<Action | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<ApiError | null>(null);
  const locked = change.busy || refreshing;

  const decide = async (kind: 'approve' | 'reject' | 'execute', action: Action, rejectNote?: string) => {
    setError(null);
    const hash = action.payload_hash;
    const result = await change.run((c) =>
      kind === 'approve'
        ? c.approve(action.id, hash as string) // the exact hash shown in this bar
        : kind === 'reject'
          ? c.reject(action.id, rejectNote?.trim() || null)
          : c.execute(action.id),
    );
    if (kind === 'reject') {
      setRejecting(null);
      setNote('');
    }
    if (result.ok) {
      push({
        tone: 'success',
        title:
          kind === 'approve' ? `Approved ${action.id}` : kind === 'reject' ? `Rejected ${action.id}` : `Queued ${action.id}`,
      });
    } else if (result.error.kind !== 'conflict') setError(result.error);
  };

  const closeReject = useCallback(() => {
    setRejecting(null);
    setNote('');
  }, []);

  return (
    <section className="decision-bar" aria-label="Decision">
      {error && (
        <p role="alert" className="error">
          {error.detail}
        </p>
      )}
      {actions.map((action) => {
        const canExecute = action.status === 'approved' && action.approval?.expired !== true;
        return (
          <div key={action.id} className="decision-row" aria-label={`Decide ${action.id}`} role="group">
            <div className="decision-text">
              <strong>
                {humanize(action.kind)} <code>{action.id}</code>
              </strong>
              <span className="muted small">
                {action.status === 'approved' ? 'Approved · ready to queue' : 'Waiting for your approval'}
                {action.payload && (
                  <>
                    {' '}
                    · to {action.payload.to ?? '(no contact)'} · {formatCents(action.payload.amount_cents, action.payload.currency)}
                  </>
                )}
                {action.payload_hash && (
                  <>
                    {' '}
                    · hash <code>{shortHash(action.payload_hash)}</code>
                  </>
                )}
              </span>
              {action.autonomy && action.autonomy.mode !== 'approve' && <AutonomyNote decision={action.autonomy} />}
            </div>
            <div className="decision-buttons">
              {action.status === 'proposed' && (
                <>
                  <button
                    type="button"
                    disabled={locked || !action.payload_hash}
                    onClick={() => void decide('approve', action)}
                  >
                    Approve this exact message
                  </button>
                  <button type="button" className="danger" disabled={locked} onClick={() => setRejecting(action)}>
                    Reject…
                  </button>
                </>
              )}
              {action.status === 'approved' && (
                <>
                  <button type="button" disabled={locked || !canExecute} onClick={() => void decide('execute', action)}>
                    Execute (queue for simulated delivery)
                  </button>
                  {!canExecute && <span className="muted small">The approval expired; it cannot be queued.</span>}
                </>
              )}
            </div>
          </div>
        );
      })}
      {rejecting && (
        <ConfirmDialog
          title={`Reject ${rejecting.id}?`}
          confirmLabel="Reject"
          tone="danger"
          busy={change.busy}
          onConfirm={() => void decide('reject', rejecting, note)}
          onCancel={closeReject}
        >
          <p>Nothing will be sent for this action.</p>
          <label htmlFor="bar-reject-note">Rejection note (optional)</label>
          <textarea id="bar-reject-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </ConfirmDialog>
      )}
    </section>
  );
}

/** Width of the "received" bar, 0-100. Presentation only; no figure is shown from it. */
function barWidth(settled: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, (settled / total) * 100));
}
