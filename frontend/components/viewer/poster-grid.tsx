import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** Responsive, aspect-locked poster grid for the Movies / TV Shows pages. */
export function PosterGrid({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-7',
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Placeholder tiles shown while a page of posters is loading. */
export function PosterGridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <PosterGrid>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="w-full">
          <div className="aspect-[2/3] w-full animate-pulse rounded-xl bg-muted" />
          <div className="mt-2 h-3 w-3/4 animate-pulse rounded bg-muted" />
          <div className="mt-1.5 h-2.5 w-1/2 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </PosterGrid>
  );
}
