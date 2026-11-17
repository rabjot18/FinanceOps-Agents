import { useEffect, useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { keys, usePolicy } from '../api/queries';
import { asApiError, isNotBuilt, type ApiError, type AutonomyRuleIn, type Policy, type PolicyIn, type PolicyPreview } from '../api/client';
import { formatCents, formatTime } from '../format';
import { useSession } from '../session';
import { useToast } from '../components/Shell';
import { EmptyState, ErrorBox, NotBuiltNotice, Skeleton } from '../components/common';

// The form edits text; these turn it into the integers the API takes. They
// only parse what the owner typed; the server validates and decides.

/** "5000" or "5000.5" or "5,000.00" -> cents, or null if it is not a plain amount. */
export function parseCents(text: string): number | null {
  const clean = text.replace(/[,\s$]/g, '');
  const match = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(clean);
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'));
}

/** Cents from the server -> the text shown in an amount box. */
function centsText(cents: number | null): string {
  if (cents === null) return '';
  const whole = Math.floor(cents / 100);
  const part = String(cents % 100).padStart(2, '0');
  return part === '00' ? String(whole) : `${whole}.${part}`;
}

interface RuleDraft {
  key: number;
  id: string;
  description: string;
  action_kind: AutonomyRuleIn['action_kind'];
  mode: AutonomyRuleIn['mode'];
  maxAmount: string;
  require_evidence: boolean;
  require_undisputed: boolean;
}

interface Draft {
  ttl: string;
  followUp: string;
  maxReminders: string;
  cadence: string;
  budget: string;
  rules: RuleDraft[];
}

let ruleKey = 1;

function draftFrom(policy: Policy | null): Draft {
  return {
    ttl: String(policy?.approval_ttl_minutes ?? 60),
    followUp: String(policy?.follow_up_days ?? 7),
    maxReminders: String(policy?.max_reminders ?? 3),
    cadence: (policy?.cadence_days ?? []).join(', '),
    budget: centsText(policy?.daily_model_budget_cents ?? 0),
    rules: (policy?.autonomy_rules ?? []).map((r) => ({
      key: ruleKey++,
      id: r.id,
      description: r.description,
      action_kind: r.action_kind,
      mode: r.mode,
      maxAmount: centsText(r.max_amount_cents),
      require_evidence: r.require_evidence,
      require_undisputed: r.require_undisputed,
    })),
  };
}

const SUGGESTED: Omit<RuleDraft, 'key'> = {
  id: 'routine-reminders',
  description: 'Routine reminders up to CAD 5,000 on undisputed invoices run automatically',
  action_kind: 'collection_reminder',
  mode: 'auto',
  maxAmount: '5000',
  require_evidence: true,
  require_undisputed: true,
};

/** The draft as a PolicyIn, or a list of problems with what was typed. */
function toPolicyIn(d: Draft): { body: PolicyIn } | { problems: string[] } {
  const problems: string[] = [];
  const int = (text: string, label: string) => {
    if (!/^\d+$/.test(text.trim())) problems.push(`${label} must be a whole number.`);
    return Number(text.trim());
  };
  const ttl = int(d.ttl, 'Approval expiry');
  const followUp = int(d.followUp, 'Follow-up days');
  const maxReminders = int(d.maxReminders, 'Max reminders');
  const cadenceParts = d.cadence.split(',').map((s) => s.trim()).filter(Boolean);
  if (cadenceParts.some((p) => !/^\d+$/.test(p))) problems.push('Cadence days must be whole numbers separated by commas.');
  const budget = parseCents(d.budget || '0');
  if (budget === null) problems.push('Daily model budget must be an amount like 5 or 5.50.');
  const rules: AutonomyRuleIn[] = d.rules.map((r, i) => {
    let max: number | null = null;
    if (r.maxAmount.trim()) {
      max = parseCents(r.maxAmount);
      if (max === null || max === 0) problems.push(`Rule ${i + 1}: max amount must be a positive amount, or empty for no limit.`);
    }
    return {
      id: r.id.trim(),
      description: r.description.trim(),
      action_kind: r.action_kind,
      mode: r.mode,
      max_amount_cents: max,
      require_evidence: r.require_evidence,
      require_undisputed: r.require_undisputed,
    };
  });
  if (problems.length) return { problems };
  return {
    body: {
      approval_ttl_minutes: ttl,
      follow_up_days: followUp,
      max_reminders: maxReminders,
      cadence_days: cadenceParts.map(Number),
      daily_model_budget_cents: budget ?? 0,
      autonomy_rules: rules,
    },
  };
}

export function PolicyPage() {
  const policy = usePolicy();
  return (
    <div className="page-stack">
      <div className="page-head">
        <h2>Policy</h2>
        <p className="muted">
          The rules the system follows. Only actions a rule marks <strong>auto</strong> run without you; the first matching
          rule wins.
        </p>
      </div>
      {policy.isPending && <Skeleton label="Loading policy" rows={6} />}
      {policy.error && <ErrorBox error={policy.error} onRetry={() => void policy.refetch()} />}
      {policy.isSuccess && <PolicyForm key={policy.data?.version ?? 0} policy={policy.data} />}
    </div>
  );
}

function PolicyForm({ policy }: { policy: Policy | null }) {
  const { client, signOut } = useSession();
  const qc = useQueryClient();
  const push = useToast();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(policy));
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<PolicyPreview | null>(null);
  const [previewError, setPreviewError] = useState<ApiError | null>(null);

  // A preview describes one draft; any edit makes it stale.
  useEffect(() => {
    setPreview(null);
  }, [draft]);

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const setRule = (key: number, change: Partial<RuleDraft>) =>
    set(
      'rules',
      draft.rules.map((r) => (r.key === key ? { ...r, ...change } : r)),
    );
  const move = (i: number, by: number) => {
    const rules = [...draft.rules];
    const [rule] = rules.splice(i, 1);
    if (rule) rules.splice(i + by, 0, rule);
    set('rules', rules);
  };

  const build = () => {
    const out = toPolicyIn(draft);
    if ('problems' in out) {
      setProblems(out.problems);
      return null;
    }
    setProblems([]);
    return out.body;
  };

  const handle = (err: unknown, setter: (e: ApiError) => void) => {
    const e = asApiError(err);
    if (e.kind === 'unauthorized') signOut('The server rejected the owner token. Enter it again.');
    else setter(e);
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const body = build();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await client.savePolicy(body);
      qc.setQueryData(keys.policy, saved);
      void qc.invalidateQueries({ queryKey: keys.allWorkspaces });
      push({ tone: 'success', title: `Policy v${saved.version} saved` });
    } catch (err) {
      handle(err, setError);
    } finally {
      setBusy(false);
    }
  };

  const runPreview = async () => {
    const body = build();
    if (!body) return;
    setPreviewError(null);
    try {
      setPreview(await client.previewPolicy(body));
    } catch (err) {
      handle(err, setPreviewError);
    }
  };

  return (
    <form className="policy-form" onSubmit={(e) => void save(e)}>
      <section className="panel">
        <div className="panel-head">
          <h3>Basics</h3>
          <span className="muted small">
            {policy ? `Current: v${policy.version} by ${policy.created_by}, ${formatTime(policy.created_at)}` : 'No policy stored yet: sending is disabled until you save one.'}
          </span>
        </div>
        <div className="form-grid">
          <div>
            <label htmlFor="pol-ttl">Approval expires after (minutes)</label>
            <input id="pol-ttl" inputMode="numeric" value={draft.ttl} onChange={(e) => set('ttl', e.target.value)} />
          </div>
          <div>
            <label htmlFor="pol-follow">Follow up after (days)</label>
            <input id="pol-follow" inputMode="numeric" value={draft.followUp} onChange={(e) => set('followUp', e.target.value)} />
          </div>
          <div>
            <label htmlFor="pol-max">Max reminders per invoice</label>
            <input id="pol-max" inputMode="numeric" value={draft.maxReminders} onChange={(e) => set('maxReminders', e.target.value)} />
          </div>
          <div>
            <label htmlFor="pol-cadence">Reminder cadence (days after due, comma separated)</label>
            <input id="pol-cadence" placeholder="e.g. 3, 10, 21" value={draft.cadence} onChange={(e) => set('cadence', e.target.value)} />
          </div>
          <div>
            <label htmlFor="pol-budget">Daily model budget (CAD)</label>
            <input id="pol-budget" inputMode="decimal" value={draft.budget} onChange={(e) => set('budget', e.target.value)} />
            {parseCents(draft.budget || '0') !== null && (
              <span className="muted small">= {formatCents(parseCents(draft.budget || '0') ?? 0, 'CAD')} per day</span>
            )}
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h3>Autonomy rules</h3>
          <span className="count">{draft.rules.length}</span>
        </div>
        <p className="panel-note">
          Checked top to bottom. <strong>auto</strong> runs without you, <strong>approve</strong> waits in your queue,{' '}
          <strong>blocked</strong> is never sent. No matching rule means it waits for you.
        </p>
        {draft.rules.length === 0 && (
          <EmptyState title="No rules: nothing runs automatically">
            Add a rule, or start from the suggested one for routine reminders up to CAD 5,000.
          </EmptyState>
        )}
        <ol className="rules">
          {draft.rules.map((r, i) => (
            <li key={r.key} className="rule" aria-label={`Rule ${i + 1}`}>
              <div className="rule-order">
                <span className="rule-n">{i + 1}</span>
                <button type="button" className="icon" aria-label={`Move rule ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button
                  type="button"
                  className="icon"
                  aria-label={`Move rule ${i + 1} down`}
                  disabled={i === draft.rules.length - 1}
                  onClick={() => move(i, 1)}
                >
                  ↓
                </button>
              </div>
              <div className="rule-fields">
                <div>
                  <label htmlFor={`rule-id-${r.key}`}>Rule id</label>
                  <input id={`rule-id-${r.key}`} value={r.id} onChange={(e) => setRule(r.key, { id: e.target.value })} />
                </div>
                <div>
                  <label htmlFor={`rule-kind-${r.key}`}>Action</label>
                  <select
                    id={`rule-kind-${r.key}`}
                    value={r.action_kind}
                    onChange={(e) => setRule(r.key, { action_kind: e.target.value as RuleDraft['action_kind'] })}
                  >
                    <option value="collection_reminder">Collection reminder</option>
                    <option value="request_information">Request information</option>
                  </select>
                </div>
                <div>
                  <label htmlFor={`rule-mode-${r.key}`}>Mode</label>
                  <select
                    id={`rule-mode-${r.key}`}
                    value={r.mode}
                    onChange={(e) => setRule(r.key, { mode: e.target.value as RuleDraft['mode'] })}
                  >
                    <option value="auto">auto: run without me</option>
                    <option value="approve">approve: ask me</option>
                    <option value="blocked">blocked: never</option>
                  </select>
                </div>
                <div>
                  <label htmlFor={`rule-max-${r.key}`}>Max amount (CAD, empty = no limit)</label>
                  <input
                    id={`rule-max-${r.key}`}
                    inputMode="decimal"
                    value={r.maxAmount}
                    onChange={(e) => setRule(r.key, { maxAmount: e.target.value })}
                  />
                </div>
                <div className="span-2">
                  <label htmlFor={`rule-desc-${r.key}`}>Description</label>
                  <input
                    id={`rule-desc-${r.key}`}
                    maxLength={200}
                    value={r.description}
                    onChange={(e) => setRule(r.key, { description: e.target.value })}
                  />
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={r.require_evidence}
                    onChange={(e) => setRule(r.key, { require_evidence: e.target.checked })}
                  />{' '}
                  Requires supporting evidence
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={r.require_undisputed}
                    onChange={(e) => setRule(r.key, { require_undisputed: e.target.checked })}
                  />{' '}
                  Only if the invoice is not disputed
                </label>
              </div>
              <button
                type="button"
                className="danger"
                aria-label={`Remove rule ${i + 1}`}
                onClick={() => set('rules', draft.rules.filter((x) => x.key !== r.key))}
              >
                Remove
              </button>
            </li>
          ))}
        </ol>
        <div className="row-buttons">
          <button
            type="button"
            className="secondary"
            disabled={draft.rules.length >= 20}
            onClick={() =>
              set('rules', [
                ...draft.rules,
                {
                  key: ruleKey++,
                  id: `rule-${draft.rules.length + 1}`,
                  description: '',
                  action_kind: 'collection_reminder',
                  mode: 'approve',
                  maxAmount: '',
                  require_evidence: true,
                  require_undisputed: true,
                },
              ])
            }
          >
            + Add rule
          </button>
          {!draft.rules.some((r) => r.id === SUGGESTED.id) && (
            <button
              type="button"
              className="secondary"
              onClick={() => set('rules', [...draft.rules, { ...SUGGESTED, key: ruleKey++ }])}
            >
              + Suggested: routine reminders up to CAD 5,000
            </button>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h3>Preview and save</h3>
        </div>
        <p className="panel-note">Saving creates a new policy version. Approvals given under an older version stop matching.</p>
        {previewError && isNotBuilt(previewError) && <NotBuiltNotice what="Preview" error={previewError} />}
        {previewError && !isNotBuilt(previewError) && <ErrorBox error={previewError} />}
        {preview && (
          <p role="status" className="success">
            With these rules, {preview.auto} of {preview.open_cases} open cases would be handled automatically,{' '}
            {preview.approve} would wait for you and {preview.blocked} would be blocked.
          </p>
        )}
        {problems.length > 0 && (
          <ul role="alert" className="error problems">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        {error && <ErrorBox error={error} />}
        <div className="row-buttons">
          <button type="button" className="secondary" onClick={() => void runPreview()}>
            Preview impact
          </button>
          <button type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save new policy version'}
          </button>
        </div>
      </section>
    </form>
  );
}
