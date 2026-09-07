'use client';

import Link from 'next/link';
import { Play, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { buttonVariants } from '@/components/ui/button';
import { clockTime } from '@/lib/viewer';
import { OverflowMenu } from '@/components/viewer/overflow-menu';

/**
 * Detail-page primary action area. Order of prominence:
 *   1. Resume  (when watch progress exists)
 *   2. Play    (no progress)
 *   3. Restart from Beginning (secondary, when a file is playable)
 *   4. More options (overflow pop-out)
 * Resume and Play are never shown as co-equal. The links reuse the existing
 * /player/:id route and its unchanged playback handler.
 */
export function PrimaryActions({
  mediaId,
  canPlay,
  positionSeconds,
  watched,
  kind,
  onChanged,
}: {
  mediaId: string;
  canPlay: boolean;
  positionSeconds: number;
  watched: boolean;
  kind: string;
  onChanged?: () => void;
}) {
  const hasProgress = positionSeconds >= 5 && !watched;

  return (
    <div className="flex flex-wrap items-center gap-3">
      {canPlay ? (
        <>
          {hasProgress ? (
            <Link
              href={`/player/${mediaId}`}
              className={cn(buttonVariants({ size: 'lg' }), 'h-11 px-6 text-sm')}
            >
              <Play className="size-4" aria-hidden="true" />
              Resume · {clockTime(positionSeconds)}
            </Link>
          ) : (
            <Link
              href={`/player/${mediaId}`}
              className={cn(buttonVariants({ size: 'lg' }), 'h-11 px-6 text-sm')}
            >
              <Play className="size-4" aria-hidden="true" />
              Play
            </Link>
          )}

          {hasProgress ? (
            <Link
              href={`/player/${mediaId}?restart=1`}
              className={cn(
                buttonVariants({ variant: 'outline', size: 'lg' }),
                'h-11 px-4 text-sm',
              )}
            >
              <RotateCcw className="size-4" aria-hidden="true" />
              Restart from Beginning
            </Link>
          ) : null}
        </>
      ) : (
        <span className="rounded-lg border border-border px-4 py-2.5 text-sm text-muted-foreground">
          {kind === 'series'
            ? 'Choose an episode to play'
            : 'No available file to play'}
        </span>
      )}

      <OverflowMenu
        mediaId={mediaId}
        watched={watched}
        canPlay={canPlay}
        kind={kind}
        onChanged={onChanged}
        compact={false}
      />
    </div>
  );
}
