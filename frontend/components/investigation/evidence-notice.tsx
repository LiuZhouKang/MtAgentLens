'use client';

/**
 * Evidence fetch state as a visible fact.
 *
 * A failed request used to collapse to `[]`, so the workbench printed "0 条"
 * and every empty state claimed there was no evidence. Unknown is not zero.
 */
export default function EvidenceNotice({
  loading,
  error,
}: {
  loading: boolean;
  error: string | null;
}) {
  if (error) {
    return (
      <div
        role="alert"
        data-testid="evidence-error"
        style={{
          marginBottom: 10,
          padding: '8px 10px',
          borderRadius: 8,
          border: '1px solid rgba(248,113,113,0.35)',
          background: 'rgba(248,113,113,0.08)',
          color: '#f87171',
          fontSize: 11,
        }}
      >
        证据加载失败：{error}。当前计数不代表“没有证据”。
      </div>
    );
  }
  if (loading) {
    return (
      <div
        className="ln-panel__meta"
        data-testid="evidence-loading"
        style={{ marginBottom: 10, fontSize: 11 }}
      >
        正在加载证据…
      </div>
    );
  }
  return null;
}
