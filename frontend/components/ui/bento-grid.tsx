'use client';

import { cn } from '@/lib/utils';
import { Spotlight } from '@/components/motion-primitives/spotlight';

export function BentoGrid({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        'mx-auto grid w-full max-w-7xl auto-rows-[minmax(10rem,auto)] grid-cols-1 gap-6 md:grid-cols-3 lg:grid-cols-4',
        className,
      )}
    >
      {children}
    </div>
  );
}

export function BentoCard({
  className,
  children,
  colSpan = '',
  rowSpan = '',
}: {
  className?: string;
  children: React.ReactNode;
  colSpan?: string;
  rowSpan?: string;
}) {
  return (
    <div
      className={cn(
        'group relative flex flex-col justify-between overflow-hidden rounded-[32px] border border-white/5 bg-[#1C1C1E]/60 p-8 shadow-[0_8px_32px_-8px_rgba(0,0,0,0.5)] saturate-[150%] backdrop-blur-[40px] transition-all duration-300 hover:border-white/10 hover:bg-[#1C1C1E]/80 hover:shadow-[0_20px_40px_-10px_rgba(0,0,0,0.7)]',
        colSpan,
        rowSpan,
        className,
      )}
    >
      <Spotlight className="-z-10 from-white/20 via-white/5 to-transparent" size={400} />
      {children}
    </div>
  );
}
