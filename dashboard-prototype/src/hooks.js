import { useCallback, useEffect, useRef, useState } from "react";

export function useResource(loader, dependencies = [], refreshKey = 0) {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const requestId = useRef(0);
  const mounted = useRef(false);
  const [state, setState] = useState({
    data: null,
    error: null,
    loading: true,
    lastSuccessAt: null,
  });

  const reload = useCallback(async () => {
    const id = ++requestId.current;
    if (!mounted.current) return null;
    setState((current) => ({ ...current, error: null, loading: true }));
    try {
      const data = await loaderRef.current();
      if (!mounted.current || id !== requestId.current) return null;
      setState({ data, error: null, loading: false, lastSuccessAt: new Date().toISOString() });
      return data;
    } catch (error) {
      if (mounted.current && id === requestId.current)
        setState((current) => ({ ...current, error, loading: false }));
      return null;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestId.current++;
    };
  }, []);

  useEffect(() => {
    void reload();
    return () => { requestId.current++; };
  }, [...dependencies, refreshKey, reload]);

  return { ...state, stale: state.data != null && Boolean(state.error || state.loading), reload };
}

export function useDebouncedValue(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function useHealthTime() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}
