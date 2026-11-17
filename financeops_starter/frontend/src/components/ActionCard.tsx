import type { Action } from '../api/client';
import { formatCents, formatTime, humanize, shortHash } from '../format';
import { PlainText, SimulatedTag } from './common';

type Decision = NonNullable<Action['autonomy']>;

const MODE_TEXT: Record<Decision['mode'], string> = {
  auto: 'runs automatically',
  approve: 'needs your approval',
  blocked: 'blocked by policy',
};

/** What the policy decided for this action (computed by the backend). */
export function AutonomyNote({ decision }: { decision: Decision }) {
  return (
    <div className={`autonomy-note mode-${decision.mode}`}>
      <strong>
        Policy v{decision.policy_version}
        {decision.rule_id ? ` · rule ${decision.rule_id}` : ' · no rule matched'}: {MODE_TEXT[decision.mode]}
      </strong>
      {decision.reasons.length > 0 && (
        <ul>
          {decision.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The exact message the server rendered, with the hash an approval binds to. */
export function ExactMessage({ action, compact = false }: { action: Action; compact?: boolean }) {
  const { payload } = action;
  const hash = action.payload_hash;
  if (!payload) return <p className="muted">No message rendered for this action.</p>;
  return (
    <section className={compact ? 'message compact' : 'message'} aria-label="Exact message">
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
      {!compact && <PlainText text={payload.body} />}
      {hash && (
        <p className="muted hash">
          Payload hash <code title={hash}>{shortHash(hash)}</code> · invoice v{payload.invoice_version}
          {payload.policy_version != null && <> · policy v{payload.policy_version}</>}
        </p>
      )}
    </section>
  );
}

/** One action: what was proposed, decided, queued and delivered. Decisions live in the action bar. */
export function ActionCard({ action }: { action: Action }) {
  const { approval, delivery } = action;
  return (
    <article className="action-card" aria-label={`Action ${action.id}`}>
      <header className="action-head">
        <h4>
          <span className="kind">{humanize(action.kind)}</span> <code>{action.id}</code>
        </h4>
        <span className={`badge status-${action.status}`}>{humanize(action.status)}</span>
      </header>
      <Stepper status={action.status} />

      {action.superseded_by && (
        <p className="superseded">
          Superseded by <code>{action.superseded_by}</code>: a newer plan replaced this one (kept for the record).
        </p>
      )}
      {action.autonomy && <AutonomyNote decision={action.autonomy} />}

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

      <ExactMessage action={action} />
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
  superseded: 0, // a newer plan replaced it before approval
};
const STOPPED: ReadonlySet<Action['status']> = new Set(['rejected', 'cancelled', 'failed', 'superseded']);

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
