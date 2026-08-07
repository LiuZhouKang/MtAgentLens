import type { FacetSelection, WhyFacetKind } from './why-facets';

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

export const FACET_PARAM = 'facet';
const VALID_FACETS: WhyFacetKind[] = ['pollution', 'policy', 'diagnostic'];

export function parseFacetSelection(searchParams: { get(k: string): string | null } | null): FacetSelection {
  const raw = searchParams?.get(FACET_PARAM);
  return (VALID_FACETS as readonly string[]).includes(raw ?? '') ? (raw as WhyFacetKind) : 'all';
}

export function buildFacetHref(facet: FacetSelection, currentSearch: string, currentHash: string): string {
  const params = new URLSearchParams(currentSearch);
  if (facet === 'all') params.delete(FACET_PARAM);
  else params.set(FACET_PARAM, facet);
  const qs = params.toString();
  const hash = currentHash.startsWith('#') ? currentHash : currentHash ? `#${currentHash}` : '';
  return qs ? `/investigations?${qs}${hash}` : `/investigations${hash}`;
}

/**
 * 在告警深链 href 上保留当前 query 参数（如 `?facet=`）。
 * 分面激活时点选队列中的告警，`buildInvestigationHref` 会丢弃 query 导致分面丢失；
 * 此函数将 query 注入 hash 之前，保证「分面 + 告警选择」可组合、可深链。
 * 同时剔除 query 中陈旧的 `alertId`：hash 是选中告警的唯一权威，避免外部 `?alertId=`
 * 深链与 hash 冲突时 `parseInvestigationAlertId` 优先 query 把选中「弹回」旧告警。
 */
export function buildAlertHrefWithQuery(alertId: string | null | undefined, currentSearch: string): string {
  const base = buildInvestigationHref(alertId);
  const params = new URLSearchParams(currentSearch.trim());
  params.delete('alertId');
  const qs = params.toString();
  if (!qs) return base;
  const hashIdx = base.indexOf('#');
  return hashIdx === -1 ? `${base}?${qs}` : `${base.slice(0, hashIdx)}?${qs}${base.slice(hashIdx)}`;
}
