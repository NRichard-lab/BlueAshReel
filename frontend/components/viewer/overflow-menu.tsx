'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  MoreHorizontal,
  RotateCcw,
  Check,
  Layers,
  Captions,
  Info,
  XCircle,
} from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuGroup,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { apiRequest, jsonBody } from '@/lib/api';
import { SelectVersionDialog } from '@/components/viewer/select-version-dialog';

/**
 * Secondary-actions pop-out for a media item.
 *
 * Wired (real) actions: Play from Beginning, Mark Watched / Unwatched,
 * Audio & Subtitle Options (opens the player), View Information.
 * Placeholder actions are disabled and labelled "Coming later"; Select Version
 * opens a design-only pop-out. Nothing here adds backend behaviour and no fake
 * success messages are shown. The Menu primitive already closes on Escape,
 * outside click and item selection, and manages focus.
 */
export function OverflowMenu({
  mediaId,
  watched,
  canPlay,
  kind,
  onChanged,
  triggerClassName,
  triggerLabel = 'More options',
  compact = true,
}: {
  mediaId: string;
  watched: boolean;
  canPlay: boolean;
  kind: string;
  /** Called after a real mutation so the caller can refresh its data. */
  onChanged?: () => void;
  triggerClassName?: string;
  triggerLabel?: string;
  compact?: boolean;
}) {
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [pending, setPending] = useState(false);

  async function toggleWatched() {
    if (pending) return;
    setPending(true);
    try {
      await apiRequest(`/browse/media/${mediaId}/watched`, {
        method: 'PUT',
        body: jsonBody({ watched: !watched }),
      });
      onChanged?.();
    } catch {
      // No fake toast. The detail page surfaces watch-status errors inline;
      // from a card the next data refresh reflects the real state.
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            compact ? (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={triggerLabel}
                className={triggerClassName}
              />
            ) : (
              <Button
                variant="outline"
                aria-label={triggerLabel}
                className={triggerClassName}
              />
            )
          }
        >
          <MoreHorizontal aria-hidden="true" />
          {compact ? null : <span>More</span>}
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-56">
          {canPlay ? (
            <DropdownMenuItem
              render={<Link href={`/player/${mediaId}?restart=1`} />}
            >
              <RotateCcw aria-hidden="true" />
              Play from Beginning
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem
            onClick={() => void toggleWatched()}
            disabled={pending}
          >
            <Check aria-hidden="true" />
            {watched ? 'Mark Unwatched' : 'Mark Watched'}
          </DropdownMenuItem>

          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>Playback</DropdownMenuLabel>
            <DropdownMenuItem onClick={() => setVersionsOpen(true)}>
              <Layers aria-hidden="true" />
              Select Version
              <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">
                Preview
              </span>
            </DropdownMenuItem>
            {canPlay ? (
              <DropdownMenuItem render={<Link href={`/player/${mediaId}`} />}>
                <Captions aria-hidden="true" />
                Audio &amp; Subtitle Options
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem disabled>
                <Captions aria-hidden="true" />
                Audio &amp; Subtitle Options
                <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">
                  Coming later
                </span>
              </DropdownMenuItem>
            )}
          </DropdownMenuGroup>

          <DropdownMenuSeparator />
          <DropdownMenuItem render={<Link href={`/watch/${mediaId}`} />}>
            <Info aria-hidden="true" />
            View Information
          </DropdownMenuItem>
          <DropdownMenuItem disabled>
            <XCircle aria-hidden="true" />
            Remove from Continue Watching
            <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">
              Coming later
            </span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <SelectVersionDialog
        open={versionsOpen}
        onOpenChange={setVersionsOpen}
        kind={kind}
      />
    </>
  );
}
