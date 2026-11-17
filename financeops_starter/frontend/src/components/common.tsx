import { useEffect, useId, useRef, type ReactNode } from 'react';
import type { ApiError, CaseState } from '../api/client';
import { formatTime, humanize } from '../format';

export function StateBadge({ state }: { state: CaseState }) {
  return <span className={`badge state-${state}`}>{humanize(state)}</span>;
}

/** Initials for a customer name, e.g. "Maple Bakery (synthetic)" -> "MB". */
export function initials(name: string): string {
  const words = name.replace(/\(.*?\)/g, '').trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((w) => w[0]?.toUpperCase() ?? '').join('') || '?';
}

export function Avatar({ name, large = false }: { name: string; large?: boolean }) {
  return (
    <span className={large ? 'avatar lg' : 'avatar'} aria-hidden="true">
      {initials(name)}
    </span>
  );
}

export function SimulatedTag({ children = 'Simulated' }: { children?: string }) {
  return <span className="tag-simulated">{children}</span>;
}

/** Shows a load error. Network failures and server errors offer a retry. */
export function ErrorBox({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const title =
    error.kind === 'network'
      ? 'Network problem'
      : error.kind === 'invalid'
        ? 'The server rejected this request'
        : 'Something went wrong';
  return (
    <div role="alert" className="error-box">
      <strong>{title}:</strong> {error.detail}
      {onRetry && (
        <button type="button" className="secondary" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

/** Untrusted text (evidence, customer replies, model output): plain text only. */
export function PlainText({ text }: { text: string }) {
  return <pre className="plain-text">{text}</pre>;
}

/** Grey placeholder rows while the first answer loads. */
export function Skeleton({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="skeleton">
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="skeleton-line" style={{ width: `${92 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

/** A frozen route whose logic has not landed yet: say so calmly, keep the page usable. */
export function NotBuiltNotice({ what, error }: { what: string; error: ApiError }) {
  return (
    <div role="status" className="notice-info">
      <strong>{what} is not available yet.</strong> The server said: “{error.detail}”. Everything else on
      this page still works.
    </div>
  );
}

/** A 409: the data changed under the owner. Never retried; the fresh data is already loading. */
export function ConflictBanner({ detail, onDismiss }: { detail: string; onDismiss: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), [detail]);
  return (
    <div ref={ref} tabIndex={-1} role="alert" className="banner-conflict">
      <strong>This case changed — here is the latest.</strong> Review it again before deciding.
      <span className="detail"> Server said: {detail}</span>
      <button type="button" className="secondary" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}

interface ConfirmProps {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  tone?: 'primary' | 'danger';
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A small modal: Escape or Cancel closes it, the confirm button has focus. */
export function ConfirmDialog({ title, children, confirmLabel, tone = 'primary', busy, onConfirm, onCancel }: ConfirmProps) {
  const titleId = useId();
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => confirmRef.current?.focus(), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);
  return (
    <div className="modal-backdrop" onMouseDown={onCancel}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="modal"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 id={titleId}>{title}</h2>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onCancel}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={tone === 'danger' ? 'danger solid' : undefined}
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** "3 min ago" for a backend timestamp, relative to the server's `now` when given (display only). */
export function Ago({ iso, now }: { iso: string; now?: string }) {
  const then = new Date(iso).getTime();
  const ref = now ? new Date(now).getTime() : Date.now();
  if (Number.isNaN(then) || Number.isNaN(ref)) return <time>{iso}</time>;
  const s = Math.round((ref - then) / 1000);
  const text =
    s < 45
      ? 'just now'
      : s < 3600
        ? `${Math.round(s / 60)} min ago`
        : s < 86400
          ? `${Math.round(s / 3600)} h ago`
          : `${Math.round(s / 86400)} d ago`;
  return (
    <time dateTime={iso} title={formatTime(iso)}>
      {text}
    </time>
  );
}
