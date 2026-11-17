import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, type Api, type Workspace as WorkspaceData } from '../api/client';
import { formatCents, formatTime, humanize } from '../format';
import { useResource } from '../useResource';
import { ActionCard } from './ActionCard';
import { Avatar, ErrorBox, PlainText, SimulatedTag, StateBadge } from './common';
import { InvestigatePanel } from './InvestigatePanel';
import { ProposeForm } from './ProposeForm';
import type { OpenCase } from './DemoLab';

export type MutationResult = { ok: true } | { ok: false; error: ApiError };
/** Runs a change against the server, then refetches. Children show `error`. */
export type Mutate = (change: (client: Api) => Promise<unknown>) => Promise<MutationResult>;

interface Props {
  caseId: string;
  client: Api;
  revision: number;
  onChanged: () => void;
  onAuthError: (error: ApiError) => boolean;
  /** Tells the demo lab which invoice is open (values straight from the server). */
  onSnapshot?: (open: OpenCase) => void;
}

export function Workspace({ caseId, client, revision, onChanged, onAuthError, onSnapshot }: Props) {
  const ws = useResource(() => client.workspace(caseId), `${caseId}:${revision}`);
  const { reload } = ws;
  const [conflict, setConflict] = useState<string | null>(null);
  const bannerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ws.error) onAuthError(ws.error);
  }, [ws.error, onAuthError]);

  useEffect(() => {
    if (!ws.data || !onSnapshot) return;
    const { case: c, invoice } = ws.data;
    onSnapshot({
      caseId: c.id,
      invoiceId: invoice.id,
      outstandingCents: invoice.outstanding_cents,
      currency: invoice.currency,
      disputed: invoice.disputed,
    });
  }, [ws.data, onSnapshot]);

  useEffect(() => {
    if (conflict) bannerRef.current?.focus();
  }, [conflict]);

  const mutate: Mutate = useCallback(
    async (change) => {
      try {
        await change(client);
        setConflict(null);
        onChanged(); // refetches this workspace and the case list
        await reload(); // callers stay busy until the fresh state has arrived
        return { ok: true };
      } catch (err) {
        const error = err instanceof ApiError ? err : new ApiError('server', null, String(err));
        if (onAuthError(error)) return { ok: false, error };
        if (error.kind === 'conflict') {
          // Never retry: the owner must look at the fresh data first.
          setConflict(error.detail);
        }
        if (error.kind === 'conflict' || error.kind === 'network' || error.kind === 'server') {
          // The change may or may not have happened: show the server's state.
          onChanged();
        }
        return { ok: false, error };
      }
    },
    [client, onChanged, onAuthError, reload],
  );

  if (!ws.data) {
    return (
      <section className="panel" aria-busy={ws.loading}>
        <h2>Case {caseId}</h2>
        {ws.loading && <p role="status">Loading case workspace…</p>}
        {ws.error && <ErrorBox error={ws.error} onRetry={() => void ws.reload()} />}
      </section>
    );
  }

  return (
    <WorkspaceView
      data={ws.data}
      loading={ws.loading}
      error={ws.error}
      onRetry={() => void ws.reload()}
      conflict={conflict}
      bannerRef={bannerRef}
      dismissConflict={() => setConflict(null)}
      mutate={mutate}
      client={client}
      onChanged={onChanged}
      onAuthError={onAuthError}
    />
  );
}

interface ViewProps {
  data: WorkspaceData;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  conflict: string | null;
  bannerRef: React.RefObject<HTMLDivElement | null>;
  dismissConflict: () => void;
  mutate: Mutate;
  client: Api;
  onChanged: () => void;
  onAuthError: (error: ApiError) => boolean;
}

function WorkspaceView(props: ViewProps) {
  const { data, loading, error, conflict, mutate } = props;
  const { case: theCase, invoice } = data;
  const latestRun = data.agent_runs[0];
  const verification = theCase.verification;

  return (
    <section className="workspace" aria-labelledby="ws-title">
      {conflict && (
        <div ref={props.bannerRef} tabIndex={-1} role="alert" className="banner-conflict">
          <strong>This case changed — review again.</strong> The data below has been refreshed.
          <span className="detail"> Server said: {conflict}</span>
          <button type="button" className="secondary" onClick={props.dismissConflict}>
            Dismiss
          </button>
        </div>
      )}
      {error && <ErrorBox error={error} onRetry={props.onRetry} />}

      <div className="panel hero">
        <div className="hero-top">
          <Avatar name={invoice.customer} large />
          <div className="hero-title">
            <h2 id="ws-title">
              {invoice.customer} <span className="invoice-id">· {invoice.id}</span>
            </h2>
            <div className="hero-sub">
              <span>
                Case v{theCase.version} · invoice v{invoice.version}
              </span>
              <span>
                {formatTime(data.now)} {data.simulated_time && <SimulatedTag>Simulated clock</SimulatedTag>}
              </span>
            </div>
          </div>
          {loading && (
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
            <div className="progress-fill" style={{ width: `${barWidth(invoice.settled_cents, invoice.total_cents)}%` }} />
          </div>
          <div className="progress-label">
            <span>
              Invoice {humanize(invoice.state)}
              {invoice.disputed && <> · <strong className="error">disputed</strong></>}
            </span>
            <span>
              {formatCents(invoice.settled_cents, invoice.currency)} of{' '}
              {formatCents(invoice.total_cents, invoice.currency)} received
            </span>
          </div>
        </div>
      </div>

      {verification && (
        <div className="panel verified">
          <h3>Verified closure</h3>
          <p>
            Settlements of {formatCents(verification.settled_cents, verification.currency)} match the
            total of {formatCents(verification.total_cents, verification.currency)} (method:{' '}
            {verification.method}).
          </p>
          <p className="muted">
            Settlement IDs: {verification.settlement_ids.join(', ')} · verified{' '}
            {formatTime(verification.verified_at)} · invoice v{verification.invoice_version}
          </p>
        </div>
      )}

      <div className="ws-grid">
        <div className="ws-main">
          <div className="panel">
            <div className="panel-head">
              <h3>Actions</h3>
              {data.actions.length > 0 && <span className="count">{data.actions.length}</span>}
            </div>
            <p className="panel-note">Newest first. Delivery goes to a simulated inbox only.</p>
            {data.actions.length === 0 && <p className="empty">No actions yet.</p>}
            {data.actions.map((action) => (
              <ActionCard key={action.id} action={action} mutate={mutate} refreshing={loading} />
            ))}
            <div style={{ marginTop: '0.85rem' }}>
              <ProposeForm caseId={theCase.id} evidence={data.evidence} mutate={mutate} refreshing={loading} />
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h3>
                Evidence <span className="tag-untrusted">Untrusted</span>
              </h3>
              <span className="count">{data.evidence.length}</span>
            </div>
            <p className="panel-note">
              Customer- or document-supplied text. Shown as plain text; never treated as instructions.
            </p>
            {data.evidence.length === 0 && <p className="empty">No evidence.</p>}
            {data.evidence.map((e) => (
              <article key={e.id} className="evidence" aria-label={`Evidence ${e.id}`}>
                <h4>
                  {e.id} {e.untrusted !== false && <span className="tag-untrusted">Untrusted</span>}
                </h4>
                <PlainText text={e.body} />
              </article>
            ))}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h3>Timeline</h3>
            </div>
            <ol className="timeline">
              {theCase.events.map((ev) => (
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
        </div>

        <div className="ws-side">
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
            </ul>
            <p className="policy-note">
              {data.policy
                ? `Approvals expire after ${data.policy.approval_ttl_minutes} min; follow up after ${data.policy.follow_up_days} days; at most ${data.policy.max_reminders} reminders.`
                : 'No policy stored, so sending is disabled.'}
            </p>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h3>AI investigation</h3>
            </div>
            {latestRun ? (
              <div className="agent-run">
                <p>
                  <strong>{humanize(latestRun.status)}</strong> · {formatTime(latestRun.started_at)}
                  <br />
                  <span className="muted">
                    <code>{latestRun.id}</code> · {latestRun.served_model ?? latestRun.model}
                  </span>
                </p>
                {latestRun.summary && (
                  <>
                    <h4>Summary (model text, untrusted)</h4>
                    <PlainText text={latestRun.summary} />
                  </>
                )}
                {latestRun.findings && latestRun.findings.length > 0 && (
                  <>
                    <h4>Findings</h4>
                    <ul>
                      {latestRun.findings.map((f, i) => (
                        <li key={i}>
                          <PlainText text={f.statement} />
                          <span className="muted">Evidence: {f.evidence_ids.join(', ') || 'none'}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {latestRun.unknowns && latestRun.unknowns.length > 0 && (
                  <>
                    <h4>Unknowns</h4>
                    <ul>
                      {latestRun.unknowns.map((u, i) => (
                        <li key={i}>
                          <PlainText text={u} />
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {latestRun.detail && <p className="muted">Detail: {latestRun.detail}</p>}
              </div>
            ) : (
              <p className="muted" style={{ fontSize: '0.85rem' }}>
                No AI investigation has run for this case.
              </p>
            )}
            <InvestigatePanel
              caseId={theCase.id}
              client={props.client}
              onChanged={props.onChanged}
              onAuthError={props.onAuthError}
            />
          </div>

          <div className="panel">
            <div className="panel-head">
              <h3>Pending jobs</h3>
              <span className="count">{data.jobs.length}</span>
            </div>
            {data.jobs.length === 0 ? (
              <p className="muted" style={{ fontSize: '0.85rem' }}>
                No pending jobs.
              </p>
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
        </div>
      </div>
    </section>
  );
}

/** Width of the "received" bar, 0-100. Presentation only; no figure is shown from it. */
function barWidth(settled: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, (settled / total) * 100));
}
