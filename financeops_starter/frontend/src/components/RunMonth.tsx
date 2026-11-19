// "Run the month": one click starts a synthetic month (customers, invoices,
// the simulated clock playing) through POST /demo/run. Opened from the top bar
// and from the empty Flow page.
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChange } from '../api/queries';
import type { DemoRunIn } from '../api/client';
import { ConfirmDialog } from './common';
import { useToast } from './toast';

export const SPEEDS = [
  { value: 144, label: '1 day / 10 s' },
  { value: 360, label: '1 day / 4 s' },
  { value: 720, label: '1 day / 2 s' },
] as const;

const RunMonthContext = createContext<() => void>(() => {});

/** Opens the "Run the month" dialog. */
export function useRunMonth(): () => void {
  return useContext(RunMonthContext);
}

export function RunMonthProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const show = useCallback(() => setOpen(true), []);
  const close = useCallback(() => setOpen(false), []);
  return (
    <RunMonthContext.Provider value={show}>
      {children}
      {open && <RunMonthDialog onClose={close} />}
    </RunMonthContext.Provider>
  );
}

export function RunMonthButton() {
  const open = useRunMonth();
  return (
    <button type="button" className="run-month" onClick={open}>
      <span aria-hidden="true">▶</span> Run the month
    </button>
  );
}

function whole(text: string): number | null {
  return /^\d+$/.test(text.trim()) ? Number(text.trim()) : null;
}

function RunMonthDialog({ onClose }: { onClose: () => void }) {
  const { run, busy } = useChange();
  const push = useToast();
  const navigate = useNavigate();
  const [seed, setSeed] = useState('42');
  const [customers, setCustomers] = useState('10');
  const [speed, setSpeed] = useState<number>(SPEEDS[0].value);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    const seedN = whole(seed);
    const customersN = whole(customers);
    if (seedN === null) return setError('The seed is a whole number, e.g. 42.');
    if (customersN === null || customersN < 1 || customersN > 30) return setError('Choose between 1 and 30 customers.');
    setError(null);
    const body: DemoRunIn = { seed: seedN, customers: customersN, months: 1, speed };
    const result = await run((c) => c.runDemo(body));
    if (result.ok) {
      const label = SPEEDS.find((s) => s.value === result.data.speed)?.label ?? `${result.data.speed}× speed`;
      push({
        tone: 'success',
        title: 'The month is running',
        body: `${result.data.customers} synthetic customers · ${result.data.invoices} invoices · ${label}`,
      });
      onClose();
      navigate('/');
      return;
    }
    const e = result.error;
    if (e.kind === 'not_found')
      setError('Demo routes are off on this server. Start it with FINANCEOPS_ENABLE_DEMO=1 to run a month.');
    else if (e.kind === 'conflict') setError(`A run is already going. The server said: ${e.detail}`);
    else setError(e.detail);
  };

  return (
    <ConfirmDialog
      title="Run the month"
      confirmLabel={busy ? 'Starting…' : 'Start the month'}
      busy={busy}
      onConfirm={() => void start()}
      onCancel={onClose}
    >
      <p>
        Synthetic customers get a month of invoices and the clock starts playing. The agents take it from there;
        you only see what needs you.
      </p>
      <div className="form-grid three">
        <div>
          <label htmlFor="run-seed">Seed</label>
          <input id="run-seed" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)} />
        </div>
        <div>
          <label htmlFor="run-customers">Customers</label>
          <input
            id="run-customers"
            type="number"
            min={1}
            max={30}
            value={customers}
            onChange={(e) => setCustomers(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="run-speed">Speed</label>
          <select id="run-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="muted small">
        Same seed, same customers. Synthetic data, simulated delivery; model calls stop at your spend cap.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </ConfirmDialog>
  );
}
