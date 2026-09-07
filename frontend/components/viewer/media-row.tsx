'use client';

import { useRef, type ReactNode } from 'react';
import Link from 'next/link';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { MediaCardRecord } from '@/lib/viewer';
import { PosterCard } from '@/components/viewer/poster-card';

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * A horizontally scrollable row of media cards. Used for Home rails and the
 * detail-page "Related titles" row. The track scrolls with the wheel, touch,
 * the arrow buttons, or the keyboard (it is a focusable region); each card is a
 * normal focusable link.
 */
export function MediaRow({
  title,
  href,
  items,
  onChanged,
  sample = false,
  emptyState,
  className,
}: {
  title: string;
  href?: string;
  items: MediaCardRecord[] | undefined;
  onChanged?: () => void;
  sample?: boolean;
  emptyState?: ReactNode;
  className?: string;
}) {
  const trackRef = useRef<HTMLDivElement>(null);

  function nudge(direction: 1 | -1) {
    const track = trackRef.current;
    if (!track) return;
    track.scrollBy({
      left: direction * Math.round(track.clientWidth * 0.9),
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
    });
  }

  const hasItems = !!items?.length;

  return (
    <section className={cn('mb-10', className)} aria-label={title}>
      <div className="mb-3 flex items-end justify-between gap-4">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        <div className="flex items-center gap-1">
          {href && hasItems && !sample ? (
            <Link
              href={href}
              className="rounded-md px-2 py-1 text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            >
              View all
            </Link>
          ) : null}
          {hasItems ? (
            <div className="hidden sm:flex">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Scroll ${title} left`}
                onClick={() => nudge(-1)}
              >
                <ChevronLeft aria-hidden="true" />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Scroll ${title} right`}
                onClick={() => nudge(1)}
              >
                <ChevronRight aria-hidden="true" />
              </Button>
            </div>
          ) : null}
        </div>
      </div>

      {hasItems ? (
        <div
          ref={trackRef}
          className="-mx-1 flex snap-x snap-mandatory gap-4 overflow-x-auto scroll-pl-1 px-1 pb-2 [scrollbar-width:thin]"
        >
          {items!.map((item) => (
            <div
              key={item.id}
              className="w-[44vw] shrink-0 snap-start sm:w-[168px] lg:w-[184px]"
            >
              <PosterCard item={item} sample={sample} onChanged={onChanged} />
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-border/70 p-6 text-sm text-muted-foreground">
          {emptyState ?? 'Nothing here yet.'}
        </div>
      )}
    </section>
  );
}
