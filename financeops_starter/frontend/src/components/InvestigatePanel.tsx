import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { asApiError, type ApiError } from '../api/client';
import { refreshAll } from '../api/queries';
import { useSession } from '../session';

interface Props {
  caseId: string;
}

/** Investigation calls a paid model, so it always needs a second, explicit click. */
export function InvestigatePanel({ caseId }: Props) {
  const { client, signOut } = useSession();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await client.investigate(caseId);
      setConfirming(false);
      await refreshAll(qc);
    } catch (err) {
      const apiError = asApiError(err);
      // The server may still be running the paid call: a retry needs a new confirm.
      setConfirming(false);
      if (apiError.kind === 'unauthorized') signOut('The server rejected the owner token. Enter it again.');
      else setError(apiError);
      if (apiError.kind === 'conflict') await refreshAll(qc);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="investigate">
      {!confirming ? (
        <button type="button" className="secondary" onClick={() => setConfirming(true)}>
          Investigate with AI <span className="tag-paid">Paid model call</span>
        </button>
      ) : (
        <div role="group" aria-label="Confirm paid AI investigation" className="confirm-box">
          <p>
            <strong>This calls a paid Claude model and costs money.</strong> It can take up to a few
            minutes. The AI can only propose an action; you still approve it.
          </p>
          <button type="button" disabled={busy} onClick={() => void confirm()}>
            {busy ? 'Investigating…' : 'Yes, run the paid investigation'}
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error.detail}
        </p>
      )}
    </div>
  );
}
