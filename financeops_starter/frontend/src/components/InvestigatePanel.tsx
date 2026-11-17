import { useState } from 'react';
import { ApiError, type Api } from '../api/client';

interface Props {
  caseId: string;
  client: Api;
  onChanged: () => void;
  onAuthError: (error: ApiError) => boolean;
}

/** Investigation calls a paid model, so it always needs a second, explicit click. */
export function InvestigatePanel({ caseId, client, onChanged, onAuthError }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await client.investigate(caseId);
      setConfirming(false);
      onChanged();
    } catch (err) {
      const apiError = err instanceof ApiError ? err : new ApiError('server', null, String(err));
      // The server may still be running the paid call: a retry needs a new confirm.
      setConfirming(false);
      if (!onAuthError(apiError)) setError(apiError);
      if (apiError.kind === 'conflict') onChanged();
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
