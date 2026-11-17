import { useCallback, useState } from 'react';
import { useAgents, useChange } from '../api/queries';
import type { AgentInfo } from '../api/client';
import { formatTime, humanize } from '../format';
import { useToast } from '../components/Shell';
import { ConfirmDialog, EmptyState, ErrorBox, Skeleton } from '../components/common';

export function AgentsPage() {
  const agents = useAgents();
  const { run, busy } = useChange();
  const push = useToast();
  const [confirming, setConfirming] = useState<AgentInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const close = useCallback(() => {
    setConfirming(null);
    setError(null);
  }, []);

  const toggle = async () => {
    if (!confirming) return;
    const agent = confirming;
    const result = await run((c) => (agent.paused ? c.resumeAgent(agent.name) : c.pauseAgent(agent.name)));
    if (result.ok) {
      push({ tone: 'success', title: `${agent.title} ${agent.paused ? 'resumed' : 'paused'}` });
      close();
    } else setError(result.error.detail);
  };

  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>Agents</h2>
        <p className="muted">
          The AI agents that work your cases. They can only read evidence and propose; code and your policy decide what
          runs.
        </p>
      </div>
      {agents.isPending && <Skeleton label="Loading agents" rows={4} />}
      {agents.error && <ErrorBox error={agents.error} onRetry={() => void agents.refetch()} />}
      {agents.data && agents.data.agents.length === 0 && <EmptyState title="No agents configured" />}
      <div className="agent-grid">
        {agents.data?.agents.map((agent) => {
          const outcomes = Object.entries(agent.outcomes_7d);
          return (
            <section key={agent.name} className="panel agent-card" aria-label={agent.title}>
              <div className="panel-head">
                <h3>{agent.title}</h3>
                <span className={agent.paused ? 'badge state-escalated' : 'badge state-resolved'}>
                  {agent.paused ? 'paused' : 'active'}
                </span>
              </div>
              <ul className="kv">
                <li>
                  <span className="k">Model</span>
                  <span className="v">
                    <code>{agent.model}</code>
                  </span>
                </li>
                <li>
                  <span className="k">Can propose actions</span>
                  <span className="v">{agent.can_write ? 'yes (you or policy approve)' : 'no (read only)'}</span>
                </li>
                <li>
                  <span className="k">Runs today</span>
                  <span className="v">{agent.runs_today}</span>
                </li>
                <li>
                  <span className="k">Last run</span>
                  <span className="v">{agent.last_run_at ? formatTime(agent.last_run_at) : 'never'}</span>
                </li>
              </ul>
              <h4 className="small-heading">Outcomes, last 7 days</h4>
              {outcomes.length === 0 ? (
                <p className="muted small">No runs in the last 7 days.</p>
              ) : (
                <ul className="outcomes">
                  {outcomes.map(([status, n]) => (
                    <li key={status}>
                      <span className="outcome-n">{n}</span> {humanize(status)}
                    </li>
                  ))}
                </ul>
              )}
              <button
                type="button"
                className={agent.paused ? 'secondary' : 'danger'}
                disabled={busy}
                onClick={() => setConfirming(agent)}
              >
                {agent.paused ? `Resume ${agent.title}` : `Pause ${agent.title}`}
              </button>
            </section>
          );
        })}
      </div>
      {confirming && (
        <ConfirmDialog
          title={confirming.paused ? `Resume ${confirming.title}?` : `Pause ${confirming.title}?`}
          confirmLabel={confirming.paused ? 'Yes, resume' : 'Yes, pause'}
          tone={confirming.paused ? 'primary' : 'danger'}
          busy={busy}
          onConfirm={() => void toggle()}
          onCancel={close}
        >
          <p>
            {confirming.paused
              ? 'The agent can be started again by the orchestrator.'
              : 'The orchestrator will not start this agent until you resume it. Cases that need it wait or escalate.'}
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}
