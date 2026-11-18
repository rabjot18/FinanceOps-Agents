import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import {
  HASH,
  attention,
  callsTo,
  emptyAttention,
  escalation,
  mockFetch,
  proposedAction,
  shellRoutes,
  signedInAt,
} from '../test/fixtures';

const recommended = {
  ...escalation,
  recommendation: 'Offer a 10% credit on the <i>missing</i> items and keep the rest due.',
};

function decisions(routes: Parameters<typeof mockFetch>[0] = {}) {
  const fetchMock = mockFetch({
    ...shellRoutes(),
    'GET /attention': {
      body: attention({ escalations: [{ escalation: recommended, customer: 'Cedar Synthetic Inc', currency: 'CAD', outstanding_cents: 320000 }] }),
    },
    ...routes,
  });
  signedInAt('/decisions');
  const user = userEvent.setup();
  render(<App />);
  return { fetchMock, user };
}

describe('your decisions', () => {
  it('leads an escalation with the agent recommendation, as plain text', async () => {
    decisions();
    const card = await screen.findByRole('listitem', { name: 'Escalation esc-1' });
    const rec = within(card).getByText(/Offer a 10% credit/);
    expect(rec).toHaveTextContent('Offer a 10% credit on the <i>missing</i> items and keep the rest due.');
    expect(card.querySelector('i, b')).toBeNull();
    expect(card.firstElementChild).toHaveTextContent(/^Agent recommends:/);
    expect(card).toHaveTextContent('Cedar Synthetic Inc');
    expect(card).toHaveTextContent('$3,200.00 outstanding');
    expect(card).toHaveTextContent('Evidence: ev-9');
  });

  it('resolves an escalation with a note after a confirm', async () => {
    const { fetchMock, user } = decisions({
      'POST /escalations/esc-1/resolve': { body: { ...recommended, resolved_at: '2026-11-16T10:01:00Z', resolved_by: 'owner' } },
    });
    const card = await screen.findByRole('listitem', { name: 'Escalation esc-1' });
    await user.click(within(card).getByRole('button', { name: 'Resolve…' }));
    const dialog = screen.getByRole('dialog', { name: 'Resolve escalation esc-1?' });
    await user.type(within(dialog).getByLabelText('Note (optional)'), 'Credit approved');
    await user.click(within(dialog).getByRole('button', { name: 'Mark resolved' }));
    expect(await screen.findByText('Escalation esc-1 resolved')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /escalations/esc-1/resolve')[0]?.body).toEqual({ note: 'Credit approved' });
  });

  it('shows the full exact message and approves with the exact hash', async () => {
    const { fetchMock, user } = decisions({
      'POST /actions/act-1/approve': { body: { ...proposedAction, status: 'approved' } },
    });
    const queue = await screen.findByRole('list', { name: 'Actions waiting for you' });
    // Every card shows the body, not only the selected one.
    expect(within(queue).getAllByText('Hello, invoice INV-1001 for $1,250.00 CAD is outstanding.')).toHaveLength(2);
    await user.click(within(queue).getAllByRole('button', { name: 'Approve this exact message' })[0] as HTMLElement);
    expect(await screen.findByText('Approved act-1')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')[0]?.body).toEqual({ payload_hash: HASH });
  });

  it('on 409 says the case changed and never retries', async () => {
    const { fetchMock, user } = decisions({
      'POST /actions/act-1/approve': { status: 409, body: { detail: 'invoice version changed' } },
    });
    const queue = await screen.findByRole('list', { name: 'Actions waiting for you' });
    await user.click(within(queue).getAllByRole('button', { name: 'Approve this exact message' })[0] as HTMLElement);
    expect(await screen.findByText('This case changed — here is the latest.')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toHaveLength(1);
  });

  it('is calm when nothing needs the owner', async () => {
    decisions({ 'GET /attention': { body: emptyAttention } });
    expect(await screen.findByText('Nothing needs you. The agents are handling it.')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Actions waiting for you' })).toBeNull();
  });
});
