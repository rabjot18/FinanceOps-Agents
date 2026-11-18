import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useChange } from '../api/queries';
import type { ApiError, AttentionApproval } from '../api/client';
import { formatCents, humanize, shortHash } from '../format';
import { AutonomyNote, ExactMessage } from './ActionCard';
import { useToast } from './toast';
import { Avatar, ConfirmDialog, ConflictBanner, EmptyState, ErrorBox, PlainText } from './common';

// --- approval queue ----------------------------------------------------------

type Dialog = { type: 'approve' | 'reject'; item: AttentionApproval } | null;

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && !!target.closest('input, textarea, select, [contenteditable="true"]');
}

/** The approval queue: approve the exact hash shown, reject with a note, queue an approved message. */
export function ApprovalQueue({ approvals, expanded = false }: { approvals: AttentionApproval[]; expanded?: boolean }) {
  const { run, busy, conflict, clearConflict } = useChange();
  const push = useToast();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<ApiError | null>(null);
  const itemRefs = useRef(new Map<string, HTMLLIElement>());

  const index = Math.max(0, approvals.findIndex((a) => a.action.id === selectedId));
  const selected = approvals[index];

  const select = useCallback(
    (i: number) => {
      const item = approvals[Math.max(0, Math.min(approvals.length - 1, i))];
      if (!item) return;
      setSelectedId(item.action.id);
      itemRefs.current.get(item.action.id)?.scrollIntoView?.({ block: 'nearest' });
    },
    [approvals],
  );

  const decide = useCallback(
    async (kind: 'approve' | 'reject' | 'execute', item: AttentionApproval, rejectNote?: string) => {
      setError(null);
      const { action } = item;
      const hash = action.payload_hash;
      if (kind === 'approve' && !hash) return;
      const result = await run((c) =>
        kind === 'approve'
          ? c.approve(action.id, hash as string) // the exact hash shown on screen
          : kind === 'reject'
            ? c.reject(action.id, rejectNote?.trim() || null)
            : c.execute(action.id),
      );
      setDialog(null);
      setNote('');
      if (result.ok) {
        push({
          tone: 'success',
          title:
            kind === 'approve'
              ? `Approved ${action.id}`
              : kind === 'reject'
                ? `Rejected ${action.id}`
                : `Queued ${action.id} for simulated delivery`,
          body: kind === 'approve' ? 'Execute it to queue the message.' : undefined,
        });
      } else if (result.error.kind !== 'conflict') setError(result.error);
    },
    [run, push],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey || isTyping(e.target)) return;
      if (document.querySelector('[role="dialog"]')) return;
      if (!selected) return;
      if (e.key === 'j') select(index + 1);
      else if (e.key === 'k') select(index - 1);
      else if (e.key === 'a' && selected.action.status === 'proposed' && selected.action.payload_hash)
        setDialog({ type: 'approve', item: selected });
      else if (e.key === 'r' && selected.action.status === 'proposed') setDialog({ type: 'reject', item: selected });
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [index, selected, select]);

  const closeDialog = useCallback(() => {
    setDialog(null);
    setNote('');
  }, []);

  return (
    <>
      {conflict && <ConflictBanner detail={conflict} onDismiss={clearConflict} />}
      {error && <ErrorBox error={error} />}
      {approvals.length === 0 ? (
        <EmptyState title="Nothing waits for your approval">
          New proposals that your policy does not run automatically appear here.
        </EmptyState>
      ) : (
        <ol className="queue" aria-label="Actions waiting for you">
          {approvals.map((item, i) => {
            const { action } = item;
            const isSelected = i === index;
            return (
              <li
                key={action.id}
                ref={(el) => {
                  if (el) itemRefs.current.set(action.id, el);
                  else itemRefs.current.delete(action.id);
                }}
                className={isSelected ? 'queue-item selected' : 'queue-item'}
                aria-current={isSelected ? 'true' : undefined}
                aria-label={`${item.customer}: ${humanize(action.kind)} ${action.id}`}
                onClick={() => setSelectedId(action.id)}
              >
                <div className="queue-head">
                  <Avatar name={item.customer} />
                  <div className="queue-title">
                    <Link to={`/cases/${encodeURIComponent(item.case_id)}`}>{item.customer}</Link>
                    <span className="muted">
                      {humanize(action.kind)} · <code>{action.id}</code> · outstanding{' '}
                      {formatCents(item.outstanding_cents, item.currency)}
                    </span>
                  </div>
                  <span className={`badge status-${action.status}`}>
                    {action.status === 'approved' ? 'approved · execute' : humanize(action.status)}
                  </span>
                </div>
                {action.autonomy && <AutonomyNote decision={action.autonomy} />}
                {action.rationale && (
                  <details className="rationale">
                    <summary>Rationale (not part of the message)</summary>
                    <PlainText text={action.rationale} />
                  </details>
                )}
                <ExactMessage action={action} compact={!expanded && !isSelected} />
                <div className="queue-actions">
                  {action.status === 'proposed' && (
                    <>
                      <button
                        type="button"
                        disabled={busy || !action.payload_hash}
                        onClick={() => void decide('approve', item)}
                      >
                        Approve this exact message
                      </button>
                      <button
                        type="button"
                        className="danger"
                        disabled={busy}
                        onClick={() => setDialog({ type: 'reject', item })}
                      >
                        Reject…
                      </button>
                    </>
                  )}
                  {action.status === 'approved' && (
                    <>
                      <button
                        type="button"
                        disabled={busy || action.approval?.expired === true}
                        onClick={() => void decide('execute', item)}
                      >
                        Execute (queue for simulated delivery)
                      </button>
                      {action.approval?.expired && <span className="muted">The approval expired.</span>}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {dialog?.type === 'approve' && (
        <ConfirmDialog
          title="Approve this exact message?"
          confirmLabel="Approve exact message"
          busy={busy}
          onConfirm={() => void decide('approve', dialog.item)}
          onCancel={closeDialog}
        >
          <p>
            {humanize(dialog.item.action.kind)} to <strong>{dialog.item.action.payload?.to ?? '(no contact)'}</strong>{' '}
            for {dialog.item.customer}
            {dialog.item.action.payload &&
              ` · ${formatCents(dialog.item.action.payload.amount_cents, dialog.item.action.payload.currency)}`}
            .
          </p>
          <p className="muted">
            Subject: {dialog.item.action.payload?.subject} · hash{' '}
            <code>{shortHash(dialog.item.action.payload_hash ?? '')}</code>
          </p>
        </ConfirmDialog>
      )}
      {dialog?.type === 'reject' && (
        <ConfirmDialog
          title={`Reject ${dialog.item.action.id}?`}
          confirmLabel="Reject"
          tone="danger"
          busy={busy}
          onConfirm={() => void decide('reject', dialog.item, note)}
          onCancel={closeDialog}
        >
          <p>
            {humanize(dialog.item.action.kind)} for {dialog.item.customer}. Nothing will be sent.
          </p>
          <label htmlFor="reject-note">Rejection note (optional)</label>
          <textarea id="reject-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </ConfirmDialog>
      )}
    </>
  );
}
