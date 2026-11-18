import { Link } from 'react-router-dom';
import { useAttention, useBriefing, useSummary } from '../api/queries';
import type { Summary } from '../api/client';
import { formatCents, formatTime } from '../format';
import { ApprovalQueue } from '../components/ApprovalQueue';
import { EscalationList } from '../components/Escalations';
import { EmptyState, ErrorBox, SimulatedTag, Skeleton } from '../components/common';

export function HomePage() {
  const attention = useAttention();
  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>Needs you</h2>
        <p className="muted">What is waiting for your decision. Everything else is handled by policy or the agents.</p>
      </div>
      <KpiStrip />
      <div className="home-grid">
        <section className="panel" aria-labelledby="queue-title">
          <div className="panel-head">
            <h3 id="queue-title">Approval queue</h3>
            {attention.data && <span className="count">{attention.data.approvals.length}</span>}
          </div>
          <p className="panel-note">
            Keyboard: <kbd>j</kbd>/<kbd>k</kbd> move, <kbd>a</kbd> approve, <kbd>r</kbd> reject (each asks to confirm).
          </p>
          {attention.isPending && <Skeleton label="Loading approval queue" rows={4} />}
          {attention.error && <ErrorBox error={attention.error} onRetry={() => void attention.refetch()} />}
          {attention.data && <ApprovalQueue approvals={attention.data.approvals} />}
        </section>
        <div className="home-side">
          <section className="panel" aria-labelledby="esc-title">
            <div className="panel-head">
              <h3 id="esc-title">Escalations</h3>
              {attention.data && <span className="count">{attention.data.escalations.length}</span>}
            </div>
            {attention.isPending && <Skeleton label="Loading escalations" />}
            {attention.data && attention.data.escalations.length === 0 && (
              <EmptyState title="No open escalations">When an agent needs a decision only you can make, it asks here.</EmptyState>
            )}
            {attention.data && attention.data.escalations.length > 0 && (
              <EscalationList items={attention.data.escalations} showCase />
            )}
          </section>
          <BriefingPanel />
        </div>
      </div>
    </div>
  );
}

// --- KPIs ------------------------------------------------------------------

function KpiStrip() {
  const summary = useSummary();
  if (summary.isPending) return <Skeleton label="Loading headline numbers" rows={2} />;
  if (summary.error) return <ErrorBox error={summary.error} onRetry={() => void summary.refetch()} />;
  return <Kpis s={summary.data} />;
}

function Kpis({ s }: { s: Summary }) {
  return (
    <section aria-label="Headline numbers" className="kpis">
      <div className="kpi">
        <span className="kpi-label">Outstanding</span>
        <span className="kpi-value">{formatCents(s.outstanding_cents, s.currency)}</span>
        <span className="kpi-sub">{s.open_cases} open cases</span>
      </div>
      <div className="kpi">
        <span className="kpi-label">Overdue</span>
        <span className="kpi-value warn">{formatCents(s.overdue_cents, s.currency)}</span>
        <span className="kpi-sub">{s.overdue_cases} cases overdue</span>
      </div>
      <div className="kpi">
        <span className="kpi-label">Auto-handled (7 days)</span>
        <span className="kpi-value">{s.auto_handled_7d}</span>
        <span className="kpi-sub">{s.resolved_7d} resolved this week</span>
      </div>
      <div className="kpi">
        <span className="kpi-label">Awaiting you</span>
        <span className={s.awaiting_owner > 0 ? 'kpi-value attention' : 'kpi-value'}>{s.awaiting_owner}</span>
        <span className="kpi-sub">approvals and escalations</span>
      </div>
      <div className={s.autonomy_paused ? 'kpi paused' : 'kpi'}>
        <span className="kpi-label">Autonomy</span>
        <span className="kpi-value">{s.autonomy_paused ? 'Paused' : 'On'}</span>
        <span className="kpi-sub">
          {s.model_spend_today_cents !== null
            ? `Model spend today ${formatCents(s.model_spend_today_cents, s.currency)}`
            : formatTime(s.now)}
          {s.simulated_time && <> · <SimulatedTag>Simulated clock</SimulatedTag></>}
        </span>
      </div>
    </section>
  );
}

// --- briefing ----------------------------------------------------------------

function BriefingPanel() {
  const briefing = useBriefing();
  return (
    <section className="panel" aria-labelledby="brief-title">
      <div className="panel-head">
        <h3 id="brief-title">Latest briefing</h3>
        {briefing.data && <span className="muted small">{formatTime(briefing.data.created_at)}</span>}
      </div>
      {briefing.isPending && <Skeleton label="Loading briefing" />}
      {briefing.error && <ErrorBox error={briefing.error} onRetry={() => void briefing.refetch()} />}
      {briefing.data === null && (
        <EmptyState title="No briefing yet">The briefing agent writes a short summary here once it has run.</EmptyState>
      )}
      {briefing.data && (
        <div className="briefing">
          <p className="briefing-headline">{briefing.data.headline}</p>
          <p className="muted small">
            {formatTime(briefing.data.period_start)} – {formatTime(briefing.data.period_end)} · model text, untrusted
          </p>
          {briefing.data.sections.map((section, i) => (
            <div key={i} className="briefing-section">
              <h4>{section.title}</h4>
              <ul>
                {section.items.map((item, j) => (
                  <li key={j}>
                    <span className="plain-inline">{item.text}</span>
                    {item.case_ids.length > 0 && (
                      <span className="muted small">
                        {' '}
                        ·{' '}
                        {item.case_ids.map((id, k) => (
                          <span key={id}>
                            {k > 0 && ', '}
                            <Link to={`/cases/${encodeURIComponent(id)}`}>{id}</Link>
                          </span>
                        ))}
                      </span>
                    )}
                    {item.evidence_ids.length > 0 && (
                      <span className="muted small"> · evidence {item.evidence_ids.join(', ')}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
