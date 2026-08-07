// The standalone /agents page was consolidated into the 4-page structure
// (态势 / 仪表盘 / 告警 / 追踪). Agent deep links now land on the overview
// dashboard, which already renders agent risk context and graph focus.

export function buildAgentHref(agentId?: string | null) {
  if (!agentId) {
    return '/overview';
  }

  return `/overview#agentId=${encodeURIComponent(agentId)}`;
}

export function parseAgentId(searchParams: URLSearchParams, hash?: string | null) {
  const queryAgentId = searchParams.get('agentId');
  if (queryAgentId) {
    return queryAgentId;
  }

  if (!hash) {
    return null;
  }

  const normalizedHash = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!normalizedHash) {
    return null;
  }

  const hashParams = new URLSearchParams(normalizedHash);
  return hashParams.get('agentId');
}
