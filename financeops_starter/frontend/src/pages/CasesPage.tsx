import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useCases } from '../api/queries';
import type { CaseListParams, CaseState } from '../api/client';
import { formatCents, humanize } from '../format';
import { Avatar, EmptyState, ErrorBox, Skeleton, StateBadge } from '../components/common';

const STATES: CaseState[] = [
  'open',
  'needs_information',
  'awaiting_approval',
  'awaiting_external_outcome',
  'escalated',
  'resolved',
];
const SORTS: { value: NonNullable<CaseListParams['sort']>; label: string }[] = [
  { value: 'updated', label: 'Recently updated' },
  { value: 'outstanding', label: 'Outstanding amount' },
  { value: 'overdue', label: 'Days overdue' },
  { value: 'customer', label: 'Customer name' },
];
const HANDLED: Record<string, string> = {
  owner: 'You',
  policy: 'Policy (auto)',
  agent: 'Agent',
  none: '—',
};
const PAGE_SIZE = 25;

function asState(value: string | null): CaseState | null {
  return STATES.find((s) => s === value) ?? null;
}
function asSort(value: string | null): NonNullable<CaseListParams['sort']> {
  return SORTS.find((s) => s.value === value)?.value ?? 'updated';
}

export function CasesPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const state = asState(params.get('state'));
  const sort = asSort(params.get('sort'));
  const q = params.get('q') ?? '';
  const [search, setSearch] = useState(q);
  // Cursors of the pages before the current one, for "Previous".
  const [cursors, setCursors] = useState<(string | null)[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
    setCursor(null);
    setCursors([]);
  };

  // Typing updates the URL (and so the query) after a short pause.
  useEffect(() => {
    if (search.trim() === q) return;
    const timer = setTimeout(() => update({ q: search.trim() || null }), 300);
    return () => clearTimeout(timer);
  });

  const cases = useCases({ state, q: q || null, sort, limit: PAGE_SIZE, cursor });
  const filtered = !!state || !!q;
  const pageNumber = cursors.length + 1;

  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>Cases</h2>
        <p className="muted">Every receivable the system is working on. Click a row to open it.</p>
      </div>
      <section className="panel">
        <form className="filters" role="search" onSubmit={(e) => e.preventDefault()}>
          <div>
            <label htmlFor="case-search">Search</label>
            <input
              id="case-search"
              type="search"
              placeholder="Customer or invoice"
              maxLength={100}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="case-state">State</label>
            <select id="case-state" value={state ?? ''} onChange={(e) => update({ state: e.target.value || null })}>
              <option value="">All states</option>
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {humanize(s)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="case-sort">Sort by</label>
            <select id="case-sort" value={sort} onChange={(e) => update({ sort: e.target.value })}>
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </form>

        {cases.isPending && <Skeleton label="Loading cases" rows={6} />}
        {cases.error && <ErrorBox error={cases.error} onRetry={() => void cases.refetch()} />}
        {cases.data && cases.data.cases.length === 0 && filtered && (
          <EmptyState title="No cases match">
            Try another search or state.{' '}
            <button
              type="button"
              className="link"
              onClick={() => {
                setSearch('');
                update({ q: null, state: null });
              }}
            >
              Clear filters
            </button>
          </EmptyState>
        )}
        {cases.data && cases.data.cases.length === 0 && !filtered && (
          <EmptyState title="No cases yet">
            Load a synthetic scenario or generate a business in the <Link to="/demo">Demo Lab</Link>.
          </EmptyState>
        )}
        {cases.data && cases.data.cases.length > 0 && (
          <>
            <p className="table-meta muted small" aria-live="polite">
              {cases.data.total} case{cases.data.total === 1 ? '' : 's'}
              {filtered && ' match'} · page {pageNumber}
              {cases.isFetching && ' · refreshing…'}
            </p>
            <div className="table-wrap">
              <table className="cases-table">
                <thead>
                  <tr>
                    <th scope="col">Customer</th>
                    <th scope="col" className="num">
                      Outstanding
                    </th>
                    <th scope="col" className="num">
                      Days overdue
                    </th>
                    <th scope="col">State</th>
                    <th scope="col">Handled by</th>
                    <th scope="col">
                      <span className="visually-hidden">Needs you</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {cases.data.cases.map((c) => (
                    <tr
                      key={c.id}
                      className={c.needs_owner ? 'needs-owner' : undefined}
                      onClick={() => navigate(`/cases/${encodeURIComponent(c.id)}`)}
                    >
                      <td>
                        <div className="cell-customer">
                          <Avatar name={c.customer} />
                          <div>
                            <Link to={`/cases/${encodeURIComponent(c.id)}`} onClick={(e) => e.stopPropagation()}>
                              {c.customer}
                            </Link>
                            <span className="muted small">{c.invoice_id}</span>
                          </div>
                        </div>
                      </td>
                      <td className="num money">{formatCents(c.outstanding_cents, c.currency)}</td>
                      <td className="num">
                        {c.days_overdue === null ? <span className="muted">—</span> : c.days_overdue}
                      </td>
                      <td>
                        <StateBadge state={c.state} />
                      </td>
                      <td>{HANDLED[c.handled_by] ?? humanize(c.handled_by)}</td>
                      <td>
                        {c.needs_owner && (
                          <span className="needs-you" title="Waiting for your decision">
                            Needs you
                            {c.open_escalations > 0 &&
                              ` · ${c.open_escalations} escalation${c.open_escalations === 1 ? '' : 's'}`}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <button
                type="button"
                className="secondary"
                disabled={cursors.length === 0}
                onClick={() => {
                  setCursor(cursors[cursors.length - 1] ?? null);
                  setCursors(cursors.slice(0, -1));
                }}
              >
                ← Previous
              </button>
              <button
                type="button"
                className="secondary"
                disabled={!cases.data.next_cursor}
                onClick={() => {
                  setCursors([...cursors, cursor]);
                  setCursor(cases.data?.next_cursor ?? null);
                }}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
