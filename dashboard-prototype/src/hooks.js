import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export function useResource(loader, dependencies = [], refreshKey = 0) {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const owner = useMemo(() => ({ active: false, generation: 0 }), dependencies);
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const [state, setState] = useState({
    owner,
    data: null,
    error: null,
    loading: true,
  });

  const reload = useCallback(async () => {
    if (!owner.active || ownerRef.current !== owner) return null;
    const generation = ++owner.generation;
    const isCurrent = () => owner.active && ownerRef.current === owner && owner.generation === generation;
    setState((current) => ({ owner, data: current.owner === owner ? current.data : null, error: current.owner === owner ? current.error : null, loading: true }));
    try {
      const data = await loaderRef.current();
      if (!isCurrent()) return null;
      setState({ owner, data, error: null, loading: false });
      return data;
    } catch (error) {
      if (isCurrent()) setState((current) => ({ ...current, error, loading: false }));
      return null;
    }
  }, [owner]);

  useEffect(() => {
    owner.active = true;
    reload();
    return () => {
      owner.active = false;
      owner.generation += 1;
    };
  }, [owner, reload, refreshKey]);

  return state.owner === owner ? { data: state.data, error: state.error, loading: state.loading, reload } : { data: null, error: null, loading: true, reload };
}

export function useObservedAt() {
  const [observedAt, setObservedAt] = useState(() => Date.now());
  useEffect(() => {
    const recomputeAge = () => setObservedAt(Date.now());
    // Wall time must advance even while a refresh is pending after sleep.
    const clock = setInterval(recomputeAge, 1000);
    window.addEventListener("focus", recomputeAge);
    document.addEventListener("visibilitychange", recomputeAge);
    return () => { clearInterval(clock); window.removeEventListener("focus", recomputeAge); document.removeEventListener("visibilitychange", recomputeAge); };
  }, []);
  return observedAt;
}

export function useDebouncedValue(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
