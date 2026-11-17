import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { ApiError, newId, type Api, type Inbox, type ScenarioList, type WorkerRunOut } from '../api/client';
import { formatCents, formatTime } from '../format';
import { ErrorBox, PlainText, SimulatedTag } from './common';

/** What the lab needs about the open case; all values come from the backend. */
export interface OpenCase {
  caseId: string;
  invoiceId: string;
  outstandingCents: number;
  disputed: boolean;
}

interface Props {
  client: Api;
  openCase: OpenCase | null;
  onChanged: () => void;
  onLoaded: (caseIds: string[]) => void;
  onAuthError: (error: ApiError) => boolean;
}

function asApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError('server', null, String(err));
}

/**
 * Synthetic events for the demo. Nothing here touches a real customer, inbox
 * or bank: the server's /demo routes exist only with FINANCEOPS_ENABLE_DEMO=1.
 */
export function DemoLab({ client, openCase, onChanged, onLoaded, onAuthError }: Props) {
  const [scenarios, setScenarios] = useState<ScenarioList | null>(null);
  const [scenarioError, setScenarioError] = useState<ApiError | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState('');
  const [replyText, setReplyText] = useState('');
  const [advance, setAdvance] = useState('0');
  const [worker, setWorker] = useState<WorkerRunOut | null>(null);
  const [inbox, setInbox] = useState<Inbox | null>(null);

  const loadScenarios = async () => {
    setScenarioError(null);
    try {
      setScenarios(await client.scenarios());
    } catch (err) {
      const e = asApiError(err);
      if (!onAuthError(e)) setScenarioError(e);
    }
  };

  useEffect(() => {
    void loadScenarios();
    // load once per client (token)
  }, [client]);

  /** Runs one demo call; on success shows `done` and refetches everything. */
  const act = async <T,>(call: () => Promise<T>, done: (result: T) => string) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await call();
      setMessage(done(result));
      onChanged();
      return result;
    } catch (err) {
      const e = asApiError(err);
      if (!onAuthError(e)) setError(e);
      if (e.kind === 'conflict') onChanged();
      return null;
    } finally {
      setBusy(false);
    }
  };

  const settle = (event: FormEvent) => {
    event.preventDefault();
    if (!openCase) return;
    const cents = Number(amount);
    if (!Number.isInteger(cents) || cents <= 0) {
      setError(new ApiError('invalid', null, 'Enter a whole number of cents greater than zero.'));
      return;
    }
    void act(
      () =>
        client.settle({
          invoice_id: openCase.invoiceId,
          settlement_id: newId('stl'),
          amount_cents: cents,
          currency: 'CAD',
        }),
      (inv) => `Synthetic settlement recorded. Outstanding is now ${formatCents(inv.outstanding_cents, 'CAD')}.`,
    ).then((r) => r && setAmount(''));
  };

  const reply = (event: FormEvent) => {
    event.preventDefault();
    if (!openCase || !replyText.trim()) return;
    void act(
      () => client.reply(openCase.invoiceId, newId('reply'), replyText),
      () => 'Synthetic customer reply recorded as untrusted evidence.',
    ).then((r) => r && setReplyText(''));
  };

  const runWorker = (event: FormEvent) => {
    event.preventDefault();
    const minutes = advance.trim() === '' ? 0 : Number(advance);
    if (!Number.isInteger(minutes) || minutes < 0) {
      setError(new ApiError('invalid', null, 'Advance minutes must be a whole number, 0 or more.'));
      return;
    }
    void act(
      () => client.runWorker(minutes),
      (out) => {
        setWorker(out);
        return `Worker round finished (simulated time ${formatTime(out.simulated_time)})`;
      },
    );
  };

  const loadInbox = async () => {
    setError(null);
    try {
      setInbox(await client.inbox());
    } catch (err) {
      const e = asApiError(err);
      if (!onAuthError(e)) setError(e);
    }
  };

  return (
    <section className="panel demo-lab" aria-labelledby="lab-title">
      <h2 id="lab-title">
        Demo lab <SimulatedTag>Synthetic data · simulated</SimulatedTag>
      </h2>
      <p className="muted">
        These buttons create synthetic events. Nothing reaches a real customer, inbox or bank.
      </p>

      <Block title="Load a scenario">
        {scenarioError && <ErrorBox error={scenarioError} onRetry={() => void loadScenarios()} />}
        {!scenarios && !scenarioError && <p role="status">Loading scenarios…</p>}
        {scenarios?.scenarios.map((s) => (
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
                  () => client.loadScenario(s.name),
                  (out) => {
                    onLoaded(out.case_ids);
                    return `Loaded scenario “${out.scenario}”: ${out.case_ids.length} case(s).`;
                  },
                )
              }
            >
              Load {s.name}
            </button>
          </div>
        ))}
      </Block>

      {openCase ? (
        <>
          <Block title={`Synthetic events for ${openCase.invoiceId}`}>
            <form onSubmit={settle} className="inline-form">
              <label htmlFor="settle-amount">Settlement amount (cents, CAD)</label>
              <input
                id="settle-amount"
                inputMode="numeric"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                aria-describedby="settle-hint"
              />
              <span id="settle-hint" className="muted">
                {amount && Number.isInteger(Number(amount)) ? `= ${formatCents(Number(amount), 'CAD')}` : ''}
              </span>
              <button
                type="button"
                className="secondary"
                onClick={() => setAmount(String(openCase.outstandingCents))}
              >
                Use outstanding ({formatCents(openCase.outstandingCents, 'CAD')})
              </button>
              <button type="submit" disabled={busy}>
                Add synthetic settlement
              </button>
            </form>

            <form onSubmit={reply} className="inline-form">
              <label htmlFor="reply-text">Synthetic customer reply</label>
              <textarea id="reply-text" rows={2} value={replyText} onChange={(e) => setReplyText(e.target.value)} />
              <button type="submit" disabled={busy || !replyText.trim()}>
                Add reply
              </button>
            </form>

            <div className="inline-form">
              <span>Dispute: currently {openCase.disputed ? 'disputed' : 'not disputed'}</span>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void act(
                    () => client.dispute(openCase.invoiceId, !openCase.disputed),
                    (inv) => (inv.disputed ? 'Invoice marked disputed (synthetic).' : 'Dispute cleared (synthetic).'),
                  )
                }
              >
                {openCase.disputed ? 'Clear dispute' : 'Mark disputed'}
              </button>
            </div>
          </Block>
        </>
      ) : (
        <p className="muted">Open a case to add settlements, replies or disputes.</p>
      )}

      <Block title="Worker">
        <form onSubmit={runWorker} className="inline-form">
          <label htmlFor="advance-minutes">Advance simulated clock (minutes, optional)</label>
          <input
            id="advance-minutes"
            inputMode="numeric"
            value={advance}
            onChange={(e) => setAdvance(e.target.value)}
          />
          <button type="submit" disabled={busy}>
            Run worker once
          </button>
        </form>
        <p className="muted">AI re-investigations are off here (they would call a paid model).</p>
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
      </Block>

      <Block title="Simulated inbox">
        <button type="button" className="secondary" onClick={() => void loadInbox()}>
          {inbox ? 'Refresh simulated inbox' : 'Show simulated inbox'}
        </button>
        {inbox && (
          <div aria-label="Simulated inbox messages">
            <p>
              <SimulatedTag>Simulated inbox — no real email was sent</SimulatedTag>
            </p>
            {inbox.messages.length === 0 && <p>No messages.</p>}
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
      </Block>

      {message && (
        <p role="status" className="success">
          {message}
        </p>
      )}
      {error && <ErrorBox error={error} />}
    </section>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="lab-block">
      <h3>{title}</h3>
      {children}
    </div>
  );
}
