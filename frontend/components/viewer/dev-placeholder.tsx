import type { ReactNode } from 'react';
import { FlaskConical } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Wraps any area that renders presentation-only placeholder content so it can
 * never be mistaken for verified Agent data. See lib/presentation-placeholders.ts.
 */
export function DevPlaceholder({
  children,
  label = 'Sample content',
  note,
  className,
  inline = false,
}: {
  children: ReactNode;
  label?: string;
  note?: string;
  className?: string;
  /** Render just the badge inline (no bordered container). */
  inline?: boolean;
}) {
  if (inline) {
    return (
      <span className={cn('inline-flex items-center gap-2', className)}>
        <DevBadge label={label} />
        {children}
      </span>
    );
  }
  return (
    <div
      className={cn(
        'rounded-xl border border-dashed border-border/70 bg-muted/20 p-4',
        className,
      )}
      data-dev-placeholder="true"
    >
      <div className="mb-3 flex items-center gap-2">
        <DevBadge label={label} />
        {note ? (
          <span className="text-xs text-muted-foreground">{note}</span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

export function DevBadge({ label = 'Sample content' }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border/70 bg-background/60 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
      <FlaskConical className="size-3" aria-hidden="true" />
      {label}
    </span>
  );
}
