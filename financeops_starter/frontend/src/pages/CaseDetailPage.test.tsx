import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import type { Action, AgentRun } from '../api/client';
import { HASH, callsTo, escalation, mockFetch, proposedAction, shellRoutes, signedInAt, workspace } from '../test/fixtures';

const WS = 'GET /cases/case-INV-1001/workspace';

function open(routes: Parameters<typeof mockFetch>[0] = {}, tab?: string) {
  const fetchMock = mockFetch({ ...shellRoutes(), [WS]: { body: workspace() }, ...routes });
  signedInAt(`/cases/case-INV-1001${tab ? `?tab=${tab}` : ''}`);
  const user = userEvent.setup();
  const view = render(<App />);
  return { fetchMock, user, view };
}

const bar = () => screen.findByRole('region', { name: 'Decision' });

describe('case detail: header and tabs', () => {
  it('shows the backend amounts, state and a simulated clock tag', async () => {
    open();
    const hero = await screen.findByRole('region', { name: /Maple Synthetic Ltd/ });
    expect(hero).toHaveTextContent('$1,250.00');
    expect(hero).toHaveTextContent('awaiting approval');
    expect(within(hero).getByText('Simulated clock')).toBeInTheDocument();
  });

  it('switches tabs and keeps the tab in the URL', async () => {
    const { user } = open();
    await user.click(await screen.findByRole('tab', { name: /Timeline/ }));
    expect(screen.getByRole('tab', { name: /Timeline/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/invoice created/)).toBeInTheDocument();
    expect(window.location.hash).toContain('tab=timeline');
  });

  it('renders evidence containing <script> as plain text, labelled untrusted', async () => {
    const { view } = open({}, 'evidence');
    const evidence = await screen.findByRole('article', { name: 'Evidence ev-1' });
    expect(evidence).toHaveTextContent('<script>alert("x")</script>');
    expect(within(evidence).getByText('Untrusted')).toBeInTheDocument();
    expect(view.container.querySelector('script')).toBeNull();
  });

  it('labels the message and delivery as simulated (Messages tab)', async () => {
    open({}, 'messages');
    const card = await screen.findByRole('article', { name: 'Action act-1' });
    expect(within(card).getByText('Simulated delivery')).toBeInTheDocument();
    expect(within(card).getByText('ap@maple.example')).toBeInTheDocument();
    expect(within(card).getByText('9f2c1e7ab4d0…')).toBeInTheDocument();
  });

  it('shows the autonomy decision and what superseded an old plan', async () => {
    const old: Action = { ...proposedAction, id: 'act-0', status: 'superseded', superseded_by: 'act-1' };
    const auto: Action = {
      ...proposedAction,
      status: 'dispatched',
      autonomy: {
        mode: 'auto',
        rule_id: 'small',
        policy_version: 2,
        reasons: ['under CAD 5,000', 'not disputed'],
        decided_at: '2026-11-16T10:00:00Z',
      },
    };
    open({ [WS]: { body: workspace({ actions: [auto, old] }) } }, 'messages');
    expect(await screen.findByText('Policy v2 · rule small: runs automatically')).toBeInTheDocument();
    const oldCard = screen.getByRole('article', { name: 'Action act-0' });
    expect(oldCard).toHaveTextContent('Superseded by act-1');
  });

  it('lists escalations for the case on the overview', async () => {
    open({ [WS]: { body: workspace({ escalations: [{ ...escalation, case_id: 'case-INV-1001' }] }) } });
    expect(await screen.findByRole('listitem', { name: 'Escalation esc-1' })).toHaveTextContent('Offer a credit?');
  });

  it('agent runs show tool calls on request, as plain text', async () => {
    const run: AgentRun = {
      id: 'run-1', agent: 'collections', case_id: 'case-INV-1001', action_id: null, status: 'no_proposal',
      model: 'claude-opus-5-5', served_model: null, started_at: '2026-11-16T09:00:00Z', finished_at: null,
      input_tokens: 100, output_tokens: 20, cache_read_tokens: 0, cache_write_tokens: 0, tool_calls: 1,
      summary: 'Nothing to do', findings: null, unknowns: null, detail: null, final_text: null,
      calls: [{ seq: 1, name: 'get_evidence', input: {}, result: '<img src=x onerror=alert(1)>', is_error: false, tool_use_id: 't1', created_at: '2026-11-16T09:00:01Z' }],
    };
    const { user, view } = open({ [WS]: { body: workspace({ agent_runs: [run] }) }, 'GET /agent-runs/run-1': { body: run } }, 'runs');
    await user.click(await screen.findByRole('button', { name: 'Show tool calls' }));
    expect(await screen.findByText('get_evidence')).toBeInTheDocument();
    expect(view.container.querySelector('img')).toBeNull();
  });
});

describe('case detail: sticky decision bar', () => {
  it('approve sends exactly the payload_hash that was shown, then refetches', async () => {
    const approved = { ...proposedAction, status: 'approved' as const };
    const { fetchMock, user } = open({
      [WS]: [{ body: workspace() }, { body: workspace({ actions: [approved] }) }],
      'POST /actions/act-1/approve': { body: approved },
    });
    const decision = await bar();
    expect(decision).toHaveTextContent('9f2c1e7ab4d0…');
    await user.click(within(decision).getByRole('button', { name: 'Approve this exact message' }));
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toEqual([expect.objectContaining({ body: { payload_hash: HASH } })]);
    expect(await within(await bar()).findByRole('button', { name: /Execute/ })).toBeInTheDocument();
  });

  it('on 409 refetches and shows "this case changed" without retrying', async () => {
    const { fetchMock, user } = open({
      'POST /actions/act-1/approve': { status: 409, body: { detail: 'payload hash does not match' } },
    });
    await user.click(within(await bar()).getByRole('button', { name: 'Approve this exact message' }));
    expect(await screen.findByText('This case changed — here is the latest.')).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fetchMock, WS).length).toBeGreaterThanOrEqual(2));
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toHaveLength(1);
  });

  it('reject asks to confirm and shows the server detail for a 422', async () => {
    const { user } = open({
      'POST /actions/act-1/reject': { status: 422, body: { detail: 'action is not awaiting a decision' } },
    });
    await user.click(within(await bar()).getByRole('button', { name: 'Reject…' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reject' }));
    expect(await screen.findByText('action is not awaiting a decision')).toBeInTheDocument();
  });

  it('keeps approve disabled until the refreshed case has arrived', async () => {
    let release: () => void = () => {};
    const slow = new Promise<void>((resolve) => (release = resolve));
    const { fetchMock, user } = open({
      [WS]: [{ body: workspace() }, { body: workspace(), wait: slow }],
      'POST /actions/act-1/approve': { body: proposedAction },
    });
    const approve = within(await bar()).getByRole('button', { name: 'Approve this exact message' });
    await user.click(approve);
    await waitFor(() => expect(callsTo(fetchMock, WS).length).toBeGreaterThanOrEqual(2));
    expect(approve).toBeDisabled();
    await user.click(approve);
    expect(callsTo(fetchMock, 'POST /actions/act-1/approve')).toHaveLength(1);
    release();
    await waitFor(() => expect(approve).toBeEnabled());
  });

  it('after a network error on approve, refetches instead of inviting a blind retry', async () => {
    const { fetchMock, user } = open({ 'POST /actions/act-1/approve': 'network-error' });
    await user.click(within(await bar()).getByRole('button', { name: 'Approve this exact message' }));
    expect(await screen.findByText(/Could not reach the FinanceOps server/)).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fetchMock, WS).length).toBeGreaterThanOrEqual(2));
  });
});

describe('case detail: paid investigation', () => {
  it('needs an explicit confirm before the paid call', async () => {
    const { fetchMock, user } = open();
    await user.click(await screen.findByRole('button', { name: /Investigate with AI/ }));
    expect(screen.getByText(/calls a paid Claude model/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(callsTo(fetchMock, 'POST /cases/case-INV-1001/investigate')).toHaveLength(0);
  });

  it('after a failed paid investigation, another run needs a fresh confirm', async () => {
    const { fetchMock, user } = open({ 'POST /cases/case-INV-1001/investigate': 'network-error' });
    await user.click(await screen.findByRole('button', { name: /Investigate with AI/ }));
    await user.click(screen.getByRole('button', { name: 'Yes, run the paid investigation' }));
    expect(await screen.findByText(/Could not reach the FinanceOps server/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Yes, run the paid investigation' })).toBeNull();
    expect(callsTo(fetchMock, 'POST /cases/case-INV-1001/investigate')).toHaveLength(1);
  });
});
