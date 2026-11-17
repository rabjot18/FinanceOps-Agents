import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { callsTo, mockFetch, policyV1, shellRoutes, signedInAt } from '../test/fixtures';
import { parseCents } from './PolicyPage';

function policyPage(routes: Parameters<typeof mockFetch>[0] = {}) {
  const fetchMock = mockFetch({ ...shellRoutes(), 'GET /policy': { body: policyV1 }, ...routes });
  signedInAt('/policy');
  const user = userEvent.setup();
  render(<App />);
  return { fetchMock, user };
}

describe('parseCents', () => {
  it('parses plain amounts exactly, without floating point', () => {
    expect(parseCents('5000')).toBe(500000);
    expect(parseCents('5,000.5')).toBe(500050);
    expect(parseCents('0.07')).toBe(7);
    expect(parseCents('12.345')).toBeNull();
    expect(parseCents('-1')).toBeNull();
    expect(parseCents('abc')).toBeNull();
  });
});

describe('policy page', () => {
  it('loads the stored policy into the form', async () => {
    policyPage();
    expect(await screen.findByLabelText('Approval expires after (minutes)')).toHaveValue('60');
    expect(screen.getByLabelText(/Reminder cadence/)).toHaveValue('3, 10');
    expect(screen.getByLabelText('Daily model budget (CAD)')).toHaveValue('5');
    const rules = screen.getAllByRole('listitem', { name: /^Rule \d$/ });
    expect(rules).toHaveLength(2);
    expect(within(rules[0]!).getByLabelText('Rule id')).toHaveValue('small');
    expect(within(rules[0]!).getByLabelText(/Max amount/)).toHaveValue('1000');
  });

  it('reorders, removes and adds rules, then saves exactly the edited policy', async () => {
    const { fetchMock, user } = policyPage({ 'POST /policy': { body: { ...policyV1, version: 2 } } });
    await user.click(await screen.findByRole('button', { name: 'Move rule 2 up' }));
    let rules = screen.getAllByRole('listitem', { name: /^Rule \d$/ });
    expect(within(rules[0]!).getByLabelText('Rule id')).toHaveValue('info');
    await user.click(screen.getByRole('button', { name: 'Remove rule 2' }));
    await user.click(screen.getByRole('button', { name: /Suggested: routine reminders/ }));
    rules = screen.getAllByRole('listitem', { name: /^Rule \d$/ });
    expect(rules).toHaveLength(2);
    const ttl = screen.getByLabelText('Approval expires after (minutes)');
    await user.clear(ttl);
    await user.type(ttl, '90');
    await user.click(screen.getByRole('button', { name: 'Save new policy version' }));

    await waitFor(() => expect(callsTo(fetchMock, 'POST /policy')).toHaveLength(1));
    expect(callsTo(fetchMock, 'POST /policy')[0]?.body).toEqual({
      approval_ttl_minutes: 90,
      follow_up_days: 7,
      max_reminders: 3,
      cadence_days: [3, 10],
      daily_model_budget_cents: 500,
      autonomy_rules: [
        { ...policyV1.autonomy_rules[1] },
        {
          id: 'routine-reminders',
          description: 'Routine reminders up to CAD 5,000 on undisputed invoices run automatically',
          action_kind: 'collection_reminder',
          mode: 'auto',
          max_amount_cents: 500000,
          require_evidence: true,
          require_undisputed: true,
        },
      ],
    });
    expect(await screen.findByText('Policy v2 saved')).toBeInTheDocument();
  });

  it('checks what was typed before sending anything', async () => {
    const { fetchMock, user } = policyPage();
    const cadence = await screen.findByLabelText(/Reminder cadence/);
    await user.clear(cadence);
    await user.type(cadence, '3, soon');
    await user.click(screen.getByRole('button', { name: 'Save new policy version' }));
    expect(screen.getByText('Cadence days must be whole numbers separated by commas.')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /policy')).toHaveLength(0);
  });

  it('shows the server detail for a 422', async () => {
    const { user } = policyPage({
      'POST /policy': { status: 422, body: { detail: 'rule ids must be unique, lower-case letters, digits and -' } },
    });
    await user.click(await screen.findByRole('button', { name: 'Save new policy version' }));
    expect(await screen.findByText(/rule ids must be unique/)).toBeInTheDocument();
  });

  it('preview answers 503 "Not built yet": says so and the page stays usable', async () => {
    const { fetchMock, user } = policyPage({
      'POST /policy/preview': { status: 503, body: { detail: 'Not built yet (Milestone 5 step 1a)' } },
      'POST /policy': { body: { ...policyV1, version: 2 } },
    });
    await user.click(await screen.findByRole('button', { name: 'Preview impact' }));
    expect(await screen.findByText('Preview is not available yet.')).toBeInTheDocument();
    expect(screen.getByText(/Not built yet \(Milestone 5 step 1a\)/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save new policy version' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST /policy')).toHaveLength(1));
  });

  it('shows the preview numbers when the server has them', async () => {
    const { user } = policyPage({ 'POST /policy/preview': { body: { auto: 18, approve: 6, blocked: 1, open_cases: 25 } } });
    await user.click(await screen.findByRole('button', { name: 'Preview impact' }));
    expect(await screen.findByText(/18 of 25 open cases would be handled automatically/)).toBeInTheDocument();
  });

  it('with no stored policy, starts from defaults and says sending is disabled', async () => {
    policyPage({ 'GET /policy': { status: 404, body: { detail: 'No policy stored; sending is disabled' } } });
    expect(await screen.findByText(/No policy stored yet/)).toBeInTheDocument();
    expect(screen.getByText('No rules: nothing runs automatically')).toBeInTheDocument();
  });
});
