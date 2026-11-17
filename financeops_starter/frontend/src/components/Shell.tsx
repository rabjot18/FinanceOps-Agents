import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { keys, useActivityFeed, useAttention, useAutonomy, useChange, useSummary } from '../api/queries';
import type { Attention } from '../api/client';
import { formatCents, humanize } from '../format';
import { useSession } from '../session';
import { ConfirmDialog } from './common';

// --- toasts ----------------------------------------------------------------

interface Toast {
  id: number;
  title: string;
  body?: string;
  to?: string;
  tone?: 'info' | 'success' | 'attention';
}

type PushToast = (toast: Omit<Toast, 'id'>) => void;
const ToastContext = createContext<PushToast>(() => {});

export function useToast(): PushToast {
  return useContext(ToastContext);
}

const TOAST_MS = 8000;

function Toaster({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: number) => void }) {
  return (
    <section className="toaster" aria-label="Notifications" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone ?? 'info'}`}>
          <div className="toast-text">
            <strong>{t.title}</strong>
            {t.body && <span>{t.body}</span>}
          </div>
          {t.to && (
            <Link to={t.to} className="toast-link" onClick={() => dismiss(t.id)}>
              Open
            </Link>
          )}
          <button type="button" className="icon" aria-label="Dismiss notification" onClick={() => dismiss(t.id)}>
            ×
          </button>
        </div>
      ))}
    </section>
  );
}

// --- live updates ----------------------------------------------------------

/** Everything in the "Needs you" queue by id, to spot what is new. */
function attentionToasts(data: Attention): Map<string, Omit<Toast, 'id'>> {
  const out = new Map<string, Omit<Toast, 'id'>>();
  for (const a of data.approvals) {
    out.set(`action:${a.action.id}`, {
      title: `${a.customer} needs you`,
      body: `${humanize(a.action.kind)} waiting for you · ${formatCents(a.outstanding_cents, a.currency)} outstanding`,
      to: `/cases/${encodeURIComponent(a.case_id)}`,
      tone: 'attention',
    });
  }
  for (const e of data.escalations) {
    out.set(`escalation:${e.escalation.id}`, {
      title: `${e.customer} needs you`,
      body: `Escalation: ${e.escalation.question}`,
      to: `/cases/${encodeURIComponent(e.escalation.case_id)}`,
      tone: 'attention',
    });
  }
  return out;
}

/** Keeps the activity feed polling on every page and toasts new "needs you" items. */
function LiveUpdates({ push }: { push: PushToast }) {
  const qc = useQueryClient();
  useActivityFeed();
  const summary = useSummary();
  const attention = useAttention();
  const seen = useRef<Set<string> | null>(null);
  const awaiting = useRef<number | null>(null);

  // The summary is polled; if its count moves, the queue is out of date.
  const awaitingOwner = summary.data?.awaiting_owner;
  useEffect(() => {
    if (awaitingOwner === undefined) return;
    if (awaiting.current !== null && awaiting.current !== awaitingOwner) {
      void qc.invalidateQueries({ queryKey: keys.attention });
    }
    awaiting.current = awaitingOwner;
  }, [awaitingOwner, qc]);

  useEffect(() => {
    if (!attention.data) return;
    const now = attentionToasts(attention.data);
    if (seen.current) {
      for (const [id, toast] of now) if (!seen.current.has(id)) push(toast);
    }
    seen.current = new Set(now.keys());
  }, [attention.data, push]);

  return null;
}

// --- kill switch -----------------------------------------------------------

function KillSwitch() {
  const autonomy = useAutonomy();
  const { run, busy } = useChange();
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const push = useToast();
  const paused = autonomy.data?.paused;
  const close = useCallback(() => {
    setConfirming(false);
    setError(null);
  }, []);

  if (paused === undefined) return <span className="autonomy-pill unknown">Autonomy …</span>;

  const confirm = async () => {
    const result = await run((c) => (paused ? c.resumeAutonomy() : c.pauseAutonomy(reason.trim() || null)));
    if (result.ok) {
      setConfirming(false);
      setReason('');
      push({ title: paused ? 'Autonomy resumed' : 'Autonomy paused', tone: 'success' });
    } else setError(result.error.detail);
  };

  return (
    <>
      <span className={paused ? 'autonomy-pill paused' : 'autonomy-pill on'}>
        <span className="dot" aria-hidden="true" />
        {paused ? 'Autonomy paused' : 'Autonomy on'}
      </span>
      <button type="button" className={paused ? 'secondary' : 'danger'} onClick={() => setConfirming(true)}>
        {paused ? 'Resume autonomy' : 'Pause autonomy'}
      </button>
      {confirming && (
        <ConfirmDialog
          title={paused ? 'Resume automatic actions?' : 'Pause all automatic actions?'}
          confirmLabel={paused ? 'Yes, resume autonomy' : 'Yes, pause autonomy'}
          tone={paused ? 'primary' : 'danger'}
          busy={busy}
          onConfirm={() => void confirm()}
          onCancel={close}
        >
          {paused ? (
            <p>Actions your policy rules allow will run on their own again. Everything else still waits for you.</p>
          ) : (
            <>
              <p>
                Nothing will be sent or run automatically until you resume. Proposals still wait for your approval as
                usual.
              </p>
              <label htmlFor="pause-reason">Reason (optional)</label>
              <input id="pause-reason" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
            </>
          )}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </ConfirmDialog>
      )}
    </>
  );
}

// --- shell -----------------------------------------------------------------

function NavCount() {
  const attention = useAttention();
  const n = attention.data ? attention.data.approvals.length + attention.data.escalations.length : 0;
  if (!n) return null;
  return (
    <span className="nav-count" aria-label={`${n} waiting`}>
      {n}
    </span>
  );
}

const NAV: { to: string; label: string; icon: string; end?: boolean }[] = [
  { to: '/', label: 'Needs you', icon: '◎', end: true },
  { to: '/cases', label: 'Cases', icon: '▤' },
  { to: '/activity', label: 'Activity', icon: '≋' },
  { to: '/agents', label: 'Agents', icon: '◇' },
  { to: '/policy', label: 'Policy', icon: '§' },
  { to: '/demo', label: 'Demo Lab', icon: '⚗' },
];

interface ShellProps {
  children: ReactNode;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
}

export function Shell({ children, headingRef }: ShellProps) {
  const { signOut } = useSession();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setToasts((all) => all.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const push = useCallback<PushToast>(
    (toast) => {
      const id = nextId.current++;
      setToasts((all) => [...all.slice(-3), { ...toast, id }]);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), TOAST_MS),
      );
    },
    [dismiss],
  );

  useEffect(() => {
    const all = timers.current;
    return () => all.forEach((t) => clearTimeout(t));
  }, []);

  return (
    <ToastContext.Provider value={push}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              FO
            </span>
            <h1 ref={headingRef} tabIndex={-1}>
              FinanceOps <span className="sub">owner console</span>
            </h1>
          </div>
          <nav aria-label="Main">
            <ul>
              {NAV.map((item) => (
                <li key={item.to}>
                  <NavLink to={item.to} end={item.end} className="nav-link">
                    <span className="nav-icon" aria-hidden="true">
                      {item.icon}
                    </span>
                    <span className="nav-label">{item.label}</span>
                    {item.to === '/' && <NavCount />}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </aside>
        <div className="shell-main">
          <header className="topbar">
            <p className="banner-synthetic">Synthetic data · simulated delivery · no real money moves</p>
            <div className="topbar-actions">
              <KillSwitch />
              <button type="button" className="secondary" onClick={() => signOut(null)}>
                Clear token
              </button>
            </div>
          </header>
          <main className="page">{children}</main>
        </div>
        <LiveUpdates push={push} />
        <Toaster toasts={toasts} dismiss={dismiss} />
      </div>
    </ToastContext.Provider>
  );
}
