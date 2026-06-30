import { Suspense } from 'react';
import TracesPageClient from '@/components/traces/traces-page-client';

export default function TracesPage() {
  return (
    <Suspense fallback={null}>
      <TracesPageClient />
    </Suspense>
  );
}
