import { useState, type FormEvent } from 'react';
import { newId, type ApiError, type ProposalIn, type Workspace } from '../api/client';
import type { Mutate } from './Workspace';

interface Props {
  caseId: string;
  evidence: Workspace['evidence'];
  mutate: Mutate;
}

/** A manual owner proposal. The server renders the message from stored facts. */
export function ProposeForm({ caseId, evidence, mutate }: Props) {
  const [kind, setKind] = useState<ProposalIn['kind']>('collection_reminder');
  const [picked, setPicked] = useState<string[]>([]);
  const [rationale, setRationale] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const toggle = (id: string) =>
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body: ProposalIn = {
      action_id: newId('act'),
      kind,
      evidence_ids: picked,
      rationale: rationale.trim() || null,
    };
    const result = await mutate((c) => c.propose(caseId, body));
    setBusy(false);
    if (result.ok) {
      setPicked([]);
      setRationale('');
    } else if (result.error.kind !== 'conflict') {
      setError(result.error);
    }
  };

  return (
    <details className="propose">
      <summary>Propose an action manually</summary>
      <form onSubmit={(e) => void submit(e)}>
        <label htmlFor="propose-kind">Kind</label>
        <select id="propose-kind" value={kind} onChange={(e) => setKind(e.target.value as ProposalIn['kind'])}>
          <option value="collection_reminder">Collection reminder</option>
          <option value="request_information">Request information</option>
        </select>
        <fieldset>
          <legend>Supporting evidence</legend>
          {evidence.length === 0 && <p className="muted">No evidence on this case.</p>}
          {evidence.map((e) => (
            <label key={e.id} className="check">
              <input type="checkbox" checked={picked.includes(e.id)} onChange={() => toggle(e.id)} /> {e.id}
            </label>
          ))}
        </fieldset>
        <label htmlFor="propose-rationale">Rationale (optional, never sent to the customer)</label>
        <textarea
          id="propose-rationale"
          rows={2}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
        />
        {error && (
          <p role="alert" className="error">
            {error.detail}
          </p>
        )}
        <button type="submit" disabled={busy}>
          Propose
        </button>
      </form>
    </details>
  );
}
