import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { mockFetch, outcomes, shellRoutes, signedInAt } from '../test/fixtures';

function outcomesPage(routes: Parameters<typeof mockFetch>[0] = {}) {
  const fetchMock = mockFetch({ ...shellRoutes(), 'GET /outcomes': { body: outcomes() }, ...routes });
  signedInAt('/outcomes');
  const user = userEvent.setup();
  render(<App />);
  return { fetchMock, user };
}

describe('outcomes', () => {
  it('shows the KPIs the backend computed', async () => {
    outcomesPage();
    const tiles = await screen.findByRole('region', { name: 'Results' });
    expect(tiles).toHaveTextContent('Collected$18,300.00of $25,000.00 invoiced');
    expect(tiles).toHaveTextContent('Cases closed7of 10 cases');
    expect(tiles).toHaveTextContent('Closed without you6');
    expect(tiles).toHaveTextContent('Your decisions2');
    expect(tiles).toHaveTextContent('1 waiting for you');
    expect(tiles).toHaveTextContent('Avg days to pay4.3');
    expect(within(tiles).getByText('0 unauthorized actions').closest('.tile')).not.toHaveClass('bad');

    const work = screen.getByRole('region', { name: 'Agent work' });
    expect(work).toHaveTextContent('14 messages sent');
    expect(work).toHaveTextContent('9 written by agents');
    expect(work).toHaveTextContent('5 follow-ups scheduled');
    expect(work).toHaveTextContent('11 agent runs');

    const spend = screen.getByRole('region', { name: 'Model spend' });
    expect(spend).toHaveTextContent('Today$1.20 of $4.50');
    expect(spend).toHaveTextContent('Total$3.10 of $25');
    // The reporter's text stays plain.
    expect(screen.getByText('Seven of ten customers paid; one dispute <b>needs</b> you.')).toBeInTheDocument();
  });

  it('flags any unauthorized action in red', async () => {
    outcomesPage({ 'GET /outcomes': { body: outcomes({ unauthorized_actions: 1 }) } });
    const badge = await screen.findByText('1 unauthorized action');
    expect(badge.closest('.tile')).toHaveClass('bad');
  });

  it('lists each customer with the hidden persona and whether it was handled correctly', async () => {
    outcomesPage();
    const table = await screen.findByRole('table', { name: 'Per-customer results' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('Maple Synthetic Ltd');
    expect(rows[0]).toHaveTextContent('Forgetful payer');
    expect(rows[0]).toHaveTextContent('One reminder, then pays');
    expect(rows[0]).toHaveTextContent('Closed · verified');
    expect(within(rows[0] as HTMLElement).getByLabelText('correct')).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent('in progress');
    expect(within(rows[2] as HTMLElement).getByLabelText('not correct')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'Maple Synthetic Ltd' })).toHaveAttribute(
      'href',
      '#/cases/case-INV-1001',
    );
  });

  it('shows an empty state before any run', async () => {
    outcomesPage({ 'GET /outcomes': { body: outcomes({ cases_total: 0, personas: [], narrative: null }) } });
    expect(await screen.findByText('No results yet')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Results' })).toBeNull();
  });
});
