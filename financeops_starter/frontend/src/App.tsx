import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from './api/client';
import { TokenEntry } from './components/TokenEntry';
import { CaseList } from './components/CaseList';
import { Workspace } from './components/Workspace';
import { DemoLab, type OpenCase } from './components/DemoLab';

const TOKEN_KEY = 'financeops.ownerToken';

function readStoredToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function storeToken(token: string | null) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage blocked (private mode): the token still lives in memory
  }
}

// The selected case lives in the URL hash (#case=<id>), so the page path is
// always /app/ and the server never needs client-side routes.
function caseFromHash(): string | null {
  const match = /^#case=(.+)$/.exec(window.location.hash);
  if (!match?.[1]) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null; // a malformed link (e.g. "#case=%") just selects nothing
  }
}

export function App() {
  const [token, setToken] = useState<string | null>(readStoredToken);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(caseFromHash);
  // Bumped after any change, so the case list and workspace both refetch.
  const [revision, setRevision] = useState(0);
  const [openCase, setOpenCase] = useState<OpenCase | null>(null);
  const mainHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const onHash = () => setSelected(caseFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const client = useMemo(() => api(token), [token]);

  const signIn = (value: string) => {
    storeToken(value);
    setToken(value);
    setNotice(null);
    setTimeout(() => mainHeading.current?.focus(), 0);
  };

  const signOut = useCallback((message: string | null) => {
    storeToken(null);
    setToken(null);
    setNotice(message);
  }, []);

  /** Every screen sends its errors here first: a 401 ends the session. */
  const handleAuthError = useCallback(
    (error: ApiError) => {
      if (error.kind === 'unauthorized') {
        signOut('The server rejected the owner token. Enter it again.');
        return true;
      }
      return false;
    },
    [signOut],
  );

  const selectCase = (caseId: string) => {
    window.location.hash = `case=${encodeURIComponent(caseId)}`;
    setSelected(caseId);
  };

  const changed = useCallback(() => setRevision((r) => r + 1), []);

  if (!token) {
    return <TokenEntry onSubmit={signIn} notice={notice} />;
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1 ref={mainHeading} tabIndex={-1}>
          FinanceOps owner console
        </h1>
        <p className="banner-synthetic">Synthetic data · simulated delivery · no real money moves</p>
        <button type="button" className="secondary" onClick={() => signOut(null)}>
          Clear token
        </button>
      </header>
      <div className="layout">
        <CaseList
          client={client}
          revision={revision}
          selected={selected}
          onSelect={selectCase}
          onAuthError={handleAuthError}
        />
        <main className="main-column">
          {selected ? (
            <Workspace
              key={selected}
              caseId={selected}
              client={client}
              revision={revision}
              onChanged={changed}
              onAuthError={handleAuthError}
              onSnapshot={setOpenCase}
            />
          ) : (
            <section className="panel">
              <h2>No case selected</h2>
              <p>Pick a case from the list, or load a synthetic scenario in the demo lab.</p>
            </section>
          )}
          <DemoLab
            client={client}
            openCase={openCase && openCase.caseId === selected ? openCase : null}
            onChanged={changed}
            onLoaded={(ids) => {
              if (ids[0]) selectCase(ids[0]);
            }}
            onAuthError={handleAuthError}
          />
        </main>
      </div>
    </div>
  );
}
