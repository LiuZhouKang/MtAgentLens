export function buildAgentHref(agentId?: string | null) {
  if (!agentId) {
    return '/agents';
  }

  return `/agents#agentId=${encodeURIComponent(agentId)}`;
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
