import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { activity, activityItem, callsTo, mockFetch, shellRoutes, signedInAt } from '../test/fixtures';

describe('activity feed', () => {
  it('polling picks up new activity (newest first) and refreshes what it affects', async () => {
    let afterOne = 0;
    const fetchMock = mockFetch({
      ...shellRoutes(),
      'GET /activity': (_init, url) => {
        if (url.includes('after=0')) return { body: activity([activityItem(1)]) };
        afterOne += 1;
        // The second poll after the first load brings a new automatic send.
        if (afterOne === 2) return { body: activity([activityItem(2, { summary: 'Reminder 2 sent to Northwind automatically' })]) };
        return { body: activity([], afterOne > 2 ? 2 : 1) };
      },
    });
    signedInAt('/activity');
    render(<App pollMs={40} />);

    const feed = await screen.findByRole('list', { name: 'Activity feed' });
    expect(within(feed).getAllByRole('listitem')).toHaveLength(1);
    const summariesBefore = callsTo(fetchMock, 'GET /summary').length;
    const attentionBefore = callsTo(fetchMock, 'GET /attention').length;

    expect(await within(feed).findByText('Reminder 2 sent to Northwind automatically')).toBeInTheDocument();
    const items = within(feed).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Reminder 2 sent to Northwind automatically');
    expect(items[1]).toHaveTextContent('Reminder 1 sent to Maple');
    expect(items[0]).toHaveTextContent('Synthetic');
    // The cursor moved forward, and the new item refreshed the queue and KPIs.
    await waitFor(() => expect(callsTo(fetchMock, 'GET /activity').some((c) => c.url.includes('after=2'))).toBe(true));
    await waitFor(() => expect(callsTo(fetchMock, 'GET /attention').length).toBeGreaterThan(attentionBefore));
    expect(callsTo(fetchMock, 'GET /summary').length).toBeGreaterThan(summariesBefore);
  });

  it('filters by kind and links to the case', async () => {
    mockFetch({
      ...shellRoutes(),
      'GET /activity': {
        body: activity([
          activityItem(1),
          activityItem(2, { kind: 'escalation_opened', summary: 'Agent escalated a dispute', case_id: 'case-INV-2002' }),
        ]),
      },
    });
    signedInAt('/activity');
    const user = userEvent.setup();
    render(<App />);
    const feed = await screen.findByRole('list', { name: 'Activity feed' });
    expect(within(feed).getAllByRole('listitem')).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Needs you' }));
    expect(within(feed).getAllByRole('listitem')).toHaveLength(1);
    expect(within(feed).getByRole('link', { name: 'case-INV-2002' })).toBeInTheDocument();
  });

  it('has a helpful empty state', async () => {
    mockFetch(shellRoutes());
    signedInAt('/activity');
    render(<App />);
    expect(await screen.findByText('Nothing has happened yet')).toBeInTheDocument();
  });
});
