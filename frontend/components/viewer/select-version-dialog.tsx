'use client';

import { useState } from 'react';
import { Check, Layers } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import { placeholderVersions } from '@/lib/presentation-placeholders';

/**
 * Design-only "Select Version" pop-out, modelled on the simplicity of a media
 * server's version picker. Rows are presentation placeholders
 * (lib/presentation-placeholders.ts) — no filesystem paths, no filenames, and
 * choosing a row does not change playback. Real version selection is a later
 * task. The dialog primitive already traps focus and closes on Escape / outside
 * click.
 */
export function SelectVersionDialog({
  open,
  onOpenChange,
  kind,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kind: string;
}) {
  const defaultId =
    placeholderVersions.find((v) => v.isDefault)?.id ?? placeholderVersions[0]?.id;
  const [selected, setSelected] = useState<string | undefined>(defaultId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Layers className="size-4 text-primary" aria-hidden="true" />
            Select Version
          </DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-2">
            <DevBadge label="Example data — not yet connected" />
            <span>
              {kind === 'series'
                ? 'Episodes will list their available files here.'
                : 'Available files for this title will appear here.'}
            </span>
          </DialogDescription>
        </DialogHeader>

        <fieldset className="flex flex-col gap-2">
          <legend className="sr-only">Available versions</legend>
          {placeholderVersions.map((v) => {
            const isSelected = selected === v.id;
            return (
              <label
                key={v.id}
                className={cn(
                  'block w-full cursor-pointer rounded-lg border p-3 text-left transition-colors',
                  'has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-background',
                  isSelected
                    ? 'border-primary bg-primary/10'
                    : 'border-border hover:bg-muted/50',
                )}
              >
                <input
                  type="radio"
                  name="select-version"
                  className="sr-only"
                  checked={isSelected}
                  onChange={() => setSelected(v.id)}
                />
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex items-center gap-2 font-medium">
                      {v.label}
                      {v.isDefault ? (
                        <span className="rounded-full border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Default
                        </span>
                      ) : null}
                    </span>
                    <span
                      className={cn(
                        'grid size-5 shrink-0 place-items-center rounded-full border',
                        isSelected
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-border',
                      )}
                      aria-hidden="true"
                    >
                      {isSelected ? <Check className="size-3" /> : null}
                    </span>
                  </div>
                  <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
                    <Spec term="Resolution" value={v.resolution} />
                    <Spec term="Video" value={v.videoCodec} />
                    <Spec term="Audio" value={v.audioCodec} />
                    <Spec term="Container" value={v.container} />
                    <Spec term="Bitrate" value={v.bitrate} />
                    <Spec term="Size" value={v.size} />
                  </dl>
                  <p className="mt-2 text-xs">
                    <span
                      className={cn(
                        'rounded-full px-2 py-0.5',
                        v.directPlay
                          ? 'bg-[var(--success-soft)] text-[var(--success-foreground)]'
                          : 'bg-muted text-muted-foreground',
                      )}
                    >
                      {v.directPlay
                        ? 'Direct Play compatible'
                        : 'Needs local conversion'}
                    </span>
                  </p>
              </label>
            );
          })}
        </fieldset>

        <DialogFooter showCloseButton>
          <Button type="button" disabled title="Version switching is not implemented yet">
            Use selected version
            <span className="ml-2 text-[10px] uppercase tracking-wide opacity-80">
              Coming later
            </span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Spec({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[10px] uppercase tracking-wide opacity-70">{term}</dt>
      <dd className="text-foreground/90">{value}</dd>
    </div>
  );
}
