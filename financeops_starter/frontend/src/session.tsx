import { createContext, useContext, type ReactNode } from 'react';
import type { Api } from './api/client';

export interface Session {
  client: Api;
  /** Ends the session and returns to token entry, optionally with a notice. */
  signOut: (notice: string | null) => void;
  /** How often live data (activity, summary) is polled while the tab is visible. */
  pollMs: number;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ value, children }: { value: Session; children: ReactNode }) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error('useSession outside SessionProvider');
  return session;
}
