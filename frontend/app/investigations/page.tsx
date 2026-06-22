import { Suspense } from 'react';
import InvestigationsPageClient from '@/components/investigation/investigations-page-client';

export default function InvestigationsPage() {
  return (
    <Suspense fallback={null}>
      <InvestigationsPageClient />
    </Suspense>
  );
}
