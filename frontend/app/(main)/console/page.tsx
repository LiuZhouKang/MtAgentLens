'use client';

import dynamic from 'next/dynamic';
import { Suspense } from 'react';
import PageSkeleton from '@/components/ui/page-skeleton';

const CommandCenterPage = dynamic(() => import('@/components/command-center/command-center-page'), {
  ssr: false,
});

export default function Home() {
  return (
    <Suspense fallback={<PageSkeleton label="加载态势" />}>
      <CommandCenterPage />
    </Suspense>
  );
}
