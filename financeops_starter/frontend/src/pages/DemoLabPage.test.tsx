import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { callsTo, caseList, clock, inbox, mockFetch, scenarios, shellRoutes, signedInAt, workspace } from '../test/fixtures';

function lab(routes: Parameters<typeof mockFetch>[0] = {}) {
  const fetchMock = mockFetch({
    ...shellRoutes(),
    'GET /demo/scenarios': { body: scenarios },
    'GET /demo/clock': { body: clock },
    'GET /cases': { body: caseList },
    'GET /cases/case-INV-1001/workspace': { body: workspace() },
    ...routes,
  });
  signedInAt('/demo');
  const user = userEvent.setup();
  render(<App />);
  return { fetchMock, user };
}

describe('demo lab', () => {
  it('labels the lab and the inbox as simulated', async () => {
    const { user } = lab({ 'GET /demo/inbox': { body: inbox } });
    expect(await screen.findByText('Synthetic data · simulated')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Load baseline' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show simulated inbox' }));
    expect(await screen.findByText('Simulated inbox — no real email was sent')).toBeInTheDocument();
  });

  it('loading a scenario links to the new cases', async () => {
    const { user } = lab({ 'POST /demo/scenarios/baseline': { body: { scenario: 'baseline', case_ids: ['case-INV-1001'] } } });
    await user.click(await screen.findByRole('button', { name: 'Load baseline' }));
    expect(await screen.findByRole('link', { name: 'Open case-INV-1001' })).toBeInTheDocument();
  });

  it('never asks the worker to run paid investigations', async () => {
    const { fetchMock, user } = lab({
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
    const minutes = await screen.findByLabelText(/Advance simulated clock/);
    await user.clear(minutes);
    await user.type(minutes, '90');
    await user.click(screen.getByRole('button', { name: 'Run worker once' }));
    expect(await screen.findByText(/Sent to simulated inbox: act-1/)).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /demo/worker/run')[0]?.body).toEqual({ advance_minutes: 90, run_investigations: false });
  });

  it('clock: play and speed', async () => {
    const { fetchMock, user } = lab({
      'POST /demo/clock': (init) => {
        const body = JSON.parse(String(init.body)) as { action: string; speed?: number };
        return { body: { ...clock, playing: body.action !== 'pause', speed: body.speed ?? clock.speed } };
      },
    });
    await user.click(await screen.findByRole('button', { name: '▶ Play' }));
    expect(await screen.findByRole('button', { name: '❚❚ Pause' })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Speed'), '1440');
    await waitFor(() =>
      expect(callsTo(fetchMock, 'POST /demo/clock').map((c) => c.body)).toEqual([
        { action: 'play' },
        { action: 'speed', speed: 1440 },
      ]),
    );
  });

  it('clock: explains when simulated time is off (409)', async () => {
    lab({ 'GET /demo/clock': { status: 409, body: { detail: 'Simulated time is off; it is only available in the demo' } } });
    expect(await screen.findByText('Simulated time is off; it is only available in the demo')).toBeInTheDocument();
  });

  it('generator answers 503 "Not built yet": says so and the rest of the lab still works', async () => {
    const { fetchMock, user } = lab({
      'POST /demo/generator/run': { status: 503, body: { detail: 'Not built yet (Milestone 5 step 1b)' } },
      'POST /demo/scenarios/baseline': { body: { scenario: 'baseline', case_ids: ['case-INV-1001'] } },
    });
    await user.click(await screen.findByRole('button', { name: 'Generate business' }));
    expect(await screen.findByText('The business generator is not available yet.')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /demo/generator/run')[0]?.body).toEqual({ seed: 42, months: 6, customers: 25 });
    await user.click(screen.getByRole('button', { name: 'Load baseline' }));
    expect(await screen.findByRole('link', { name: 'Open case-INV-1001' })).toBeInTheDocument();
  });

  it('adds a synthetic settlement to the picked case', async () => {
    const { fetchMock, user } = lab({
      'POST /demo/settlements': { body: { ...workspace().invoice, outstanding_cents: 0, settled_cents: 125000 } },
    });
    const events = await screen.findByRole('region', { name: 'Synthetic events' });
    await user.click(await within(events).findByRole('button', { name: /Use outstanding/ }));
    await user.click(within(events).getByRole('button', { name: 'Add synthetic settlement' }));
    expect(await within(events).findByText(/Outstanding is now \$0\.00/)).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /demo/settlements')[0]?.body).toMatchObject({
      invoice_id: 'INV-1001',
      amount_cents: 125000,
      currency: 'CAD',
    });
  });
});
