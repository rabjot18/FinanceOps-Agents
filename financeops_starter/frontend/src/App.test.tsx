import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from './App';
import {
  TOKEN,
  activity,
  activityItem,
  attention,
  autonomyPaused,
  callsTo,
  caseList,
  mockFetch,
  shellRoutes,
  signedInAt,
  summary,
} from './test/fixtures';

const BANNER = 'Synthetic data · simulated delivery · no real money moves';

describe('token entry', () => {
  it('asks for the token, labels it demo-only, then opens the Flow page with it', async () => {
    const fetchMock = mockFetch(shellRoutes());
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByText(/local demo only, not production authentication/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText('Owner token'), TOKEN);
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByRole('heading', { level: 2, name: 'Flow' })).toBeInTheDocument();
    expect(sessionStorage.getItem('financeops.ownerToken')).toBe(TOKEN);
    await waitFor(() => expect(callsTo(fetchMock, 'GET /attention')[0]?.headers.Authorization).toBe(`Bearer ${TOKEN}`));
  });

  it('goes back to token entry and forgets the token on 401', async () => {
    mockFetch({ ...shellRoutes(), 'GET /summary': { status: 401, body: { detail: 'Owner authentication required' } } });
    signedInAt('/');
    render(<App />);

    expect(await screen.findByLabelText('Owner token')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/rejected the owner token/i);
    expect(sessionStorage.getItem('financeops.ownerToken')).toBeNull();
  });

  it('a 401 on an owner action also ends the session', async () => {
    mockFetch({
      ...shellRoutes(),
      'GET /attention': { body: attention() },
      'POST /actions/act-1/approve': { status: 401, body: { detail: 'Owner authentication required' } },
    });
    signedInAt('/home');
    const user = userEvent.setup();
    render(<App />);
    const queue = await screen.findByRole('list', { name: 'Actions waiting for you' });
    await user.click(within(queue).getAllByRole('button', { name: 'Approve this exact message' })[0] as HTMLElement);
    expect(await screen.findByLabelText('Owner token')).toBeInTheDocument();
  });

  it('clears the token on request', async () => {
    mockFetch(shellRoutes());
    signedInAt('/');
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Clear token' }));
    expect(screen.getByLabelText('Owner token')).toBeInTheDocument();
    expect(sessionStorage.getItem('financeops.ownerToken')).toBeNull();
  });
});

describe('app shell', () => {
  it('shows the synthetic banner and navigation on every page', async () => {
    mockFetch({
      ...shellRoutes(),
      'GET /cases': { body: caseList },
      'GET /agents': { body: { agents: [] } },
    });
    signedInAt('/');
    const user = userEvent.setup();
    render(<App />);
    const nav = await screen.findByRole('navigation', { name: 'Main' });

    // Three primary pages; the rest of the console sits under a collapsed "Advanced" group.
    expect(await screen.findByRole('heading', { level: 2, name: 'Flow' })).toBeInTheDocument();
    expect(within(nav).queryByRole('link', { name: /Cases/ })).toBeNull();
    const advanced = within(nav).getByRole('button', { name: /Advanced/ });
    expect(advanced).toHaveAttribute('aria-expanded', 'false');
    await user.click(advanced);
    expect(advanced).toHaveAttribute('aria-expanded', 'true');

    for (const [link, heading] of [
      ['Your decisions', 'Your decisions'],
      ['Outcomes', 'Outcomes'],
      ['Cases', 'Cases'],
      ['Activity', 'Activity'],
      ['Agents', 'Agents'],
      ['Needs you', 'Needs you'],
      ['Flow', 'Flow'],
    ] as const) {
      await user.click(within(nav).getByRole('link', { name: new RegExp(link) }));
      expect(await screen.findByRole('heading', { level: 2, name: heading })).toBeInTheDocument();
      expect(screen.getByText(BANNER)).toBeInTheDocument();
    }
  });

  it('upgrades an old #case=<id> link to the case page', async () => {
    mockFetch({ ...shellRoutes(), 'GET /cases/case-X/workspace': { status: 404, body: { detail: 'Unknown case' } } });
    sessionStorage.setItem('financeops.ownerToken', TOKEN);
    window.location.hash = '#case=case-X';
    render(<App />);
    expect(await screen.findByText('This case does not exist')).toBeInTheDocument();
  });

  it('kill switch: pausing autonomy needs a confirm and sends the reason', async () => {
    const fetchMock = mockFetch({
      ...shellRoutes(),
      'POST /autonomy/pause': { body: autonomyPaused },
      'GET /autonomy': [{ body: { ...autonomyPaused, paused: false } }, { body: autonomyPaused }],
    });
    signedInAt('/');
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: 'Pause autonomy' }));
    const dialog = screen.getByRole('dialog', { name: 'Pause all automatic actions?' });
    expect(callsTo(fetchMock, 'POST /autonomy/pause')).toHaveLength(0);
    await user.type(within(dialog).getByLabelText('Reason (optional)'), 'audit');
    await user.click(within(dialog).getByRole('button', { name: 'Yes, pause autonomy' }));

    expect(await screen.findByText('Autonomy paused', { selector: '.autonomy-pill' })).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /autonomy/pause')[0]?.body).toEqual({ reason: 'audit' });
    expect(screen.getByRole('button', { name: 'Resume autonomy' })).toBeInTheDocument();
  });

  it('kill switch: cancel sends nothing', async () => {
    const fetchMock = mockFetch(shellRoutes());
    signedInAt('/');
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Pause autonomy' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(callsTo(fetchMock, 'POST /autonomy/pause')).toHaveLength(0);
  });

  it('polls activity and summary, and toasts new items that need the owner', async () => {
    let polls = 0;
    const fetchMock = mockFetch({
      ...shellRoutes(),
      // First load: one old item. A later poll brings a new proposal.
      'GET /activity': (_init, url) => {
        if (url.includes('after=0')) return { body: activity([activityItem(1)]) };
        polls += 1;
        if (polls === 2) return { body: activity([activityItem(2, { kind: 'action_proposed', summary: 'New reminder proposed' })]) };
        return { body: activity([], polls > 2 ? 2 : 1) };
      },
      'GET /attention': [{ body: { ...attention(), approvals: [], escalations: [] } }, { body: attention() }],
      'GET /summary': [{ body: summary }, { body: { ...summary, awaiting_owner: 3 } }],
    });
    signedInAt('/');
    render(<App pollMs={40} />);

    // The new approvals arrive after the poll; each one gets a toast with a link.
    const toasts = await screen.findByRole('region', { name: 'Notifications' });
    expect(await within(toasts).findByText('Maple Synthetic Ltd needs you', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(within(toasts).getByText('Cedar Synthetic Inc needs you')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'GET /summary').length).toBeGreaterThanOrEqual(2);
    expect(callsTo(fetchMock, 'GET /activity').some((c) => c.url.includes('after=1'))).toBe(true);
  });
});
