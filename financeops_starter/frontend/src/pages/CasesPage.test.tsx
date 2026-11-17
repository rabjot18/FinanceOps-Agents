import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { callsTo, caseList, mockFetch, shellRoutes, signedInAt } from '../test/fixtures';

const second = {
  ...caseList.cases[0]!,
  id: 'case-INV-2002',
  invoice_id: 'INV-2002',
  customer: 'Cedar Synthetic Inc',
  outstanding_cents: 320000,
  days_overdue: null,
  state: 'escalated' as const,
  needs_owner: true,
  open_escalations: 1,
  handled_by: 'agent' as const,
};

function cases(routes: Parameters<typeof mockFetch>[0] = {}) {
  const fetchMock = mockFetch({
    ...shellRoutes(),
    'GET /cases': { body: { cases: [caseList.cases[0]!, second], total: 2, next_cursor: null } },
    ...routes,
  });
  signedInAt('/cases');
  const user = userEvent.setup();
  render(<App />);
  return { fetchMock, user };
}

describe('cases table', () => {
  it('shows a skeleton, then one row per case with backend values', async () => {
    cases();
    expect(screen.getByRole('status', { name: 'Loading cases' })).toBeInTheDocument();
    const table = await screen.findByRole('table');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Maple Synthetic Ltd');
    expect(rows[0]).toHaveTextContent('$1,250.00');
    expect(rows[0]).toHaveTextContent('6');
    expect(rows[0]).toHaveTextContent('awaiting approval');
    expect(rows[0]).toHaveTextContent('You');
    expect(rows[0]).toHaveTextContent('Needs you');
    expect(rows[1]).toHaveTextContent('Agent');
    expect(rows[1]).toHaveTextContent('Needs you · 1 escalation');
    expect(screen.getByText(/2 cases · page 1/)).toBeInTheDocument();
  });

  it('search, state and sort go to the server as query parameters', async () => {
    const { fetchMock, user } = cases();
    await screen.findByRole('table');
    await user.type(screen.getByLabelText('Search'), 'maple');
    await waitFor(() => expect(callsTo(fetchMock, 'GET /cases').some((c) => c.url.includes('q=maple'))).toBe(true));
    await user.selectOptions(screen.getByLabelText('State'), 'escalated');
    await user.selectOptions(screen.getByLabelText('Sort by'), 'overdue');
    await waitFor(() =>
      expect(callsTo(fetchMock, 'GET /cases').map((c) => c.url)).toContain(
        '/cases?state=escalated&q=maple&sort=overdue&limit=25',
      ),
    );
  });

  it('pages forward with the server cursor and back again', async () => {
    const { fetchMock, user } = cases({
      'GET /cases': (_init, url) =>
        url.includes('cursor=25')
          ? { body: { cases: [second], total: 26, next_cursor: null } }
          : { body: { cases: [caseList.cases[0]!], total: 26, next_cursor: '25' } },
    });
    await screen.findByText('Maple Synthetic Ltd');
    await user.click(screen.getByRole('button', { name: 'Next →' }));
    expect(await screen.findByText('Cedar Synthetic Inc')).toBeInTheDocument();
    expect(screen.getByText(/page 2/)).toBeInTheDocument();
    expect(callsTo(fetchMock, 'GET /cases').some((c) => c.url.endsWith('cursor=25'))).toBe(true);
    await user.click(screen.getByRole('button', { name: '← Previous' }));
    expect(await screen.findByText('Maple Synthetic Ltd')).toBeInTheDocument();
  });

  it('an empty system points to the Demo Lab; an empty filter offers to clear it', async () => {
    const { user } = cases({
      'GET /cases': { body: { cases: [], total: 0, next_cursor: null } },
    });
    expect(await screen.findByText('No cases yet')).toBeInTheDocument();
    expect(within(screen.getByRole('main')).getByRole('link', { name: 'Demo Lab' })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('State'), 'resolved');
    expect(await screen.findByText('No cases match')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(await screen.findByText('No cases yet')).toBeInTheDocument();
  });

  it('opens a case from its row', async () => {
    const { user } = cases({ 'GET /cases/case-INV-1001/workspace': { status: 404, body: { detail: 'Unknown case' } } });
    await user.click(await screen.findByRole('link', { name: 'Maple Synthetic Ltd' }));
    expect(await screen.findByText('This case does not exist')).toBeInTheDocument();
  });

  it('shows a network failure with a retry that works', async () => {
    const { user } = cases({ 'GET /cases': ['network-error', { body: caseList }] });
    expect(await screen.findByText(/Could not reach the FinanceOps server/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Maple Synthetic Ltd')).toBeInTheDocument();
  });
});
