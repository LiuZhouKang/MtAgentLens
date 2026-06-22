import { Suspense } from 'react';
import AgentsPageClient from '@/components/agent/agents-page-client';

export default function AgentsPage() {
  return (
    <Suspense fallback={null}>
      <AgentsPageClient />
    </Suspense>
  );
}
