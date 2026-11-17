import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import {
  HASH,
  attention,
  briefing,
  callsTo,
  emptyAttention,
  escalation,
  mockFetch,
  proposedAction,
  secondAction,
  shellRoutes,
  signedInAt,
} from '../test/fixtures';

function home(routes: Parameters<typeof mockFetch>[0] = {}) {
  const fetchMock = mockFetch({ ...shellRoutes(), 'GET /attention': { body: attention() }, ...routes });
  signedInAt('/');
  const user = userEvent.setup();
  render(<App />);
  return { fetchMock, user };
}

async function queue() {
  return screen.findByRole('list', { name: 'Actions waiting for you' });
}

describe('home: KPIs and empty states', () => {
  it('shows the numbers the backend computed', async () => {
    home();
    const kpis = await screen.findByRole('region', { name: 'Headline numbers' });
    expect(kpis).toHaveTextContent('$8,450.00');
    expect(kpis).toHaveTextContent('$1,250.00');
    expect(kpis).toHaveTextContent('Auto-handled (7 days)7');
    expect(kpis).toHaveTextContent('Awaiting you2');
    expect(kpis).toHaveTextContent('AutonomyOn');
  });

  it('shows skeletons first, then helpful empty states', async () => {
    home({ 'GET /attention': { body: emptyAttention } });
    expect(screen.getByRole('status', { name: 'Loading approval queue' })).toBeInTheDocument();
    expect(await screen.findByText('Nothing waits for your approval')).toBeInTheDocument();
    expect(screen.getByText('No open escalations')).toBeInTheDocument();
    expect(await screen.findByText('No briefing yet')).toBeInTheDocument();
  });

  it('shows the latest briefing as plain text with case links', async () => {
    home({ 'GET /briefings/latest': { body: briefing } });
    expect(await screen.findByText('Two reminders went out; one dispute needs you.')).toBeInTheDocument();
    expect(screen.getByText('Cedar disputes delivery')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'case-INV-2002' }).length).toBeGreaterThan(0);
  });
});

describe('home: approval queue', () => {
  it('approve sends exactly the payload_hash that was shown', async () => {
    const { fetchMock, user } = home({ 'POST /actions/act-1/approve': { body: { ...proposedAction, status: 'approved' } } });
    const list = await queue();
    const first = within(list).getByRole('listitem', { name: /Maple Synthetic Ltd/ });
    expect(within(first).getByText('9f2c1e7ab4d0…')).toBeInTheDocument();
    await user.click(within(first).getByRole('button', { name: 'Approve this exact message' }));
    await waitFor(() =>
      expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toEqual([expect.objectContaining({ body: { payload_hash: HASH } })]),
    );
    // Refetches the queue afterwards.
    await waitFor(() => expect(callsTo(fetchMock, 'GET /attention').length).toBeGreaterThanOrEqual(2));
  });

  it('keyboard: j moves, a asks to confirm, then approves the selected item with its hash', async () => {
    const { fetchMock, user } = home({ 'POST /actions/act-2/approve': { body: { ...secondAction, status: 'approved' } } });
    const list = await queue();
    expect(within(list).getAllByRole('listitem')[0]).toHaveAttribute('aria-current', 'true');

    await user.keyboard('j');
    expect(within(list).getAllByRole('listitem')[1]).toHaveAttribute('aria-current', 'true');
    await user.keyboard('a');
    const dialog = screen.getByRole('dialog', { name: 'Approve this exact message?' });
    expect(dialog).toHaveTextContent('ap@birch.example');
    expect(callsTo(fetchMock, 'POST /actions/act-2/approve')).toHaveLength(0);

    await user.click(within(dialog).getByRole('button', { name: 'Approve exact message' }));
    await waitFor(() =>
      expect(callsTo(fetchMock, 'POST /actions/act-2/approve')[0]?.body).toEqual({ payload_hash: secondAction.payload_hash }),
    );
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toHaveLength(0);
  });

  it('keyboard: k moves back, Escape cancels the confirm and nothing is sent', async () => {
    const { fetchMock, user } = home();
    const list = await queue();
    await user.keyboard('jk');
    expect(within(list).getAllByRole('listitem')[0]).toHaveAttribute('aria-current', 'true');
    await user.keyboard('a');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toHaveLength(0);
  });

  it('keyboard: r asks to confirm a rejection with an optional note', async () => {
    const { fetchMock, user } = home({ 'POST /actions/act-1/reject': { body: { ...proposedAction, status: 'rejected' } } });
    await queue();
    await user.keyboard('r');
    const dialog = screen.getByRole('dialog', { name: 'Reject act-1?' });
    await user.type(within(dialog).getByLabelText('Rejection note (optional)'), 'wrong contact');
    await user.click(within(dialog).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST /actions/act-1/reject')[0]?.body).toEqual({ note: 'wrong contact' }));
  });

  it('shortcuts are ignored while typing in a field', async () => {
    const { user } = home({ 'GET /attention': { body: attention() } });
    await queue();
    await user.keyboard('r');
    const note = screen.getByLabelText('Rejection note (optional)');
    await user.type(note, 'a');
    // Typing "a" in the note did not open the approve dialog on top.
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(note).toHaveValue('a');
  });

  it('on 409 says the case changed, refetches and never retries', async () => {
    const { fetchMock, user } = home({
      'POST /actions/act-1/approve': { status: 409, body: { detail: 'payload hash does not match' } },
    });
    const list = await queue();
    await user.click(within(list).getAllByRole('button', { name: 'Approve this exact message' })[0] as HTMLElement);
    expect(await screen.findByText('This case changed — here is the latest.')).toBeInTheDocument();
    expect(screen.getByText(/payload hash does not match/)).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fetchMock, 'GET /attention').length).toBeGreaterThanOrEqual(2));
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toHaveLength(1);
  });

  it('approved items offer Execute instead of Approve', async () => {
    const approved = { ...proposedAction, status: 'approved' as const };
    const { fetchMock, user } = home({
      'GET /attention': { body: attention({ approvals: [{ ...attention().approvals[0]!, action: approved }] }) },
      'POST /actions/act-1/execute': { body: { ...approved, status: 'queued' } },
    });
    const list = await queue();
    expect(within(list).queryByRole('button', { name: 'Approve this exact message' })).toBeNull();
    await user.click(within(list).getByRole('button', { name: 'Execute (queue for simulated delivery)' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST /actions/act-1/execute')).toHaveLength(1));
  });

  it('shows the autonomy decision on a queued item', async () => {
    const withDecision = {
      ...proposedAction,
      autonomy: {
        mode: 'approve' as const,
        rule_id: 'small',
        policy_version: 2,
        reasons: ['amount CAD 1,250.00 is over the rule limit'],
        decided_at: '2026-11-16T10:00:00Z',
      },
    };
    home({ 'GET /attention': { body: attention({ approvals: [{ ...attention().approvals[0]!, action: withDecision }] }) } });
    expect(await screen.findByText('Policy v2 · rule small: needs your approval')).toBeInTheDocument();
    expect(screen.getByText('amount CAD 1,250.00 is over the rule limit')).toBeInTheDocument();
  });
});

describe('home: escalations', () => {
  it('shows the question as plain text and resolves with a note after a confirm', async () => {
    const { fetchMock, user } = home({
      'POST /escalations/esc-1/resolve': { body: { ...escalation, resolved_at: '2026-11-16T11:00:00Z', resolved_by: 'owner' } },
    });
    const item = await screen.findByRole('listitem', { name: 'Escalation esc-1' });
    expect(item).toHaveTextContent('Customer says the delivery was <b>incomplete</b>. Offer a credit?');
    expect(item.querySelector('b')).toBeNull();

    await user.click(within(item).getByRole('button', { name: 'Resolve…' }));
    const dialog = screen.getByRole('dialog', { name: 'Resolve escalation esc-1?' });
    await user.type(within(dialog).getByLabelText('Note (optional)'), 'offer 10% credit');
    await user.click(within(dialog).getByRole('button', { name: 'Mark resolved' }));
    await waitFor(() =>
      expect(callsTo(fetchMock, 'POST /escalations/esc-1/resolve')[0]?.body).toEqual({ note: 'offer 10% credit' }),
    );
  });
});
