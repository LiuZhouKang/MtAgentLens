export function buildClusterHref(clusterId?: string | null) {
  if (!clusterId) {
    return '/clusters';
  }

  return `/clusters#clusterId=${encodeURIComponent(clusterId)}`;
}

export function parseClusterId(searchParams: URLSearchParams, hash?: string | null) {
  const queryClusterId = searchParams.get('clusterId');
  if (queryClusterId) {
    return queryClusterId;
  }

  if (!hash) {
    return null;
  }

  const normalizedHash = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!normalizedHash) {
    return null;
  }

  const hashParams = new URLSearchParams(normalizedHash);
  return hashParams.get('clusterId');
}
