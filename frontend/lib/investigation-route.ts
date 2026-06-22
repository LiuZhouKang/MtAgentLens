export function buildInvestigationHref(alertId?: string | null) {
  if (!alertId) {
    return '/investigations';
  }

  return `/investigations#alertId=${encodeURIComponent(alertId)}`;
}

export function parseInvestigationAlertId(searchParams: URLSearchParams, hash?: string | null) {
  const queryAlertId = searchParams.get('alertId');
  if (queryAlertId) {
    return queryAlertId;
  }

  if (!hash) {
    return null;
  }

  const normalizedHash = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!normalizedHash) {
    return null;
  }

  const hashParams = new URLSearchParams(normalizedHash);
  return hashParams.get('alertId');
}
