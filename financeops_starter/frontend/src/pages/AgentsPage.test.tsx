import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { agents, callsTo, mockFetch, shellRoutes, signedInAt } from '../test/fixtures';

describe('agents page', () => {
  it('shows each agent with its runs and outcomes', async () => {
    mockFetch({ ...shellRoutes(), 'GET /agents': { body: agents } });
    signedInAt('/agents');
    render(<App />);
    const collections = await screen.findByRole('region', { name: 'Collections agent' });
    expect(collections).toHaveTextContent('claude-opus-5-5');
    expect(collections).toHaveTextContent('Runs today3');
    expect(collections).toHaveTextContent('4 proposed');
    expect(collections).toHaveTextContent('1 escalated');
    const briefing = screen.getByRole('region', { name: 'Owner briefing agent' });
    expect(briefing).toHaveTextContent('paused');
    expect(briefing).toHaveTextContent('No runs in the last 7 days.');
  });

  it('pausing an agent needs a confirm', async () => {
    const fetchMock = mockFetch({
      ...shellRoutes(),
      'GET /agents': { body: agents },
      'POST /agents/collections/pause': { body: { ...agents.agents[0], paused: true } },
    });
    signedInAt('/agents');
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Pause Collections agent' }));
    expect(callsTo(fetchMock, 'POST /agents/collections/pause')).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Yes, pause' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST /agents/collections/pause')).toHaveLength(1));
    await waitFor(() => expect(callsTo(fetchMock, 'GET /agents').length).toBeGreaterThanOrEqual(2));
  });

  it('resumes a paused agent', async () => {
    const fetchMock = mockFetch({
      ...shellRoutes(),
      'GET /agents': { body: agents },
      'POST /agents/briefing/resume': { body: { ...agents.agents[1], paused: false } },
    });
    signedInAt('/agents');
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Resume Owner briefing agent' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Yes, resume' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST /agents/briefing/resume')).toHaveLength(1));
  });
});
