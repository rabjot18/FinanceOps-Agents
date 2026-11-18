import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { keys, useActivityFeed, useAttention, useAutonomy, useChange, useSummary } from '../api/queries';
import type { Attention, Summary } from '../api/client';
import { formatCents, formatUsdCents, humanize } from '../format';
import { useSession } from '../session';
import { ConfirmDialog } from './common';
import { RunMonthButton, RunMonthProvider } from './RunMonth';
import { ToastContext, useToast, type PushToast, type Toast } from './toast';

export { useToast };

// --- toasts ----------------------------------------------------------------

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

// --- spend meter -----------------------------------------------------------

/** Model spend (US cents) against the caps, as the backend reports it. */
export function SpendMeter({ s }: { s: Summary }) {
  const capped = !s.model_calls_allowed;
  const share = s.daily_cap_cents > 0 ? Math.min(1, s.spend_real_today_cents / s.daily_cap_cents) : 0; // bar width only
  return (
    <div
      className={capped ? 'spend-meter capped' : 'spend-meter'}
      role="status"
      aria-label="Model spend"
      title="Model spend in US dollars; invoices are in CAD"
    >
      <span className="spend-track" aria-hidden="true">
        <span className="spend-fill" style={{ width: `${capped ? 100 : Math.round(share * 100)}%` }} />
      </span>
      <span className="spend-text">
        {capped ? (
          <strong>Cap reached — agents paused, decisions come to you</strong>
        ) : (
          <>
            <strong>{formatUsdCents(s.spend_real_today_cents)}</strong> today
            {s.daily_cap_cents > 0 && <> of {formatUsdCents(s.daily_cap_cents, true)}</>}
            <span className="spend-sep"> · </span>
            {formatUsdCents(s.spend_total_cents)}
            {s.total_cap_cents > 0 && <> of {formatUsdCents(s.total_cap_cents, true)}</>} total
          </>
        )}
      </span>
    </div>
  );
}

function TopSpend() {
  const summary = useSummary();
  if (!summary.data) return null;
  return <SpendMeter s={summary.data} />;
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

interface NavItem {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
  count?: boolean;
}

// The three things the owner comes back for; the full console sits under "Advanced".
const PRIMARY: NavItem[] = [
  { to: '/', label: 'Flow', icon: '⇢', end: true },
  { to: '/decisions', label: 'Your decisions', icon: '◎', count: true },
  { to: '/outcomes', label: 'Outcomes', icon: '★' },
];

const ADVANCED: NavItem[] = [
  { to: '/home', label: 'Needs you', icon: '◉' },
  { to: '/cases', label: 'Cases', icon: '▤' },
  { to: '/activity', label: 'Activity', icon: '≋' },
  { to: '/agents', label: 'Agents', icon: '◇' },
  { to: '/policy', label: 'Policy', icon: '§' },
  { to: '/demo', label: 'Demo Lab', icon: '⚗' },
];

function isAdvanced(pathname: string): boolean {
  return ADVANCED.some((item) => pathname === item.to || pathname.startsWith(`${item.to}/`));
}

function NavItems({ items }: { items: NavItem[] }) {
  return (
    <ul>
      {items.map((item) => (
        <li key={item.to}>
          <NavLink to={item.to} end={item.end} className="nav-link">
            <span className="nav-icon" aria-hidden="true">
              {item.icon}
            </span>
            <span className="nav-label">{item.label}</span>
            {item.count && <NavCount />}
          </NavLink>
        </li>
      ))}
    </ul>
  );
}

function MainNav() {
  const { pathname } = useLocation();
  const onAdvanced = isAdvanced(pathname);
  const [open, setOpen] = useState(onAdvanced);
  useEffect(() => {
    if (onAdvanced) setOpen(true);
  }, [onAdvanced]);
  return (
    <nav aria-label="Main">
      <NavItems items={PRIMARY} />
      <div className="nav-group">
        <button
          type="button"
          className="nav-group-toggle"
          aria-expanded={open}
          aria-controls="nav-advanced"
          onClick={() => setOpen((v) => !v)}
        >
          <span className="nav-caret" aria-hidden="true">
            {open ? '▾' : '▸'}
          </span>
          Advanced
        </button>
        {open && (
          <div id="nav-advanced">
            <NavItems items={ADVANCED} />
          </div>
        )}
      </div>
    </nav>
  );
}

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
      <RunMonthProvider>
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
            <MainNav />
            <p className="banner-synthetic sidebar-banner">Synthetic data · simulated delivery · no real money moves</p>
          </aside>
          <div className="shell-main">
            <header className="topbar">
              <div className="topbar-left">
                <RunMonthButton />
                <TopSpend />
              </div>
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
      </RunMonthProvider>
    </ToastContext.Provider>
  );
}
