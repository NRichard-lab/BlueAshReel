'use client';

import { useState, type ReactNode } from 'react';
import Image from 'next/image';
import { cn } from '@/lib/utils';

/**
 * Cinematic hero area for the movie/episode detail page. Uses the Agent-provided
 * backdrop (`background_url`) when present, with top and bottom gradient scrims
 * so overlaid text stays readable; falls back to a branded gradient otherwise.
 * No external artwork is fetched.
 */
export function HeroBackdrop({
  backgroundUrl,
  children,
  className,
}: {
  backgroundUrl: string | null;
  children: ReactNode;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const showImage = Boolean(backgroundUrl) && !failed;

  return (
    <div
      className={cn(
        'relative isolate -mx-5 overflow-hidden rounded-none border-b border-border/60 md:-mx-10 md:rounded-2xl md:border',
        className,
      )}
    >
      <div className="absolute inset-0 -z-10">
        {showImage ? (
          <Image
            unoptimized
            width={1920}
            height={800}
            src={backgroundUrl as string}
            alt=""
            onError={() => setFailed(true)}
            className="size-full object-cover"
            priority
          />
        ) : (
          <div className="size-full bg-gradient-to-br from-primary/25 via-background to-background" />
        )}
        {/* Scrims: darken top for the back link, bottom for the title block. */}
        <div className="absolute inset-0 bg-gradient-to-t from-background via-background/70 to-background/20" />
        <div className="absolute inset-0 bg-gradient-to-r from-background/80 via-background/30 to-transparent" />
      </div>
      <div className="px-5 pb-6 pt-5 md:px-10 md:pb-10 md:pt-8">{children}</div>
    </div>
  );
}
