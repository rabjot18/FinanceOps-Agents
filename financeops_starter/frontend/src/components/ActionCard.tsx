import { useId, useState } from 'react';
import type { Action, ApiError } from '../api/client';
import { formatCents, formatTime, humanize, shortHash } from '../format';
import { PlainText, SimulatedTag } from './common';
import type { Mutate } from './Workspace';

interface Props {
  action: Action;
  mutate: Mutate;
  /** True while the workspace refetches: buttons wait for the fresh state. */
  refreshing: boolean;
}

/** One action: what was proposed, what was approved, queued and delivered. */
export function ActionCard({ action, mutate, refreshing }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [note, setNote] = useState('');
  const noteId = useId();
  const { payload, approval, delivery } = action;

  const run = async (change: Parameters<Mutate>[0]) => {
    setBusy(true);
    setError(null);
    const result = await mutate(change);
    setBusy(false);
    // A conflict shows the page-level banner instead.
    if (!result.ok && result.error.kind !== 'conflict') setError(result.error);
  };

  const locked = busy || refreshing;
  const hash = action.payload_hash;
  const canDecide = action.status === 'proposed';
  const canExecute = action.status === 'approved' && approval?.expired !== true;

  return (
    <article className="action-card" aria-label={`Action ${action.id}`}>
      <header className="action-head">
        <h4>
          <span className="kind">{humanize(action.kind)}</span> <code>{action.id}</code>
        </h4>
        <span className={`badge status-${action.status}`}>{humanize(action.status)}</span>
      </header>
      <Stepper status={action.status} />

      <dl className="lifecycle">
        <div>
          <dt>Proposed by</dt>
          <dd>{action.proposed_by}</dd>
        </div>
        <div>
          <dt>Approval</dt>
          <dd>
            {approval
              ? `${approval.decision} by ${approval.decided_by} at ${formatTime(approval.decided_at)}`
              : 'not decided'}
            {approval?.expires_at && (
              <>
                {' '}
                · expires {formatTime(approval.expires_at)}
                {approval.expired && <strong className="error"> (expired)</strong>}
              </>
            )}
            {approval?.note && <> · note: “{approval.note}”</>}
          </dd>
        </div>
        <div>
          <dt>Queued</dt>
          <dd>{action.executed_at ? `${formatTime(action.executed_at)} by ${action.executed_by ?? '?'}` : 'no'}</dd>
        </div>
        <div>
          <dt>
            Delivery <SimulatedTag />
          </dt>
          <dd>
            {delivery
              ? `${delivery.status} · attempts ${delivery.attempts}${delivery.external_id ? ` · ${delivery.external_id}` : ''}`
              : 'not queued'}
            {delivery?.last_error && <span className="error"> · {delivery.last_error}</span>}
          </dd>
        </div>
      </dl>

      {action.rationale && (
        <details className="rationale">
          <summary>Rationale (not part of the message)</summary>
          <PlainText text={action.rationale} />
        </details>
      )}

      {payload ? (
        <section className="message" aria-label="Exact message">
          <div className="message-bar">
            <h5>Exact message</h5>
            <SimulatedTag>Simulated delivery</SimulatedTag>
          </div>
          <dl>
            <div>
              <dt>To</dt>
              <dd>{payload.to ?? '(no contact on file)'}</dd>
            </div>
            <div>
              <dt>Subject</dt>
              <dd>{payload.subject}</dd>
            </div>
            <div>
              <dt>Amount</dt>
              <dd>{formatCents(payload.amount_cents, payload.currency)}</dd>
            </div>
          </dl>
          <PlainText text={payload.body} />
          {hash && (
            <p className="muted hash">
              Payload hash <code title={hash}>{shortHash(hash)}</code> · invoice v{payload.invoice_version}
              {payload.policy_version != null && <> · policy v{payload.policy_version}</>}
            </p>
          )}
        </section>
      ) : (
        <p className="muted">No message rendered for this action.</p>
      )}

      {error && (
        <p role="alert" className="error">
          {error.detail}
        </p>
      )}

      {canDecide && (
        <div className="decision">
          <button
            type="button"
            disabled={locked || !hash}
            onClick={() => hash && void run((c) => c.approve(action.id, hash))}
          >
            Approve this exact message
          </button>
          {!hash && <span className="muted">Nothing to approve: no message hash.</span>}
          <label htmlFor={noteId}>Rejection note (optional)</label>
          <textarea id={noteId} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          <button
            type="button"
            className="danger"
            disabled={locked}
            onClick={() => void run((c) => c.reject(action.id, note.trim() || null))}
          >
            Reject
          </button>
        </div>
      )}

      {action.status === 'approved' && (
        <div className="decision">
          <button type="button" disabled={locked || !canExecute} onClick={() => void run((c) => c.execute(action.id))}>
            Execute (queue for simulated delivery)
          </button>
          {!canExecute && <span className="muted">The approval expired; it cannot be queued.</span>}
        </div>
      )}
    </article>
  );
}

// The lifecycle the backend reports, drawn as steps. Display only: the status
// comes from the server and nothing here decides what is allowed.
const STEPS = ['Proposed', 'Approved', 'Queued', 'Delivered'] as const;
const STEP_OF: Record<Action['status'], number> = {
  proposed: 0,
  approved: 1,
  rejected: 1,
  queued: 2,
  dispatched: 3, // sent to the (simulated) inbox
  cancelled: 2,
  failed: 2,
  follow_up_due: 3,
  executed_simulated: 3,
};
const STOPPED: ReadonlySet<Action['status']> = new Set(['rejected', 'cancelled', 'failed']);

function Stepper({ status }: { status: Action['status'] }) {
  const current = STEP_OF[status];
  const stopped = STOPPED.has(status);
  const complete = current === STEPS.length - 1 && !stopped;
  const className = ['stepper', stopped ? 'stopped' : '', complete ? 'complete' : ''].join(' ').trim();
  return (
    <ol className={className} aria-hidden="true">
      {STEPS.map((label, i) => (
        <li key={label} className={i < current ? 'done' : i === current ? 'current' : ''}>
          {i === current && stopped ? humanize(status) : label}
        </li>
      ))}
    </ol>
  );
}
