'use client';

import dynamic from 'next/dynamic';
import { Suspense } from 'react';
import PageSkeleton from '@/components/ui/page-skeleton';

const InvestigationsPageClient = dynamic(
  () => import('@/components/investigation/investigations-page-client'),
  { ssr: false },
);

export default function InvestigationsPage() {
  return (
    <Suspense fallback={<PageSkeleton label="加载告警" />}>
      <InvestigationsPageClient />
    </Suspense>
  );
}
