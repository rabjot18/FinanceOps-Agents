import { useState, type FormEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { keys, refreshAll, useCases, useClock, useScenarios, useWorkspace } from '../api/queries';
import {
  ApiError,
  asApiError,
  isNotBuilt,
  newId,
  type Api,
  type GeneratorRun,
  type Inbox,
  type WorkerRunOut,
} from '../api/client';
import { formatCents, formatTime } from '../format';
import { useSession } from '../session';
import { EmptyState, ErrorBox, NotBuiltNotice, PlainText, SimulatedTag, Skeleton } from '../components/common';

const SPEEDS = [
  { value: 1, label: '1 min / s' },
  { value: 60, label: '1 hour / s' },
  { value: 360, label: '6 hours / s' },
  { value: 1440, label: '1 day / s' },
  { value: 10080, label: '1 week / s' },
];

/** Runs one demo call, refreshes everything, and reports the outcome. */
function useDemoAction() {
  const { client, signOut } = useSession();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<ReactNode>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const act = async <T,>(call: (c: Api) => Promise<T>, done: (result: T) => ReactNode): Promise<T | null> => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await call(client);
      setMessage(done(result));
      await refreshAll(qc);
      return result;
    } catch (err) {
      const e = asApiError(err);
      if (e.kind === 'unauthorized') signOut('The server rejected the owner token. Enter it again.');
      else setError(e);
      if (e.kind === 'conflict') await refreshAll(qc);
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { act, busy, message, error, setError };
}

/**
 * Synthetic events for the demo. Nothing here touches a real customer, inbox
 * or bank: the server's /demo routes exist only with FINANCEOPS_ENABLE_DEMO=1.
 */
export function DemoLabPage() {
  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>
          Demo Lab <SimulatedTag>Synthetic data · simulated</SimulatedTag>
        </h2>
        <p className="muted">These controls create synthetic events. Nothing reaches a real customer, inbox or bank.</p>
      </div>
      <div className="lab-grid">
        <div className="lab-col">
          <ClockPanel />
          <GeneratorPanel />
          <ScenarioPanel />
        </div>
        <div className="lab-col">
          <EventsPanel />
          <WorkerPanel />
          <InboxPanel />
        </div>
      </div>
    </div>
  );
}

function Outcome({ message, error }: { message: ReactNode; error: ApiError | null }) {
  return (
    <>
      {message && (
        <p role="status" className="success">
          {message}
        </p>
      )}
      {error && <ErrorBox error={error} />}
    </>
  );
}

// --- clock -------------------------------------------------------------------

function ClockPanel() {
  const clock = useClock(true);
  const qc = useQueryClient();
  const { act, busy, error } = useDemoAction();

  const set = (body: Parameters<Api['setClock']>[0]) =>
    act(
      (c) => c.setClock(body),
      (out) => {
        qc.setQueryData(keys.clock, out);
        return null;
      },
    );

  return (
    <section className="panel" aria-labelledby="clock-title">
      <div className="panel-head">
        <h3 id="clock-title">Simulated clock</h3>
        <SimulatedTag>Simulated time</SimulatedTag>
      </div>
      {clock.isPending && <Skeleton label="Loading clock" rows={2} />}
      {clock.error &&
        (clock.error.kind === 'conflict' ? (
          <p className="muted">{clock.error.detail}</p>
        ) : (
          <ErrorBox error={clock.error} onRetry={() => void clock.refetch()} />
        ))}
      {clock.data && (
        <>
          <p className="clock-now">
            <span className="clock-time">{formatTime(clock.data.now)}</span>
            <span className={clock.data.playing ? 'badge state-resolved' : 'badge state-open'}>
              {clock.data.playing ? 'playing' : 'paused'}
            </span>
          </p>
          <p className="muted small">
            Speed: {clock.data.speed} simulated minute{clock.data.speed === 1 ? '' : 's'} per real second · offset{' '}
            {Math.round(clock.data.offset_seconds / 60)} min. While playing, the worker loop moves the clock.
          </p>
          <div className="row-buttons">
            {clock.data.playing ? (
              <button type="button" className="secondary" disabled={busy} onClick={() => void set({ action: 'pause' })}>
                ❚❚ Pause
              </button>
            ) : (
              <button type="button" disabled={busy} onClick={() => void set({ action: 'play' })}>
                ▶ Play
              </button>
            )}
            <label htmlFor="clock-speed" className="inline-label">
              Speed
            </label>
            <select
              id="clock-speed"
              className="narrow"
              value={SPEEDS.some((s) => s.value === clock.data.speed) ? clock.data.speed : ''}
              disabled={busy}
              onChange={(e) => void set({ action: 'speed', speed: Number(e.target.value) })}
            >
              {!SPEEDS.some((s) => s.value === clock.data.speed) && <option value="">{clock.data.speed} min / s</option>}
              {SPEEDS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </>
      )}
      {error && <ErrorBox error={error} />}
    </section>
  );
}

// --- generator -----------------------------------------------------------------

function GeneratorPanel() {
  const { act, busy, error, setError } = useDemoAction();
  const [seed, setSeed] = useState('42');
  const [months, setMonths] = useState('6');
  const [customers, setCustomers] = useState('25');
  const [result, setResult] = useState<GeneratorRun | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const n = [seed, months, customers].map((v) => (/^\d+$/.test(v.trim()) ? Number(v) : NaN));
    if (n.some(Number.isNaN)) {
      setError(new ApiError('invalid', null, 'Seed, months and customers must be whole numbers.'));
      return;
    }
    void act(
      (c) => c.runGenerator({ seed: n[0] as number, months: n[1] as number, customers: n[2] as number }),
      (out) => {
        setResult(out);
        return `Generated ${out.customers} customers and ${out.invoices} invoices; ${out.events_scheduled} events scheduled.`;
      },
    );
  };

  return (
    <section className="panel" aria-labelledby="gen-title">
      <div className="panel-head">
        <h3 id="gen-title">Generate a business</h3>
      </div>
      <p className="panel-note">A fictional business with a timeline of invoices, payments and replies. Same seed, same data.</p>
      <form onSubmit={submit} className="form-grid three">
        <div>
          <label htmlFor="gen-seed">Seed</label>
          <input id="gen-seed" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)} />
        </div>
        <div>
          <label htmlFor="gen-months">Months (1–12)</label>
          <input id="gen-months" inputMode="numeric" value={months} onChange={(e) => setMonths(e.target.value)} />
        </div>
        <div>
          <label htmlFor="gen-customers">Customers (1–200)</label>
          <input id="gen-customers" inputMode="numeric" value={customers} onChange={(e) => setCustomers(e.target.value)} />
        </div>
        <div className="span-all">
          <button type="submit" disabled={busy}>
            Generate business
          </button>
        </div>
      </form>
      {error && isNotBuilt(error) ? <NotBuiltNotice what="The business generator" error={error} /> : error && <ErrorBox error={error} />}
      {result && (
        <p role="status" className="success">
          Seed {result.seed}: {result.customers} customers, {result.invoices} invoices, {result.events_scheduled} events
          from {formatTime(result.first_event_at)} to {formatTime(result.last_event_at)}. Press Play to run it.
        </p>
      )}
    </section>
  );
}

// --- scenarios -------------------------------------------------------------------

function ScenarioPanel() {
  const scenarios = useScenarios();
  const { act, busy, message, error } = useDemoAction();
  return (
    <section className="panel" aria-labelledby="scn-title">
      <div className="panel-head">
        <h3 id="scn-title">Load a scenario</h3>
      </div>
      {scenarios.isPending && <Skeleton label="Loading scenarios" rows={3} />}
      {scenarios.error && <ErrorBox error={scenarios.error} onRetry={() => void scenarios.refetch()} />}
      {scenarios.data?.scenarios.map((s) => (
        <div key={s.name} className="scenario">
          <div>
            <strong>{s.title}</strong> <code>{s.name}</code>
            <p className="muted">{s.description}</p>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(
                (c) => c.loadScenario(s.name),
                (out) => (
                  <>
                    Loaded scenario “{out.scenario}”: {out.case_ids.length} case(s).{' '}
                    {out.case_ids.map((id, i) => (
                      <span key={id}>
                        {i > 0 && ', '}
                        <Link to={`/cases/${encodeURIComponent(id)}`}>Open {id}</Link>
                      </span>
                    ))}
                  </>
                ),
              )
            }
          >
            Load {s.name}
          </button>
        </div>
      ))}
      <Outcome message={message} error={error} />
    </section>
  );
}

// --- synthetic events on one case ---------------------------------------------------

function EventsPanel() {
  const cases = useCases({ sort: 'updated', limit: 200 });
  const [picked, setPicked] = useState<string>('');
  const caseId = picked || cases.data?.cases[0]?.id || null;
  const ws = useWorkspace(caseId);
  const { act, busy, message, error, setError } = useDemoAction();
  const [amount, setAmount] = useState('');
  const [replyText, setReplyText] = useState('');
  const invoice = ws.data?.invoice;

  const settle = (event: FormEvent) => {
    event.preventDefault();
    if (!invoice) return;
    const cents = Number(amount);
    if (!/^\d+$/.test(amount.trim()) || cents <= 0) {
      setError(new ApiError('invalid', null, 'Enter a whole number of cents greater than zero.'));
      return;
    }
    void act(
      (c) =>
        c.settle({
          invoice_id: invoice.id,
          settlement_id: newId('stl'),
          amount_cents: cents,
          currency: 'CAD', // the contract accepts CAD settlements only
        }),
      (inv) => `Synthetic settlement recorded. Outstanding is now ${formatCents(inv.outstanding_cents, inv.currency)}.`,
    ).then((r) => r && setAmount(''));
  };

  const reply = (event: FormEvent) => {
    event.preventDefault();
    if (!invoice || !replyText.trim()) return;
    void act(
      (c) => c.reply(invoice.id, newId('reply'), replyText),
      () => 'Synthetic customer reply recorded as untrusted evidence.',
    ).then((r) => r && setReplyText(''));
  };

  return (
    <section className="panel" aria-labelledby="events-title">
      <div className="panel-head">
        <h3 id="events-title">Synthetic events</h3>
      </div>
      {cases.isPending && <Skeleton label="Loading cases" rows={2} />}
      {cases.error && <ErrorBox error={cases.error} onRetry={() => void cases.refetch()} />}
      {cases.data && cases.data.cases.length === 0 && (
        <EmptyState title="No cases yet">Load a scenario or generate a business first.</EmptyState>
      )}
      {cases.data && cases.data.cases.length > 0 && (
        <>
          <label htmlFor="lab-case">Case</label>
          <select id="lab-case" value={caseId ?? ''} onChange={(e) => setPicked(e.target.value)}>
            {cases.data.cases.map((c) => (
              <option key={c.id} value={c.id}>
                {c.customer} · {c.invoice_id}
              </option>
            ))}
          </select>
          {ws.isPending && <Skeleton label="Loading case" rows={2} />}
          {invoice && (
            <>
              <p className="muted small spaced">
                {invoice.id}: outstanding {formatCents(invoice.outstanding_cents, invoice.currency)} ·{' '}
                {invoice.disputed ? 'disputed' : 'not disputed'} ·{' '}
                {caseId && <Link to={`/cases/${encodeURIComponent(caseId)}`}>open case</Link>}
              </p>
              <form onSubmit={settle} className="inline-form">
                <label htmlFor="settle-amount">Settlement amount (cents, {invoice.currency})</label>
                <input
                  id="settle-amount"
                  inputMode="numeric"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  aria-describedby="settle-hint"
                />
                <span id="settle-hint" className="muted small">
                  {amount && /^\d+$/.test(amount) ? `= ${formatCents(Number(amount), invoice.currency)}` : ''}
                </span>
                <div className="row-buttons">
                  <button type="button" className="secondary" onClick={() => setAmount(String(invoice.outstanding_cents))}>
                    Use outstanding ({formatCents(invoice.outstanding_cents, invoice.currency)})
                  </button>
                  <button type="submit" disabled={busy}>
                    Add synthetic settlement
                  </button>
                </div>
              </form>

              <form onSubmit={reply} className="inline-form">
                <label htmlFor="reply-text">Synthetic customer reply</label>
                <textarea id="reply-text" rows={2} value={replyText} onChange={(e) => setReplyText(e.target.value)} />
                <button type="submit" disabled={busy || !replyText.trim()}>
                  Add reply
                </button>
              </form>

              <div className="inline-form">
                <span>Dispute: currently {invoice.disputed ? 'disputed' : 'not disputed'}</span>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    void act(
                      (c) => c.dispute(invoice.id, !invoice.disputed),
                      (inv) => (inv.disputed ? 'Invoice marked disputed (synthetic).' : 'Dispute cleared (synthetic).'),
                    )
                  }
                >
                  {invoice.disputed ? 'Clear dispute' : 'Mark disputed'}
                </button>
              </div>
            </>
          )}
        </>
      )}
      <Outcome message={message} error={error} />
    </section>
  );
}

// --- worker --------------------------------------------------------------------------

function WorkerPanel() {
  const { act, busy, message, error, setError } = useDemoAction();
  const [advance, setAdvance] = useState('0');
  const [worker, setWorker] = useState<WorkerRunOut | null>(null);

  const runWorker = (event: FormEvent) => {
    event.preventDefault();
    const text = advance.trim() || '0';
    if (!/^\d+$/.test(text)) {
      setError(new ApiError('invalid', null, 'Advance minutes must be a whole number, 0 or more.'));
      return;
    }
    void act(
      (c) => c.runWorker(Number(text)),
      (out) => {
        setWorker(out);
        return `Worker round finished (simulated time ${formatTime(out.simulated_time)})`;
      },
    );
  };

  return (
    <section className="panel" aria-labelledby="worker-title">
      <div className="panel-head">
        <h3 id="worker-title">Worker</h3>
      </div>
      <form onSubmit={runWorker} className="inline-form">
        <label htmlFor="advance-minutes">Advance simulated clock (minutes, optional)</label>
        <input id="advance-minutes" inputMode="numeric" value={advance} onChange={(e) => setAdvance(e.target.value)} />
        <button type="submit" disabled={busy}>
          Run worker once
        </button>
      </form>
      <p className="muted small">AI re-investigations are off here (they would call a paid model).</p>
      {worker && (
        <ul className="plain-list" aria-label="Worker result">
          <li>
            Simulated time: {formatTime(worker.simulated_time)} <SimulatedTag />
          </li>
          <li>Sent to simulated inbox: {worker.result.sent.join(', ') || 'none'}</li>
          <li>Failed: {worker.result.failed.join(', ') || 'none'}</li>
          <li>Unknown: {worker.result.unknown.join(', ') || 'none'}</li>
          <li>Cancelled: {worker.result.cancelled.join(', ') || 'none'}</li>
          <li>Follow-ups elapsed: {worker.result.follow_ups_elapsed.join(', ') || 'none'}</li>
          <li>Investigations waiting: {worker.result.investigations_waiting}</li>
        </ul>
      )}
      <Outcome message={message} error={error} />
    </section>
  );
}

// --- inbox ---------------------------------------------------------------------------

function InboxPanel() {
  const { client, signOut } = useSession();
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setError(null);
    setBusy(true);
    try {
      setInbox(await client.inbox());
    } catch (err) {
      const e = asApiError(err);
      if (e.kind === 'unauthorized') signOut('The server rejected the owner token. Enter it again.');
      else setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" aria-labelledby="inbox-title">
      <div className="panel-head">
        <h3 id="inbox-title">Simulated inbox</h3>
        <button type="button" className="secondary" disabled={busy} onClick={() => void load()}>
          {inbox ? 'Refresh simulated inbox' : 'Show simulated inbox'}
        </button>
      </div>
      {error && <ErrorBox error={error} />}
      {inbox && (
        <div aria-label="Simulated inbox messages">
          <p>
            <SimulatedTag>Simulated inbox — no real email was sent</SimulatedTag>
          </p>
          {inbox.messages.length === 0 && <EmptyState title="No messages">Run the worker to deliver queued messages.</EmptyState>}
          {inbox.messages.map((m) => (
            <article key={m.external_id} className="inbox-message">
              <h4>{m.subject}</h4>
              <p className="muted">
                To {m.recipient} · {formatTime(m.received_at)} · {m.external_id}
              </p>
              <PlainText text={m.body} />
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
