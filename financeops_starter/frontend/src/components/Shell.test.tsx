import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { callsTo, mockFetch, shellRoutes, signedInAt, summary } from '../test/fixtures';

const demoRunOut = { seed: 7, customers: 12, months: 1, speed: 360, invoices: 12, playing: true, simulated: true };

describe('top bar: spend meter', () => {
  it('shows model spend in US dollars against both caps', async () => {
    mockFetch(shellRoutes());
    signedInAt('/');
    render(<App />);
    const meter = await screen.findByRole('status', { name: 'Model spend' });
    expect(meter).toHaveTextContent('$1.20 today of $4.50 · $3.10 of $25 total');
    expect(meter).not.toHaveClass('capped');
  });

  it('says so when the cap is reached', async () => {
    mockFetch({ ...shellRoutes(), 'GET /summary': { body: { ...summary, model_calls_allowed: false } } });
    signedInAt('/');
    render(<App />);
    const meter = await screen.findByRole('status', { name: 'Model spend' });
    await waitFor(() => expect(meter).toHaveTextContent('Cap reached — agents paused, decisions come to you'));
    expect(meter).toHaveClass('capped');
  });
});

describe('top bar: run the month', () => {
  async function openDialog() {
    const user = userEvent.setup();
    const banner = await screen.findByRole('banner');
    await user.click(within(banner).getByRole('button', { name: /Run the month/ }));
    return { user, dialog: screen.getByRole('dialog', { name: 'Run the month' }) };
  }

  it('posts the seed, customers and speed chosen', async () => {
    const fetchMock = mockFetch({ ...shellRoutes(), 'POST /demo/run': { body: demoRunOut } });
    signedInAt('/outcomes');
    render(<App />);
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByLabelText('Seed')).toHaveValue('42');
    expect(within(dialog).getByLabelText('Customers')).toHaveValue(10);
    const seed = within(dialog).getByLabelText('Seed');
    await user.clear(seed);
    await user.type(seed, '7');
    const customers = within(dialog).getByLabelText('Customers');
    await user.clear(customers);
    await user.type(customers, '12');
    await user.selectOptions(within(dialog).getByLabelText('Speed'), '1 day / 4 s');
    await user.click(within(dialog).getByRole('button', { name: 'Start the month' }));

    expect(await screen.findByText('The month is running')).toBeInTheDocument();
    expect(screen.getByText('12 synthetic customers · 12 invoices · 1 day / 4 s')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST /demo/run').map((c) => c.body)).toEqual([
      { seed: 7, customers: 12, months: 1, speed: 360 },
    ]);
    // Back to the Flow page to watch it.
    expect(await screen.findByRole('heading', { level: 2, name: 'Flow' })).toBeInTheDocument();
  });

  it('uses the defaults: seed 42, ten customers, one day every ten seconds', async () => {
    const fetchMock = mockFetch({ ...shellRoutes(), 'POST /demo/run': { body: { ...demoRunOut, speed: 144 } } });
    signedInAt('/');
    render(<App />);
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Start the month' }));
    await screen.findByText('The month is running');
    expect(callsTo(fetchMock, 'POST /demo/run')[0]?.body).toEqual({ seed: 42, customers: 10, months: 1, speed: 144 });
  });

  it('explains when demo routes are off (404)', async () => {
    mockFetch({ ...shellRoutes(), 'POST /demo/run': { status: 404, body: { detail: 'Not Found' } } });
    signedInAt('/');
    render(<App />);
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Start the month' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/Demo routes are off.*FINANCEOPS_ENABLE_DEMO=1/);
  });

  it('says a run is already going on 409 and does not retry', async () => {
    const fetchMock = mockFetch({ ...shellRoutes(), 'POST /demo/run': { status: 409, body: { detail: 'A demo run is in progress' } } });
    signedInAt('/');
    render(<App />);
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Start the month' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('A run is already going. The server said: A demo run is in progress');
    expect(callsTo(fetchMock, 'POST /demo/run')).toHaveLength(1);
  });

  it('checks the customer count before sending', async () => {
    const fetchMock = mockFetch(shellRoutes());
    signedInAt('/');
    render(<App />);
    const { user, dialog } = await openDialog();
    const customers = within(dialog).getByLabelText('Customers');
    await user.clear(customers);
    await user.type(customers, '31');
    await user.click(within(dialog).getByRole('button', { name: 'Start the month' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Choose between 1 and 30 customers.');
    expect(callsTo(fetchMock, 'POST /demo/run')).toHaveLength(0);
  });
});
