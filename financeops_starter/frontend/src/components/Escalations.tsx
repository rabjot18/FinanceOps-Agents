import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { useChange } from '../api/queries';
import type { Escalation } from '../api/client';
import { formatCents, formatTime, humanize } from '../format';
import { ConfirmDialog, ConflictBanner, PlainText } from './common';
import { useToast } from './Shell';

export interface EscalationRow {
  escalation: Escalation;
  customer?: string;
  outstanding_cents?: number;
  currency?: string;
}

/** Open (and, on a case, resolved) escalations: the agent's question and a Resolve action. */
export function EscalationList({ items, showCase = false }: { items: EscalationRow[]; showCase?: boolean }) {
  const { run, busy, conflict, clearConflict } = useChange();
  const push = useToast();
  const [resolving, setResolving] = useState<Escalation | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);

  const close = useCallback(() => {
    setResolving(null);
    setNote('');
    setError(null);
  }, []);

  const resolve = async () => {
    if (!resolving) return;
    const result = await run((c) => c.resolveEscalation(resolving.id, note.trim() || null));
    if (result.ok) {
      push({ tone: 'success', title: `Escalation ${resolving.id} resolved` });
      close();
    } else if (result.error.kind === 'conflict') close();
    else setError(result.error.detail);
  };

  return (
    <>
      {conflict && <ConflictBanner detail={conflict} onDismiss={clearConflict} />}
      <ul className="escalations">
        {items.map(({ escalation: e, customer, outstanding_cents, currency }) => (
          <li key={e.id} className={e.resolved_at ? 'escalation resolved' : 'escalation'} aria-label={`Escalation ${e.id}`}>
            {e.recommendation && (
              <div className="recommendation">
                <span className="recommendation-label">Agent recommends:</span>{' '}
                {/* Model-written text: plain React text, never HTML. */}
                <span className="plain-inline">{e.recommendation}</span>
              </div>
            )}
            <div className="escalation-head">
              <span className="badge state-escalated">{e.resolved_at ? 'resolved' : humanize(e.reason)}</span>
              {showCase && (
                <Link to={`/cases/${encodeURIComponent(e.case_id)}`}>{customer ?? e.case_id}</Link>
              )}
              {showCase && outstanding_cents !== undefined && (
                <span className="muted small">{formatCents(outstanding_cents, currency)} outstanding</span>
              )}
            </div>
            <p className="escalation-q-label muted small">
              Question from {e.raised_by} · {formatTime(e.created_at)}
            </p>
            <PlainText text={e.question} />
            {e.evidence_ids.length > 0 && <p className="muted small">Evidence: {e.evidence_ids.join(', ')}</p>}
            {e.resolved_at ? (
              <p className="muted small">
                Resolved by {e.resolved_by} · {formatTime(e.resolved_at)}
                {e.resolution_note && <> · note: “{e.resolution_note}”</>}
              </p>
            ) : (
              <button type="button" className="secondary" disabled={busy} onClick={() => setResolving(e)}>
                Resolve…
              </button>
            )}
          </li>
        ))}
      </ul>
      {resolving && (
        <ConfirmDialog
          title={`Resolve escalation ${resolving.id}?`}
          confirmLabel="Mark resolved"
          busy={busy}
          onConfirm={() => void resolve()}
          onCancel={close}
        >
          <p>Record your answer or decision. The case continues under your policy.</p>
          <label htmlFor="resolve-note">Note (optional)</label>
          <textarea
            id="resolve-note"
            rows={3}
            maxLength={500}
            value={note}
            onChange={(ev) => setNote(ev.target.value)}
          />
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </ConfirmDialog>
      )}
    </>
  );
}
