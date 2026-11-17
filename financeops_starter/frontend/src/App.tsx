import { useCallback, useMemo, useRef, useState } from 'react';
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom';
import { api, ApiError } from './api/client';
import { TokenEntry } from './components/TokenEntry';
import { Shell } from './components/Shell';
import { SessionProvider, type Session } from './session';
import { HomePage } from './pages/HomePage';
import { CasesPage } from './pages/CasesPage';
import { CaseDetailPage } from './pages/CaseDetailPage';
import { ActivityPage } from './pages/ActivityPage';
import { AgentsPage } from './pages/AgentsPage';
import { PolicyPage } from './pages/PolicyPage';
import { DemoLabPage } from './pages/DemoLabPage';

const TOKEN_KEY = 'financeops.ownerToken';
const REJECTED = 'The server rejected the owner token. Enter it again.';

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

// Milestone 4 links used "#case=<id>"; send them to the new case page.
function upgradeOldHash() {
  const match = /^#case=(.+)$/.exec(window.location.hash);
  if (match?.[1]) window.location.hash = `#/cases/${match[1]}`;
}

interface Props {
  /** Live polling interval in ms (tests pass a small value). */
  pollMs?: number;
}

export function App({ pollMs = 3000 }: Props) {
  const [token, setToken] = useState<string | null>(readStoredToken);
  const [notice, setNotice] = useState<string | null>(null);
  const mainHeading = useRef<HTMLHeadingElement>(null);
  const signOutRef = useRef<(n: string | null) => void>(() => {});

  // One cache per page load. Any 401 (query or mutation) ends the session.
  const [queryClient] = useState(() => {
    const onError = (error: unknown) => {
      if (error instanceof ApiError && error.kind === 'unauthorized') signOutRef.current(REJECTED);
    };
    return new QueryClient({
      queryCache: new QueryCache({ onError }),
      mutationCache: new MutationCache({ onError }),
      defaultOptions: {
        // Errors show with a Retry button; polling queries recover by themselves.
        queries: { retry: false, staleTime: 1500, refetchOnWindowFocus: true },
      },
    });
  });

  const signOut = useCallback(
    (message: string | null) => {
      storeToken(null);
      setToken(null);
      setNotice(message);
      queryClient.clear();
    },
    [queryClient],
  );
  signOutRef.current = signOut;

  const signIn = (value: string) => {
    storeToken(value);
    setToken(value);
    setNotice(null);
    setTimeout(() => mainHeading.current?.focus(), 0);
  };

  const session = useMemo<Session | null>(
    () => (token ? { client: api(token), signOut, pollMs } : null),
    [token, signOut, pollMs],
  );

  if (!session) {
    return <TokenEntry onSubmit={signIn} notice={notice} />;
  }

  upgradeOldHash();

  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider value={session}>
        <HashRouter>
          <Shell headingRef={mainHeading}>
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/cases" element={<CasesPage />} />
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
              <Route path="/activity" element={<ActivityPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route path="/policy" element={<PolicyPage />} />
              <Route path="/demo" element={<DemoLabPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Shell>
        </HashRouter>
      </SessionProvider>
    </QueryClientProvider>
  );
}
