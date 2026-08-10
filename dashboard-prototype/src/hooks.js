import { useCallback, useEffect, useRef, useState } from "react";

export function useResource(loader, dependencies = [], refreshKey = 0) {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const [state, setState] = useState({
    data: null,
    error: null,
    loading: true,
  });

  const reload = useCallback(async () => {
    setState((current) => ({ ...current, error: null, loading: true }));
    try {
      const data = await loaderRef.current();
      setState({ data, error: null, loading: false });
      return data;
    } catch (error) {
      setState((current) => ({ ...current, error, loading: false }));
      return null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    setState((current) => ({ ...current, error: null, loading: true }));
    loaderRef
      .current()
      .then((data) => {
        if (active) setState({ data, error: null, loading: false });
      })
      .catch((error) => {
        if (active) setState((current) => ({ ...current, error, loading: false }));
      });
    return () => {
      active = false;
    };
  }, [...dependencies, refreshKey]);

  return { ...state, reload };
}

export function useDebouncedValue(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
