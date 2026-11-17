import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useActivityFeed } from '../api/queries';
import type { ActivityItem } from '../api/client';
import { formatTime, humanize } from '../format';
import { EmptyState, ErrorBox, SimulatedTag, Skeleton } from '../components/common';

const GROUPS: { id: string; label: string; kinds: ActivityItem['kind'][] | null }[] = [
  { id: 'all', label: 'Everything', kinds: null },
  {
    id: 'owner',
    label: 'Needs you',
    kinds: ['action_proposed', 'escalation_opened'],
  },
  {
    id: 'messages',
    label: 'Messages',
    kinds: ['action_auto_approved', 'action_approved', 'action_rejected', 'action_superseded', 'message_queued', 'message_sent', 'message_cancelled'],
  },
  { id: 'agents', label: 'Agents', kinds: ['agent_run_started', 'agent_run_finished', 'briefing_published'] },
  {
    id: 'controls',
    label: 'Policy and controls',
    kinds: ['policy_changed', 'autonomy_paused', 'autonomy_resumed', 'agent_paused', 'agent_resumed', 'escalation_resolved'],
  },
];

const ICON: Partial<Record<ActivityItem['kind'], string>> = {
  message_sent: '✉',
  message_queued: '⇢',
  action_auto_approved: '⚙',
  action_approved: '✓',
  action_rejected: '✕',
  escalation_opened: '!',
  escalation_resolved: '✓',
  agent_run_started: '◇',
  agent_run_finished: '◆',
  briefing_published: '☰',
  autonomy_paused: '❚❚',
  autonomy_resumed: '▶',
};

export function ActivityPage() {
  const feed = useActivityFeed();
  const [group, setGroup] = useState('all');
  const kinds = GROUPS.find((g) => g.id === group)?.kinds ?? null;

  // Newest first; the server sends oldest first.
  const items = useMemo(
    () => (feed.data ? [...feed.data.items].reverse().filter((i) => !kinds || kinds.includes(i.kind)) : []),
    [feed.data, kinds],
  );

  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>Activity</h2>
        <p className="muted">
          Everything the system did, as it happens.{' '}
          <span className="live" role="status">
            <span className="live-dot" aria-hidden="true" />
            Live{feed.dataUpdatedAt ? ` · checked ${new Date(feed.dataUpdatedAt).toLocaleTimeString('en-CA')}` : ''}
          </span>
        </p>
      </div>
      <section className="panel">
        <div className="chips" role="group" aria-label="Filter activity">
          {GROUPS.map((g) => (
            <button
              key={g.id}
              type="button"
              className={g.id === group ? 'chip active' : 'chip'}
              aria-pressed={g.id === group}
              onClick={() => setGroup(g.id)}
            >
              {g.label}
            </button>
          ))}
        </div>
        {feed.isPending && <Skeleton label="Loading activity" rows={6} />}
        {feed.error && <ErrorBox error={feed.error} onRetry={() => void feed.refetch()} />}
        {feed.data && items.length === 0 && (
          <EmptyState title={group === 'all' ? 'Nothing has happened yet' : 'Nothing of this kind yet'}>
            Automatic reminders, agent runs and your own decisions show up here within a few seconds.
          </EmptyState>
        )}
        {items.length > 0 && (
          <ol className="feed" aria-label="Activity feed">
            {items.map((item) => (
              <li key={item.seq} className={`feed-item kind-${item.kind}`}>
                <span className="feed-icon" aria-hidden="true">
                  {ICON[item.kind] ?? '•'}
                </span>
                <div className="feed-body">
                  <span className="feed-summary">{item.summary}</span>
                  <span className="muted small">
                    {formatTime(item.created_at)} · {humanize(item.kind)} · by {item.actor}
                    {item.case_id && (
                      <>
                        {' '}
                        · <Link to={`/cases/${encodeURIComponent(item.case_id)}`}>{item.case_id}</Link>
                      </>
                    )}
                    {item.synthetic && (
                      <>
                        {' '}
                        <SimulatedTag>Synthetic</SimulatedTag>
                      </>
                    )}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
