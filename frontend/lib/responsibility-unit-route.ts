// The standalone /clusters page was consolidated into the 4-page structure
// (态势 / 仪表盘 / 告警 / 追踪). Responsibility-unit deep links now land on the
// overview dashboard, which already renders the responsibility-unit summary.

export function buildResponsibilityUnitHref(unitId?: string | null) {
  if (!unitId) {
    return '/overview';
  }

  return `/overview#unitId=${encodeURIComponent(unitId)}`;
}

export function parseUnitId(searchParams: URLSearchParams, hash?: string | null) {
  // 新契约 unitId 优先；向后兼容旧 `clusterId` 深链（重构前链接仍可解析）。
  const queryUnitId = searchParams.get('unitId') ?? searchParams.get('clusterId');
  if (queryUnitId) {
    return queryUnitId;
  }

  if (!hash) {
    return null;
  }

  const normalizedHash = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!normalizedHash) {
    return null;
  }

  const hashParams = new URLSearchParams(normalizedHash);
  return hashParams.get('unitId') ?? hashParams.get('clusterId');
}
