import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { DevPlaceholder } from '@/components/viewer/dev-placeholder';
import { placeholderCast } from '@/lib/presentation-placeholders';

function initials(name: string): string {
  return name
    .split(' ')
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
}

/**
 * Cast & crew strip for the detail page. Presentation-only until the Agent
 * exposes credits — see lib/presentation-placeholders.ts.
 */
export function CastRow() {
  return (
    <section className="mt-10" aria-label="Cast and crew">
      <h2 className="mb-3 text-lg font-semibold tracking-tight">Cast &amp; Crew</h2>
      <DevPlaceholder note="Credits are not yet provided by the Agent">
        <ul className="flex gap-5 overflow-x-auto pb-1 [scrollbar-width:thin]">
          {placeholderCast.map((person) => (
            <li
              key={person.name}
              className="flex w-24 shrink-0 flex-col items-center text-center"
            >
              <Avatar size="lg" className="size-16">
                <AvatarFallback>{initials(person.name)}</AvatarFallback>
              </Avatar>
              <span className="mt-2 line-clamp-2 text-xs font-medium">
                {person.name}
              </span>
              <span className="line-clamp-1 text-[11px] text-muted-foreground">
                {person.role}
              </span>
            </li>
          ))}
        </ul>
      </DevPlaceholder>
    </section>
  );
}
