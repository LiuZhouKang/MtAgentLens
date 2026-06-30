'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

type SearchParamsLike = {
  get: (key: string) => string | null;
};

type UseHashRouteStateOptions<TSearchParams extends SearchParamsLike> = {
  searchParams: TSearchParams;
  parseValue: (searchParams: TSearchParams, hash?: string | null) => string | null;
  buildHref: (value?: string | null) => string;
};

export function useHashRouteState<TSearchParams extends SearchParamsLike>({
  searchParams,
  parseValue,
  buildHref,
}: UseHashRouteStateOptions<TSearchParams>) {
  const [hashValue, setHashValue] = useState<string | null>(null);

  useEffect(() => {
    const syncHashValue = () => {
      setHashValue(window.location.hash || null);
    };

    syncHashValue();
    window.addEventListener('hashchange', syncHashValue);
    return () => window.removeEventListener('hashchange', syncHashValue);
  }, [parseValue]);

  const selectedValue = useMemo(
    () =>
      parseValue(
        searchParams,
        hashValue,
      ),
    [hashValue, parseValue, searchParams],
  );

  const setRouteValue = useCallback((nextValue: string | null) => {
    setHashValue(nextValue);

    if (typeof window !== 'undefined') {
      window.history.replaceState(window.history.state, '', buildHref(nextValue));
    }
  }, [buildHref]);

  return {
    hashValue,
    selectedValue,
    setRouteValue,
  };
}
