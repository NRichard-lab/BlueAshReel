'use client';

import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { Film, Play, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { MediaCardRecord, clockTime } from '@/lib/viewer';
import { OverflowMenu } from '@/components/viewer/overflow-menu';
import { DevBadge } from '@/components/viewer/dev-placeholder';

function secondaryMeta(item: MediaCardRecord): string {
  const parts: string[] = [];
  if (item.season_number != null) {
    parts.push(
      `S${String(item.season_number).padStart(2, '0')}E${String(item.episode_number ?? 0).padStart(2, '0')}`,
    );
  }
  if (item.year) parts.push(String(item.year));
  if (item.duration_seconds) parts.push(clockTime(item.duration_seconds));
  else if (item.kind === 'series') parts.push('Series');
  return parts.join(' · ');
}

/**
 * Artwork-first media card used by Home rows and the Movies / TV Shows grids.
 * Real bindings preserved: navigates to /watch/:id, inline Play/Resume links to
 * /player/:id, watched state and watch-progress come straight from the record.
 * `sample` cards are non-interactive and visibly marked (placeholder rows only).
 */
export function PosterCard({
  item,
  sample = false,
  onChanged,
  className,
}: {
  item: MediaCardRecord;
  sample?: boolean;
  onChanged?: () => void;
  className?: string;
}) {
  const [failedArtwork, setFailedArtwork] = useState(false);
  const showArt = Boolean(item.poster_url) && !failedArtwork;
  const resumeSeconds =
    item.position_seconds >= 5 && !item.watched ? item.position_seconds : 0;
  const canPlay = item.kind !== 'series' && item.available && !!item.file_id;

  const poster = (
    <div className="group/poster relative overflow-hidden rounded-xl border border-border bg-card shadow-sm ring-0 transition-all duration-200 group-hover/card:-translate-y-0.5 group-hover/card:shadow-lg group-focus-visible/card:ring-2 group-focus-visible/card:ring-ring">
      {showArt ? (
        <Image
          unoptimized
          width={360}
          height={540}
          src={item.poster_url as string}
          alt=""
          loading="lazy"
          onError={() => setFailedArtwork(true)}
          className="aspect-[2/3] w-full object-cover"
        />
      ) : (
        <div className="flex aspect-[2/3] w-full flex-col items-center justify-center gap-2 bg-gradient-to-b from-muted/40 to-muted/10 p-3 text-center">
          <Film className="size-8 text-primary/50" aria-hidden="true" />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            No artwork
          </span>
        </div>
      )}

      {item.watched && (
        <span
          className="absolute right-2 top-2 grid size-6 place-items-center rounded-full bg-primary text-primary-foreground shadow"
          aria-label="Watched"
        >
          <Check className="size-3.5" aria-hidden="true" />
        </span>
      )}

      {!item.watched && item.completion > 0 && (
        <div className="absolute inset-x-0 bottom-0 h-1 bg-black/50">
          <div
            className="h-full bg-primary"
            style={{ width: `${Math.min(100, Math.max(0, item.completion))}%` }}
          />
        </div>
      )}

      {sample && (
        <div className="absolute inset-x-2 bottom-2">
          <DevBadge />
        </div>
      )}
    </div>
  );

  if (sample) {
    return (
      <article
        className={cn('w-full min-w-0 select-none opacity-90', className)}
        aria-hidden="true"
      >
        {poster}
        <p className="mt-2 line-clamp-1 text-sm font-medium text-muted-foreground">
          {item.title}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground/80">
          {secondaryMeta(item)}
        </p>
      </article>
    );
  }

  return (
    <article className={cn('group/card w-full min-w-0', className)}>
      <div className="relative">
        <Link
          href={`/watch/${item.id}`}
          className="block rounded-xl outline-none"
          aria-label={item.title}
        >
          {poster}
        </Link>
        <div className="absolute right-1.5 top-1.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover/card:opacity-100">
          <div className="rounded-md bg-background/80 backdrop-blur">
            <OverflowMenu
              mediaId={item.id}
              watched={item.watched}
              canPlay={canPlay}
              kind={item.kind}
              onChanged={onChanged}
              triggerLabel={`More options for ${item.title}`}
            />
          </div>
        </div>
      </div>

      <div className="mt-2 min-w-0">
        <Link
          href={`/watch/${item.id}`}
          className="line-clamp-1 text-sm font-medium outline-none hover:text-primary focus-visible:text-primary"
        >
          {item.title}
        </Link>
        <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
          {secondaryMeta(item)}
        </p>
        {canPlay && (
          <Link
            href={`/player/${item.id}`}
            className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-primary outline-none hover:underline focus-visible:underline"
          >
            <Play className="size-3" aria-hidden="true" />
            {resumeSeconds ? `Resume ${clockTime(resumeSeconds)}` : 'Play'}
          </Link>
        )}
      </div>
    </article>
  );
}
