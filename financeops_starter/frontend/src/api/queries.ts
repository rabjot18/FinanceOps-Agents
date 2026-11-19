// TanStack Query hooks for every screen. Query keys live here so a change in
// one place (the activity feed, an owner action) can refresh the rest.
import { useCallback, useState } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useSession } from '../session';
import { ApiError, asApiError, type ActivityItem, type Api, type CaseListParams } from './client';

export const keys = {
  summary: ['summary'] as const,
  attention: ['attention'] as const,
  cases: (params: CaseListParams) => ['cases', params] as const,
  allCases: ['cases'] as const,
  workspace: (caseId: string) => ['workspace', caseId] as const,
  allWorkspaces: ['workspace'] as const,
  agentRun: (runId: string) => ['agent-run', runId] as const,
  activity: ['activity'] as const,
  agents: ['agents'] as const,
  briefing: ['briefing'] as const,
  policy: ['policy'] as const,
  autonomy: ['autonomy'] as const,
  clock: ['clock'] as const,
  scenarios: ['scenarios'] as const,
  inbox: ['inbox'] as const,
  flow: ['flow'] as const,
  caseFlow: (caseId: string) => ['case-flow', caseId] as const,
  allCaseFlows: ['case-flow'] as const,
  outcomes: ['outcomes'] as const,
};

/** Returns null instead of throwing when the server says 404 (nothing exists yet). */
async function orNull<T>(load: () => Promise<T>): Promise<T | null> {
  try {
    return await load();
  } catch (err) {
    if (err instanceof ApiError && err.kind === 'not_found') return null;
    throw err;
  }
}

export function useSummary() {
  const { client, pollMs } = useSession();
  return useQuery({ queryKey: keys.summary, queryFn: () => client.summary(), refetchInterval: pollMs });
}

export function useAttention() {
  const { client } = useSession();
  return useQuery({ queryKey: keys.attention, queryFn: () => client.attention() });
}

export function useCases(params: CaseListParams) {
  const { client } = useSession();
  return useQuery({
    queryKey: keys.cases(params),
    queryFn: () => client.listCases(params),
    placeholderData: (previous) => previous, // keep the table while the next page loads
  });
}

export function useWorkspace(caseId: string | null) {
  const { client } = useSession();
  return useQuery({
    queryKey: keys.workspace(caseId ?? ''),
    queryFn: () => client.workspace(caseId ?? ''),
    enabled: !!caseId,
  });
}

export function useAgentRun(runId: string | null) {
  const { client } = useSession();
  return useQuery({
    queryKey: keys.agentRun(runId ?? ''),
    queryFn: () => client.agentRun(runId ?? ''),
    enabled: !!runId,
  });
}

export function useAgents() {
  const { client } = useSession();
  return useQuery({ queryKey: keys.agents, queryFn: () => client.agents() });
}

/** null until the first briefing exists (the server answers 404). */
export function useBriefing() {
  const { client } = useSession();
  return useQuery({ queryKey: keys.briefing, queryFn: () => orNull(() => client.latestBriefing()) });
}

/** null when no policy is stored yet. */
export function usePolicy() {
  const { client } = useSession();
  return useQuery({ queryKey: keys.policy, queryFn: () => orNull(() => client.policy()) });
}

export function useAutonomy() {
  const { client } = useSession();
  return useQuery({ queryKey: keys.autonomy, queryFn: () => client.autonomy() });
}

export function useClock(enabled: boolean) {
  const { client, pollMs } = useSession();
  return useQuery({ queryKey: keys.clock, queryFn: () => client.clock(), enabled, refetchInterval: pollMs });
}

// --- Milestone 6: flow board and outcomes (polled, like the summary) ---

export function useFlow() {
  const { client, pollMs } = useSession();
  return useQuery({ queryKey: keys.flow, queryFn: () => client.flow(), refetchInterval: pollMs });
}

export function useCaseFlow(caseId: string | null) {
  const { client, pollMs } = useSession();
  return useQuery({
    queryKey: keys.caseFlow(caseId ?? ''),
    queryFn: () => client.caseFlow(caseId ?? ''),
    enabled: !!caseId,
    refetchInterval: pollMs,
  });
}

export function useOutcomes() {
  const { client, pollMs } = useSession();
  return useQuery({ queryKey: keys.outcomes, queryFn: () => client.outcomes(), refetchInterval: pollMs });
}

export function useScenarios() {
  const { client } = useSession();
  return useQuery({ queryKey: keys.scenarios, queryFn: () => client.scenarios(), staleTime: Infinity });
}

// --- live activity feed --------------------------------------------------

export interface Feed {
  /** Oldest first, as the server sends them; at most FEED_KEEP items. */
  items: ActivityItem[];
  cursor: number;
}

const FEED_KEEP = 300;
const PAGE = 200;
const MAX_PAGES = 25; // the first load walks forward from the start (the contract has no "latest N")

/** Marks the queries an activity item can affect as stale; mounted ones refetch now. */
export function invalidateFor(qc: QueryClient, items: ActivityItem[]) {
  const kinds = new Set(items.map((i) => i.kind));
  void qc.invalidateQueries({ queryKey: keys.attention });
  void qc.invalidateQueries({ queryKey: keys.summary });
  void qc.invalidateQueries({ queryKey: keys.allCases });
  void qc.invalidateQueries({ queryKey: keys.flow });
  void qc.invalidateQueries({ queryKey: keys.allCaseFlows });
  void qc.invalidateQueries({ queryKey: keys.outcomes });
  const caseWide = items.some((i) => i.case_id === null);
  if (caseWide) void qc.invalidateQueries({ queryKey: keys.allWorkspaces });
  else
    for (const id of new Set(items.map((i) => i.case_id))) {
      if (id) void qc.invalidateQueries({ queryKey: keys.workspace(id) });
    }
  if ([...kinds].some((k) => k.startsWith('agent_'))) void qc.invalidateQueries({ queryKey: keys.agents });
  if (kinds.has('briefing_published')) void qc.invalidateQueries({ queryKey: keys.briefing });
  if (kinds.has('policy_changed')) void qc.invalidateQueries({ queryKey: keys.policy });
  if (kinds.has('autonomy_paused') || kinds.has('autonomy_resumed'))
    void qc.invalidateQueries({ queryKey: keys.autonomy });
}

/**
 * Polls GET /activity?after=<cursor> (TanStack Query pauses the interval while
 * the tab is hidden). New items refresh whatever they affect.
 */
export function useActivityFeed() {
  const { client, pollMs } = useSession();
  const qc = useQueryClient();
  return useQuery({
    queryKey: keys.activity,
    queryFn: async (): Promise<Feed> => {
      const previous = qc.getQueryData<Feed>(keys.activity);
      let cursor = previous?.cursor ?? 0;
      const fresh: ActivityItem[] = [];
      for (let page = 0; page < MAX_PAGES; page++) {
        const out = await client.activity(cursor, PAGE);
        fresh.push(...out.items);
        cursor = out.cursor;
        if (out.items.length < PAGE) break;
      }
      if (previous && fresh.length > 0) invalidateFor(qc, fresh);
      if (previous && fresh.length === 0) return previous;
      return { items: [...(previous?.items ?? []), ...fresh].slice(-FEED_KEEP), cursor };
    },
    refetchInterval: pollMs,
  });
}

// --- owner changes ---------------------------------------------------------

export type ChangeResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

/** Everything an owner change can affect. Awaited, so buttons stay busy until fresh data is in. */
export async function refreshAll(qc: QueryClient) {
  await Promise.all(
    [
      keys.attention,
      keys.summary,
      keys.allCases,
      keys.allWorkspaces,
      keys.activity,
      keys.agents,
      keys.autonomy,
      keys.flow,
      keys.allCaseFlows,
      keys.outcomes,
    ].map(
      (queryKey) => qc.invalidateQueries({ queryKey }),
    ),
  );
}

/**
 * Runs one owner change against the server, then refetches. A 409 is never
 * retried: it sets `conflict` so the page can say "this case changed" while
 * the fresh data loads. A 401 ends the session.
 */
export function useChange() {
  const { client, signOut } = useSession();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);

  const run = useCallback(
    async <T,>(change: (c: Api) => Promise<T>): Promise<ChangeResult<T>> => {
      setBusy(true);
      try {
        const data = await change(client);
        setConflict(null);
        await refreshAll(qc);
        return { ok: true, data };
      } catch (err) {
        const error = asApiError(err);
        if (error.kind === 'unauthorized') {
          signOut('The server rejected the owner token. Enter it again.');
          return { ok: false, error };
        }
        if (error.kind === 'conflict') setConflict(error.detail);
        if (error.kind === 'conflict' || error.kind === 'network' || error.kind === 'server') {
          // The change may or may not have happened: show the server's state.
          await refreshAll(qc);
        }
        return { ok: false, error };
      } finally {
        setBusy(false);
      }
    },
    [client, qc, signOut],
  );

  return { run, busy, conflict, clearConflict: useCallback(() => setConflict(null), []) };
}

// Every query function goes through api/client.ts, which only throws ApiError.
declare module '@tanstack/react-query' {
  interface Register {
    defaultError: ApiError;
  }
}
