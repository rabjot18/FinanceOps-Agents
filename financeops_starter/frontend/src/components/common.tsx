import type { ApiError, CaseState } from '../api/client';
import { humanize } from '../format';

export function StateBadge({ state }: { state: CaseState }) {
  return <span className={`badge state-${state}`}>{humanize(state)}</span>;
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
