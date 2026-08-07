// The standalone /clusters page was consolidated into the 4-page structure
// (态势 / 仪表盘 / 告警 / 追踪). Cluster deep links now land on the overview
// dashboard, which already renders the responsibility-cluster summary.

export function buildClusterHref(clusterId?: string | null) {
  if (!clusterId) {
    return '/overview';
  }

  return `/overview#clusterId=${encodeURIComponent(clusterId)}`;
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
