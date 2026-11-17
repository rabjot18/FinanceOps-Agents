import { useState, type FormEvent } from 'react';

interface Props {
  onSubmit: (token: string) => void;
  notice: string | null;
}

export function TokenEntry({ onSubmit, notice }: Props) {
  const [value, setValue] = useState('');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const token = value.trim();
    if (token) onSubmit(token);
  };

  return (
    <main className="token-page">
      <form className="panel token-form" onSubmit={submit} aria-labelledby="token-title">
        <span className="brand-mark" aria-hidden="true">
          FO
        </span>
        <h1 id="token-title">FinanceOps owner console</h1>
        <p className="lead">Review receivable cases and approve every message before it goes out.</p>
        <p className="warning-note">
          Local demo only, not production authentication. The token stays in this browser tab
          (memory and sessionStorage) until you clear it or close the tab.
        </p>
        {notice && (
          <p role="alert" className="error">
            {notice}
          </p>
        )}
        <label htmlFor="owner-token">Owner token</label>
        <input
          id="owner-token"
          type="password"
          autoComplete="off"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="submit" disabled={!value.trim()}>
          Continue
        </button>
      </form>
    </main>
  );
}
