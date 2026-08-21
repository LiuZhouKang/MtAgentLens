'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

type SearchParamsLike = {
  get: (key: string) => string | null;
};

const EMPTY_SEARCH_PARAMS: SearchParamsLike = {
  get: () => null,
};

type UseHashRouteStateOptions<TSearchParams extends SearchParamsLike> = {
  searchParams: TSearchParams | null;
  parseValue: (searchParams: TSearchParams, hash?: string | null) => string | null;
  buildHref: (value?: string | null) => string;
};

export function useHashRouteState<TSearchParams extends SearchParamsLike>({
  searchParams,
  parseValue,
  buildHref,
}: UseHashRouteStateOptions<TSearchParams>) {
  const [hashValue, setHashValue] = useState<string | null>(null);
  const safeSearchParams = searchParams ?? (EMPTY_SEARCH_PARAMS as TSearchParams);

  useEffect(() => {
    const syncHashValue = () => {
      setHashValue(window.location.hash || null);
    };

    syncHashValue();
    window.addEventListener('hashchange', syncHashValue);
    return () => window.removeEventListener('hashchange', syncHashValue);
  }, [parseValue]);

  const selectedValue = useMemo(
    () => parseValue(safeSearchParams, hashValue),
    [hashValue, parseValue, safeSearchParams],
  );

  const setRouteValue = useCallback(
    (nextValue: string | null) => {
      const nextHref = buildHref(nextValue);
      const nextHash = nextHref.includes('#') ? nextHref.slice(nextHref.indexOf('#')) : null;
      setHashValue(nextHash);

      if (typeof window !== 'undefined') {
        window.history.replaceState(window.history.state, '', nextHref);
      }
    },
    [buildHref],
  );

  return {
    hashValue,
    selectedValue,
    setRouteValue,
  };
}
