'use client';

import dynamic from 'next/dynamic';
import { Suspense } from 'react';
import PageSkeleton from '@/components/ui/page-skeleton';

const OverviewPageClient = dynamic(() => import('@/components/overview/overview-page-client'), {
  ssr: false,
});

export default function OverviewPage() {
  return (
    <Suspense fallback={<PageSkeleton label="加载仪表盘" />}>
      <OverviewPageClient />
    </Suspense>
  );
}
