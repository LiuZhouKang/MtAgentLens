import { Suspense } from 'react';
import ClustersPageClient from '@/components/cluster/clusters-page-client';

export default function ClustersPage() {
  return (
    <Suspense fallback={null}>
      <ClustersPageClient />
    </Suspense>
  );
}
