import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api/client';

export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => Promise<void>;
}

/**
 * Loads data with `load` and reloads when `key` changes. Keeps the last good
 * data while a reload runs, and ignores answers that arrive after a newer
 * request started (so a slow old answer cannot overwrite a fresh one).
 */
export function useResource<T>(load: () => Promise<T>, key: string): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const latest = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    const ticket = ++latest.current;
    setLoading(true);
    try {
      const result = await loadRef.current();
      if (ticket !== latest.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (ticket !== latest.current) return;
      setError(err instanceof ApiError ? err : new ApiError('server', null, String(err)));
    } finally {
      if (ticket === latest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [key, reload]);

  return { data, error, loading, reload };
}
