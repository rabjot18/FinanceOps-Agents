import { useEffect } from 'react';
import type { Api, ApiError } from '../api/client';
import { formatCents } from '../format';
import { useResource } from '../useResource';
import { ErrorBox, StateBadge } from './common';

interface Props {
  client: Api;
  revision: number;
  selected: string | null;
  onSelect: (caseId: string) => void;
  onAuthError: (error: ApiError) => boolean;
}

export function CaseList({ client, revision, selected, onSelect, onAuthError }: Props) {
  const cases = useResource(() => client.listCases(), String(revision));

  useEffect(() => {
    if (cases.error) onAuthError(cases.error);
  }, [cases.error, onAuthError]);

  return (
    <nav className="panel case-list" aria-labelledby="cases-title">
      <h2 id="cases-title">Cases</h2>
      {cases.loading && !cases.data && <p role="status">Loading cases…</p>}
      {cases.error && <ErrorBox error={cases.error} onRetry={() => void cases.reload()} />}
      {cases.data && cases.data.cases.length === 0 && (
        <p>No cases yet. Load a synthetic scenario in the demo lab.</p>
      )}
      {cases.data && cases.data.cases.length > 0 && (
        <ul>
          {cases.data.cases.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="case-row"
                aria-current={c.id === selected ? 'true' : undefined}
                onClick={() => onSelect(c.id)}
              >
                <span className="case-customer">{c.customer}</span>
                <StateBadge state={c.state} />
                <span className="case-money">
                  Outstanding {formatCents(c.outstanding_cents, c.currency)}
                </span>
                <span className="case-pending">
                  {c.pending_actions} pending action{c.pending_actions === 1 ? '' : 's'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
