import { Suspense } from 'react';
import OverviewPageClient from '@/components/overview/overview-page-client';

export default function OverviewPage() {
  return (
    <Suspense fallback={null}>
      <OverviewPageClient />
    </Suspense>
  );
}
