import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, type Api, type Workspace as WorkspaceData } from '../api/client';
import { formatCents, formatTime, humanize } from '../format';
import { useResource } from '../useResource';
import { ActionCard } from './ActionCard';
import { ErrorBox, PlainText, SimulatedTag, StateBadge } from './common';
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

      <div className="panel">
        <div className="ws-head">
          <h2 id="ws-title">
            {invoice.customer} <span className="muted">· {invoice.id}</span>
          </h2>
          <StateBadge state={theCase.state} />
          {loading && (
            <span role="status" className="muted">
              Refreshing…
            </span>
          )}
        </div>
        <dl className="facts">
          <div>
            <dt>Total</dt>
            <dd>{formatCents(invoice.total_cents, invoice.currency)}</dd>
          </div>
          <div>
            <dt>Settled</dt>
            <dd>{formatCents(invoice.settled_cents, invoice.currency)}</dd>
          </div>
          <div>
            <dt>Outstanding</dt>
            <dd>{formatCents(invoice.outstanding_cents, invoice.currency)}</dd>
          </div>
          <div>
            <dt>Invoice state</dt>
            <dd>
              {humanize(invoice.state)}
              {invoice.disputed && <span className="badge state-needs_information">disputed</span>}
            </dd>
          </div>
          <div>
            <dt>Case / invoice version</dt>
            <dd>
              v{theCase.version} / v{invoice.version}
            </dd>
          </div>
          <div>
            <dt>Clock</dt>
            <dd>
              {formatTime(data.now)} {data.simulated_time && <SimulatedTag>Simulated clock</SimulatedTag>}
            </dd>
          </div>
        </dl>
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

      <div className="panel">
        <h3>Sending</h3>
        <p>
          Sending is <strong>{data.sending_enabled ? 'enabled' : 'disabled'}</strong>{' '}
          <SimulatedTag>Simulated inbox only</SimulatedTag>
        </p>
        <ul className="plain-list">
          <li>Contact on file: {data.contact_on_file ? 'yes' : 'no'}</li>
          <li>Reminders sent: {data.reminders_sent}</li>
          {data.policy ? (
            <li>
              Policy v{data.policy.version}: approvals expire after {data.policy.approval_ttl_minutes} min,
              follow up after {data.policy.follow_up_days} days, at most {data.policy.max_reminders}{' '}
              reminders
            </li>
          ) : (
            <li>No policy stored, so sending is disabled.</li>
          )}
        </ul>
      </div>

      <div className="panel">
        <h3>Actions</h3>
        <p className="muted">Newest first. Delivery goes to a simulated inbox only.</p>
        {data.actions.length === 0 && <p>No actions yet.</p>}
        {data.actions.map((action) => (
          <ActionCard key={action.id} action={action} mutate={mutate} refreshing={loading} />
        ))}
        <ProposeForm caseId={theCase.id} evidence={data.evidence} mutate={mutate} refreshing={loading} />
      </div>

      <div className="panel">
        <h3>AI investigation</h3>
        {latestRun ? (
          <div className="agent-run">
            <p>
              Latest run <code>{latestRun.id}</code>: <strong>{humanize(latestRun.status)}</strong> ·{' '}
              {formatTime(latestRun.started_at)} · model {latestRun.served_model ?? latestRun.model}
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
          <p>No AI investigation has run for this case.</p>
        )}
        <InvestigatePanel
          caseId={theCase.id}
          client={props.client}
          onChanged={props.onChanged}
          onAuthError={props.onAuthError}
        />
      </div>

      <div className="panel">
        <h3>
          Evidence <span className="tag-untrusted">Untrusted</span>
        </h3>
        <p className="muted">
          Customer- or document-supplied text. Shown as plain text; never treated as instructions.
        </p>
        {data.evidence.length === 0 && <p>No evidence.</p>}
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
        <h3>Pending jobs</h3>
        {data.jobs.length === 0 ? (
          <p>No pending jobs.</p>
        ) : (
          <ul className="plain-list">
            {data.jobs.map((j) => (
              <li key={j.id}>
                {humanize(j.kind)} · {j.status} · due {formatTime(j.due_at)} · attempts {j.attempts}
                {j.detail && <span className="muted"> · {j.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel">
        <h3>Timeline</h3>
        <ol className="timeline">
          {theCase.events.map((ev) => (
            <li key={ev.event_id}>
              <span className="muted">{formatTime(ev.created_at)}</span>{' '}
              {ev.prior_state ? `${humanize(ev.prior_state)} → ` : ''}
              <strong>{humanize(ev.next_state)}</strong> · {ev.reason}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
