// Outcomes: what the run achieved, as computed by the backend (/outcomes).
// Nothing here is added up or judged in the browser.
import { Link } from 'react-router-dom';
import { useOutcomes } from '../api/queries';
import { isNotBuilt, type OutcomesOut, type PersonaResult } from '../api/client';
import { formatCents, formatTime, formatUsdCents } from '../format';
import { STAGE_LABEL, STAGE_TONE } from '../flowMeta';
import { useRunMonth } from '../components/RunMonth';
import { ErrorBox, NotBuiltNotice, SimulatedTag, Skeleton } from '../components/common';

export function OutcomesPage() {
  const outcomes = useOutcomes();
  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>Outcomes</h2>
        <p className="muted">What the agents achieved this run. You come back to results, not to-do lists.</p>
      </div>
      {outcomes.isPending && <Skeleton label="Loading outcomes" rows={5} />}
      {outcomes.error &&
        (isNotBuilt(outcomes.error) ? (
          <NotBuiltNotice what="Outcomes" error={outcomes.error} />
        ) : (
          <ErrorBox error={outcomes.error} onRetry={() => void outcomes.refetch()} />
        ))}
      {outcomes.data &&
        (outcomes.data.cases_total === 0 ? <NoOutcomes /> : <Results o={outcomes.data} />)}
    </div>
  );
}

function NoOutcomes() {
  const runMonth = useRunMonth();
  return (
    <div className="hero-empty">
      <strong>No results yet</strong>
      <p className="muted">Run a synthetic month and the results land here: money collected, cases closed, and how often the agents needed you.</p>
      <button type="button" className="run-month big" onClick={runMonth}>
        <span aria-hidden="true">▶</span> Run the month
      </button>
    </div>
  );
}

/** A bar for a ratio the backend already reports both sides of (display only). */
function Meter({ value, max, tone = 'money' }: { value: number; max: number; tone?: 'money' | 'accent' | 'warn' }) {
  const share = max > 0 ? Math.min(1, value / max) : 0;
  return (
    <span className={`meter meter-${tone}`} aria-hidden="true">
      <span className="meter-fill" style={{ width: `${Math.round(share * 100)}%` }} />
    </span>
  );
}

function Results({ o }: { o: OutcomesOut }) {
  const unauthorized = o.unauthorized_actions;
  return (
    <>
      <p className="muted small outcomes-meta">
        As of {formatTime(o.now)} {o.simulated_time && <SimulatedTag>Simulated clock</SimulatedTag>}
      </p>

      <section aria-label="Results" className="outcome-tiles">
        <div className="tile tile-hero">
          <span className="tile-label">Collected</span>
          <span className="tile-value money">{formatCents(o.collected_cents, o.currency)}</span>
          <span className="tile-sub">of {formatCents(o.invoiced_cents, o.currency)} invoiced</span>
          <Meter value={o.collected_cents} max={o.invoiced_cents} />
        </div>
        <div className="tile">
          <span className="tile-label">Cases closed</span>
          <span className="tile-value">{o.cases_closed}</span>
          <span className="tile-sub">of {o.cases_total} cases</span>
        </div>
        <div className="tile">
          <span className="tile-label">Closed without you</span>
          <span className="tile-value">{o.closed_without_owner}</span>
          <span className="tile-sub">handled end to end by the agents</span>
        </div>
        <div className="tile">
          <span className="tile-label">Your decisions</span>
          <span className="tile-value">{o.owner_decisions}</span>
          <span className="tile-sub">
            {o.open_for_owner > 0 ? (
              <Link to="/decisions">{o.open_for_owner} waiting for you</Link>
            ) : (
              'nothing waiting'
            )}
          </span>
        </div>
        <div className="tile">
          <span className="tile-label">Avg days to pay</span>
          <span className="tile-value">{o.avg_days_to_pay === null ? '—' : o.avg_days_to_pay.toFixed(1)}</span>
          <span className="tile-sub">{formatCents(o.outstanding_cents, o.currency)} still outstanding</span>
        </div>
        <div className={unauthorized > 0 ? 'tile tile-safety bad' : 'tile tile-safety'}>
          <span className="safety-mark" aria-hidden="true">
            {unauthorized > 0 ? '!' : '✓'}
          </span>
          <span className="tile-value">
            {unauthorized} unauthorized action{unauthorized === 1 ? '' : 's'}
          </span>
          <span className="tile-sub">
            {unauthorized > 0 ? 'Something ran without a policy rule or your approval.' : 'Every action had a policy rule or your approval.'}
          </span>
        </div>
      </section>

      <section aria-label="Agent work" className="work-row">
        <div>
          <span className="work-n">{o.messages_sent}</span> messages sent
        </div>
        <div>
          <span className="work-n">{o.messages_written_by_agents}</span> written by agents
        </div>
        <div>
          <span className="work-n">{o.followups_scheduled}</span> follow-ups scheduled
        </div>
        <div>
          <span className="work-n">{o.agent_runs}</span> agent runs
        </div>
      </section>

      <div className="outcomes-grid">
        <section className="panel" aria-labelledby="spend-title">
          <div className="panel-head">
            <h3 id="spend-title">Model spend</h3>
            <span className="muted small">US dollars</span>
          </div>
          <div className="spend-rows">
            <div>
              <div className="spend-row-text">
                <span>Today</span>
                <span>
                  <strong>{formatUsdCents(o.spend.real_today_cents)}</strong> of {formatUsdCents(o.spend.daily_cap_cents, true)}
                </span>
              </div>
              <Meter value={o.spend.real_today_cents} max={o.spend.daily_cap_cents} tone="accent" />
            </div>
            <div>
              <div className="spend-row-text">
                <span>Total</span>
                <span>
                  <strong>{formatUsdCents(o.spend.total_cents)}</strong> of {formatUsdCents(o.spend.total_cap_cents, true)}
                </span>
              </div>
              <Meter value={o.spend.total_cents} max={o.spend.total_cap_cents} tone="accent" />
            </div>
          </div>
          {!o.spend.model_calls_allowed && (
            <p className="warning-note spaced">Cap reached — agents paused, decisions come to you.</p>
          )}
        </section>

        <section className="panel" aria-labelledby="narrative-title">
          <div className="panel-head">
            <h3 id="narrative-title">The reporter&apos;s summary</h3>
            <span className="muted small">Haiku · model text</span>
          </div>
          {o.narrative ? (
            <div className="briefing">
              <p className="briefing-headline plain-inline">{o.narrative.headline}</p>
              {o.narrative.sections.map((section, i) => (
                <div key={i} className="briefing-section">
                  <h4>{section.title}</h4>
                  <ul>
                    {section.items.map((item, j) => (
                      <li key={j}>
                        <span className="plain-inline">{item.text}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">The reporter writes a short summary once the run has something to report.</p>
          )}
        </section>
      </div>

      <PersonaTable personas={o.personas} />
    </>
  );
}

function Verdict({ correct }: { correct: boolean | null }) {
  if (correct === null) return <span className="verdict pending">in progress</span>;
  return correct ? (
    <span className="verdict ok" aria-label="correct">
      ✓
    </span>
  ) : (
    <span className="verdict bad" aria-label="not correct">
      ✗
    </span>
  );
}

function PersonaTable({ personas }: { personas: PersonaResult[] }) {
  return (
    <section className="panel" aria-labelledby="personas-title">
      <div className="panel-head">
        <h3 id="personas-title">Per-customer results</h3>
        <span className="muted small">Hidden personas are revealed only here</span>
      </div>
      <div className="table-wrap">
        <table className="cases-table persona-table" aria-label="Per-customer results">
          <thead>
            <tr>
              <th>Customer</th>
              <th>Hidden persona</th>
              <th>Expected handling</th>
              <th>Stage</th>
              <th>Outcome</th>
              <th>Correct</th>
            </tr>
          </thead>
          <tbody>
            {personas.map((p) => (
              <tr key={p.case_id}>
                <td>
                  <Link to={`/cases/${encodeURIComponent(p.case_id)}`}>{p.customer}</Link>
                  <div className="muted small">{p.invoice_id}</div>
                </td>
                <td className="plain-inline">{p.persona}</td>
                <td className="plain-inline">{p.expected}</td>
                <td>
                  <span className={`stage-pill tone-${STAGE_TONE[p.stage]}`}>{STAGE_LABEL[p.stage]}</span>
                </td>
                <td className="plain-inline">{p.outcome}</td>
                <td>
                  <Verdict correct={p.correct} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
