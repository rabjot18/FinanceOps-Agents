import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from './App';
import { HASH, TOKEN, callsTo, caseList, inbox, mockFetch, proposedAction, scenarios, workspace } from './test/fixtures';

const WS = 'GET /cases/case-INV-1001/workspace';

function signedInAt(caseId?: string) {
  sessionStorage.setItem('financeops.ownerToken', TOKEN);
  if (caseId) window.location.hash = `case=${caseId}`;
}

const baseRoutes = {
  'GET /cases': { body: caseList },
  'GET /demo/scenarios': { body: scenarios },
};

describe('token entry', () => {
  it('asks for the token, labels it demo-only, then loads cases with it', async () => {
    const fetchMock = mockFetch(baseRoutes);
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByText(/local demo only, not production authentication/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText('Owner token'), TOKEN);
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByText('Maple Synthetic Ltd')).toBeInTheDocument();
    expect(sessionStorage.getItem('financeops.ownerToken')).toBe(TOKEN);
    expect(callsTo(fetchMock, 'GET /cases')[0]?.headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it('goes back to token entry and forgets the token on 401', async () => {
    mockFetch({ ...baseRoutes, 'GET /cases': { status: 401, body: { detail: 'Owner authentication required' } } });
    signedInAt();
    render(<App />);

    expect(await screen.findByLabelText('Owner token')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/rejected the owner token/i);
    expect(sessionStorage.getItem('financeops.ownerToken')).toBeNull();
  });

  it('clears the token on request', async () => {
    mockFetch(baseRoutes);
    signedInAt();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('Maple Synthetic Ltd');
    await user.click(screen.getByRole('button', { name: 'Clear token' }));
    expect(screen.getByLabelText('Owner token')).toBeInTheDocument();
    expect(sessionStorage.getItem('financeops.ownerToken')).toBeNull();
  });
});

describe('loading and errors', () => {
  it('shows a loading state, then the case list with backend-formatted money', async () => {
    mockFetch(baseRoutes);
    signedInAt();
    render(<App />);
    expect(screen.getByText('Loading cases…')).toBeInTheDocument();
    const row = await screen.findByRole('button', { name: /Maple Synthetic Ltd/ });
    expect(row).toHaveTextContent('awaiting approval');
    expect(row).toHaveTextContent('$1,250.00');
    expect(row).toHaveTextContent('1 pending action');
  });

  it('shows a network failure with a retry that works', async () => {
    mockFetch({ ...baseRoutes, 'GET /cases': ['network-error', { body: caseList }] });
    signedInAt();
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText(/Could not reach the FinanceOps server/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Maple Synthetic Ltd')).toBeInTheDocument();
  });
});

describe('case workspace', () => {
  it('renders evidence containing <script> as plain text, labelled untrusted', async () => {
    mockFetch({ ...baseRoutes, [WS]: { body: workspace() } });
    signedInAt('case-INV-1001');
    const { container } = render(<App />);

    const evidence = await screen.findByRole('article', { name: 'Evidence ev-1' });
    expect(evidence).toHaveTextContent('<script>alert("x")</script>');
    expect(within(evidence).getByText('Untrusted')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
  });

  it('labels the message and delivery as simulated', async () => {
    mockFetch({ ...baseRoutes, [WS]: { body: workspace() } });
    signedInAt('case-INV-1001');
    render(<App />);

    const card = await screen.findByRole('article', { name: 'Action act-1' });
    expect(within(card).getByText('Simulated delivery')).toBeInTheDocument();
    expect(within(card).getByText('Simulated')).toBeInTheDocument();
    expect(within(card).getByText('ap@maple.example')).toBeInTheDocument();
    expect(within(card).getByText('$1,250.00')).toBeInTheDocument();
    expect(within(card).getByText('9f2c1e7ab4d0…')).toBeInTheDocument();
  });

  it('approve sends exactly the payload_hash that was shown, then refetches', async () => {
    const approved = { ...proposedAction, status: 'approved' as const };
    const fetchMock = mockFetch({
      ...baseRoutes,
      [WS]: [{ body: workspace() }, { body: workspace({ actions: [approved] }) }],
      'POST /actions/act-1/approve': { body: approved },
    });
    signedInAt('case-INV-1001');
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: 'Approve this exact message' }));

    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toEqual([
      expect.objectContaining({ body: { payload_hash: HASH } }),
    ]);
    expect(await screen.findByRole('button', { name: /Execute/ })).toBeInTheDocument();
    expect(callsTo(fetchMock, WS).length).toBeGreaterThanOrEqual(2);
  });

  it('on 409 refetches and shows "this case changed — review again" without retrying', async () => {
    const fetchMock = mockFetch({
      ...baseRoutes,
      [WS]: { body: workspace() },
      'POST /actions/act-1/approve': { status: 409, body: { detail: 'payload hash does not match' } },
    });
    signedInAt('case-INV-1001');
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: 'Approve this exact message' }));

    expect(await screen.findByText('This case changed — review again.')).toBeInTheDocument();
    expect(screen.getByText(/payload hash does not match/)).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fetchMock, WS).length).toBeGreaterThanOrEqual(2));
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toHaveLength(1);
  });

  it('shows the server detail for a 422', async () => {
    mockFetch({
      ...baseRoutes,
      [WS]: { body: workspace() },
      'POST /actions/act-1/reject': { status: 422, body: { detail: 'action is not awaiting a decision' } },
    });
    signedInAt('case-INV-1001');
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: 'Reject' }));
    expect(await screen.findByText('action is not awaiting a decision')).toBeInTheDocument();
  });

  it('investigation needs an explicit confirm before the paid call', async () => {
    const fetchMock = mockFetch({ ...baseRoutes, [WS]: { body: workspace() } });
    signedInAt('case-INV-1001');
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: /Investigate with AI/ }));
    expect(screen.getByText(/calls a paid Claude model/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(callsTo(fetchMock, 'POST /cases/case-INV-1001/investigate')).toHaveLength(0);
  });
});

describe('demo lab', () => {
  it('labels the lab and the inbox as simulated', async () => {
    mockFetch({ ...baseRoutes, 'GET /demo/inbox': { body: inbox } });
    signedInAt();
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText('Synthetic data · simulated')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Load baseline' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show simulated inbox' }));
    expect(await screen.findByText('Simulated inbox — no real email was sent')).toBeInTheDocument();
  });

  it('never asks the worker to run paid investigations', async () => {
    const fetchMock = mockFetch({
      ...baseRoutes,
      'POST /demo/worker/run': {
        body: {
          simulated_time: '2026-11-16T11:00:00Z',
          result: {
            sent: ['act-1'], failed: [], unknown: [], cancelled: [], follow_ups_elapsed: [],
            investigations: [], investigations_waiting: 0, errors: {},
          },
        },
      },
    });
    signedInAt();
    const user = userEvent.setup();
    render(<App />);

    const minutes = await screen.findByLabelText(/Advance simulated clock/);
    await user.clear(minutes);
    await user.type(minutes, '90');
    await user.click(screen.getByRole('button', { name: 'Run worker once' }));
    expect(await screen.findByText(/Sent to simulated inbox: act-1/)).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /demo/worker/run')[0]?.body).toEqual({
      advance_minutes: 90,
      run_investigations: false,
    });
  });
});
