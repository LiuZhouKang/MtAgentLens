'use client';

import LoadingIndicator from './loading-indicator';

/**
 * Suspense fallback for the dynamic page mounts — a centered loading orbit so
 * the JS-chunk boundary is never a blank flash.
 */
export default function PageSkeleton({ label = '加载中' }: { label?: string }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '60vh',
      }}
    >
      <LoadingIndicator size={40} label={label} />
    </div>
  );
}
