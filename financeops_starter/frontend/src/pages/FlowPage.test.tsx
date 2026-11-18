import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import {
  agentWrittenAction,
  callsTo,
  caseFlow,
  emptyFlow,
  flowOut,
  flowStep,
  mockFetch,
  shellRoutes,
  signedInAt,
  workspace,
} from '../test/fixtures';

function flowPage(routes: Parameters<typeof mockFetch>[0] = {}, pollMs?: number) {
  const fetchMock = mockFetch({ ...shellRoutes(), 'GET /flow': { body: flowOut() }, ...routes });
  signedInAt('/');
  const user = userEvent.setup();
  render(pollMs ? <App pollMs={pollMs} /> : <App />);
  return { fetchMock, user };
}

describe('flow board', () => {
  it('shows every stage with the count the backend sent and case chips', async () => {
    flowPage();
    const contacted = await screen.findByRole('region', { name: 'Contacted: 7 cases' }, { timeout: 4000 });
    // At most four chips per stage, then "+N more".
    expect(within(contacted).getAllByRole('button')).toHaveLength(4);
    expect(within(contacted).getByText('+3 more')).toBeInTheDocument();
    // Most recently moved first.
    expect(within(contacted).getAllByRole('button')[0]).toHaveTextContent('Maple Synthetic Ltd$1,250.00');

    expect(screen.getByRole('region', { name: 'Monitoring: 1 case' })).toHaveTextContent('Birch Synthetic Co');
    expect(screen.getByRole('region', { name: 'Needs you: 1 case' })).toHaveTextContent('Cedar Synthetic Inc');
    expect(screen.getByRole('region', { name: 'Issued: 0 cases' })).toHaveTextContent('No cases');
    expect(screen.getByRole('region', { name: 'Disputed: 0 cases' })).toBeInTheDocument();
  });

  it('shows the agent team and the latest steps, newest first', async () => {
    flowPage();
    const steps = await screen.findByRole('list', { name: 'Latest steps' }, { timeout: 4000 });
    const items = within(steps).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Payment verified for Aspen');
    expect(items[2]).toHaveTextContent('Invoice INV-8 opened');
    const team = screen.getByRole('list', { name: 'Agent team' });
    for (const name of ['Intake', 'Rules', 'Collections agent', 'Disputes agent', 'Customer', 'Verifier', 'Owner', 'Reporter', 'Stripe'])
      expect(team).toHaveTextContent(name);
    expect(within(team).getByText('simulated · Haiku')).toBeInTheDocument();
  });

  it('labels each edge with who moves cases there (recent steps first, then the usual actor)', async () => {
    flowPage({
      'GET /flow': {
        body: flowOut({
          recent: [
            flowStep(20, { stage: 'contacted', actor: 'owner', summary: 'Owner sent it' }),
            flowStep(21, { stage: 'contacted', actor: 'owner', summary: 'Owner sent another' }),
            flowStep(22, { stage: 'contacted', actor: 'collections', summary: 'Agent sent one' }),
          ],
        }),
      },
    });
    const canvas = await screen.findByRole('region', { name: 'Case flow board' }, { timeout: 4000 });
    await waitFor(() => expect(canvas.querySelectorAll('.edge-actor').length).toBeGreaterThan(0));
    const labels = [...canvas.querySelectorAll('.edge-actor-name')].map((e) => e.textContent);
    expect(labels).toContain('You'); // the most frequent actor into Contacted
    expect(labels).toContain('Rules'); // fallback for Overdue
    expect(labels).toContain('Verifier');
    expect(labels).toContain('Disputes agent'); // into Disputed
  });

  it('opens a drawer with the case path, the exact agent-written message and the reply as plain text', async () => {
    const { user, fetchMock } = flowPage({
      'GET /cases/case-INV-1001/flow': { body: caseFlow },
      'GET /cases/case-INV-1001/workspace': { body: workspace({ actions: [agentWrittenAction] }) },
    });
    const contacted = await screen.findByRole('region', { name: 'Contacted: 7 cases' }, { timeout: 4000 });
    await user.click(within(contacted).getByRole('button', { name: /Maple Synthetic Ltd/ }));

    const drawer = await screen.findByRole('dialog', { name: 'Maple Synthetic Ltd' });
    const path = await within(drawer).findByRole('list', { name: 'Case path' });
    const steps = within(path).getAllByRole('listitem');
    expect(steps).toHaveLength(4);
    expect(steps[0]).toHaveTextContent('Issued');
    expect(steps[0]).toHaveTextContent('Intake');
    expect(await within(drawer).findByText('Written by Collections agent')).toBeInTheDocument();
    expect(within(drawer).getByText('A friendly note about INV-1001')).toBeInTheDocument();
    expect(within(drawer).getByText('Customer reply · untrusted')).toBeInTheDocument();
    // The reply's markup stays text.
    expect(within(drawer).getByText(/<script>alert\("x"\)<\/script> pay later/)).toBeInTheDocument();
    expect(drawer.querySelector('script')).toBeNull();
    expect(within(drawer).getByRole('link', { name: /Open the full case/ })).toHaveAttribute('href', '#/cases/case-INV-1001');
    expect(callsTo(fetchMock, 'POST /cases/case-INV-1001/investigate')).toHaveLength(0);

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('animates the edge into a stage that got a new step since the last poll', async () => {
    flowPage(
      {
        'GET /flow': [
          { body: flowOut() },
          {
            body: flowOut({
              recent: [...flowOut().recent, flowStep(13, { stage: 'needs_you', actor: 'collections', summary: 'Asked the owner' })],
            }),
          },
        ],
      },
      60,
    );
    const canvas = await screen.findByRole('region', { name: 'Case flow board' }, { timeout: 4000 });
    await waitFor(() => expect(canvas.querySelector('.react-flow__edge.animated')).not.toBeNull(), { timeout: 3000 });
    expect(canvas.querySelectorAll('.react-flow__edge.animated')).toHaveLength(1);
    expect(screen.getByRole('list', { name: 'Latest steps' })).toHaveTextContent('Asked the owner');
  });

  it('with no cases, offers a big Run the month call to action', async () => {
    const { user } = flowPage({ 'GET /flow': { body: emptyFlow } });
    expect(await screen.findByText('No cases yet. Let the agents run a month.')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Case flow board' })).toBeNull();
    const main = screen.getByRole('main');
    await user.click(within(main).getByRole('button', { name: /Run the month/ }));
    expect(screen.getByRole('dialog', { name: 'Run the month' })).toBeInTheDocument();
  });
});
