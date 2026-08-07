'use client';

import dynamic from 'next/dynamic';
import { Suspense } from 'react';
import PageSkeleton from '@/components/ui/page-skeleton';

const TracesPageClient = dynamic(() => import('@/components/traces/traces-page-client'), { ssr: false });

export default function TracesPage() {
  return (
    <Suspense fallback={<PageSkeleton label="加载追踪" />}>
      <TracesPageClient />
    </Suspense>
  );
}
