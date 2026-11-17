import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAttention, useBriefing, useChange, useSummary } from '../api/queries';
import type { ApiError, AttentionApproval, Summary } from '../api/client';
import { formatCents, formatTime, humanize, shortHash } from '../format';
import { AutonomyNote, ExactMessage } from '../components/ActionCard';
import { EscalationList } from '../components/Escalations';
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
} from '../components/common';

export function HomePage() {
  const attention = useAttention();
  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>Needs you</h2>
        <p className="muted">What is waiting for your decision. Everything else is handled by policy or the agents.</p>
      </div>
      <KpiStrip />
      <div className="home-grid">
        <section className="panel" aria-labelledby="queue-title">
          <div className="panel-head">
            <h3 id="queue-title">Approval queue</h3>
            {attention.data && <span className="count">{attention.data.approvals.length}</span>}
          </div>
          <p className="panel-note">
            Keyboard: <kbd>j</kbd>/<kbd>k</kbd> move, <kbd>a</kbd> approve, <kbd>r</kbd> reject (each asks to confirm).
          </p>
          {attention.isPending && <Skeleton label="Loading approval queue" rows={4} />}
          {attention.error && <ErrorBox error={attention.error} onRetry={() => void attention.refetch()} />}
          {attention.data && <ApprovalQueue approvals={attention.data.approvals} />}
        </section>
        <div className="home-side">
          <section className="panel" aria-labelledby="esc-title">
            <div className="panel-head">
              <h3 id="esc-title">Escalations</h3>
              {attention.data && <span className="count">{attention.data.escalations.length}</span>}
            </div>
            {attention.isPending && <Skeleton label="Loading escalations" />}
            {attention.data && attention.data.escalations.length === 0 && (
              <EmptyState title="No open escalations">When an agent needs a decision only you can make, it asks here.</EmptyState>
            )}
            {attention.data && attention.data.escalations.length > 0 && (
              <EscalationList items={attention.data.escalations} showCase />
            )}
          </section>
          <BriefingPanel />
        </div>
      </div>
    </div>
  );
}

// --- KPIs ------------------------------------------------------------------

function KpiStrip() {
  const summary = useSummary();
  if (summary.isPending) return <Skeleton label="Loading headline numbers" rows={2} />;
  if (summary.error) return <ErrorBox error={summary.error} onRetry={() => void summary.refetch()} />;
  return <Kpis s={summary.data} />;
}

function Kpis({ s }: { s: Summary }) {
  return (
    <section aria-label="Headline numbers" className="kpis">
      <div className="kpi">
        <span className="kpi-label">Outstanding</span>
        <span className="kpi-value">{formatCents(s.outstanding_cents, s.currency)}</span>
        <span className="kpi-sub">{s.open_cases} open cases</span>
      </div>
      <div className="kpi">
        <span className="kpi-label">Overdue</span>
        <span className="kpi-value warn">{formatCents(s.overdue_cents, s.currency)}</span>
        <span className="kpi-sub">{s.overdue_cases} cases overdue</span>
      </div>
      <div className="kpi">
        <span className="kpi-label">Auto-handled (7 days)</span>
        <span className="kpi-value">{s.auto_handled_7d}</span>
        <span className="kpi-sub">{s.resolved_7d} resolved this week</span>
      </div>
      <div className="kpi">
        <span className="kpi-label">Awaiting you</span>
        <span className={s.awaiting_owner > 0 ? 'kpi-value attention' : 'kpi-value'}>{s.awaiting_owner}</span>
        <span className="kpi-sub">approvals and escalations</span>
      </div>
      <div className={s.autonomy_paused ? 'kpi paused' : 'kpi'}>
        <span className="kpi-label">Autonomy</span>
        <span className="kpi-value">{s.autonomy_paused ? 'Paused' : 'On'}</span>
        <span className="kpi-sub">
          {s.model_spend_today_cents !== null
            ? `Model spend today ${formatCents(s.model_spend_today_cents, s.currency)}`
            : formatTime(s.now)}
          {s.simulated_time && <> · <SimulatedTag>Simulated clock</SimulatedTag></>}
        </span>
      </div>
    </section>
  );
}

// --- approval queue ----------------------------------------------------------

type Dialog = { type: 'approve' | 'reject'; item: AttentionApproval } | null;

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && !!target.closest('input, textarea, select, [contenteditable="true"]');
}

export function ApprovalQueue({ approvals }: { approvals: AttentionApproval[] }) {
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
                <ExactMessage action={action} compact={!isSelected} />
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

// --- briefing ----------------------------------------------------------------

function BriefingPanel() {
  const briefing = useBriefing();
  return (
    <section className="panel" aria-labelledby="brief-title">
      <div className="panel-head">
        <h3 id="brief-title">Latest briefing</h3>
        {briefing.data && <span className="muted small">{formatTime(briefing.data.created_at)}</span>}
      </div>
      {briefing.isPending && <Skeleton label="Loading briefing" />}
      {briefing.error && <ErrorBox error={briefing.error} onRetry={() => void briefing.refetch()} />}
      {briefing.data === null && (
        <EmptyState title="No briefing yet">The briefing agent writes a short summary here once it has run.</EmptyState>
      )}
      {briefing.data && (
        <div className="briefing">
          <p className="briefing-headline">{briefing.data.headline}</p>
          <p className="muted small">
            {formatTime(briefing.data.period_start)} – {formatTime(briefing.data.period_end)} · model text, untrusted
          </p>
          {briefing.data.sections.map((section, i) => (
            <div key={i} className="briefing-section">
              <h4>{section.title}</h4>
              <ul>
                {section.items.map((item, j) => (
                  <li key={j}>
                    <span className="plain-inline">{item.text}</span>
                    {item.case_ids.length > 0 && (
                      <span className="muted small">
                        {' '}
                        ·{' '}
                        {item.case_ids.map((id, k) => (
                          <span key={id}>
                            {k > 0 && ', '}
                            <Link to={`/cases/${encodeURIComponent(id)}`}>{id}</Link>
                          </span>
                        ))}
                      </span>
                    )}
                    {item.evidence_ids.length > 0 && (
                      <span className="muted small"> · evidence {item.evidence_ids.join(', ')}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
