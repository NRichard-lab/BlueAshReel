'use client';

import Link from 'next/link';
import { Library, ArrowRight } from 'lucide-react';
import { CatalogState } from '@/components/viewer-catalog';
import { MediaRow } from '@/components/viewer/media-row';
import { DevPlaceholder } from '@/components/viewer/dev-placeholder';
import { MediaCardRecord, useViewerData } from '@/lib/viewer';
import {
  placeholderRows,
  sampleHomeRails,
  sampleLibraries,
} from '@/lib/presentation-placeholders';
import {
  IS_DEV_PREVIEW,
  DevPreviewBanner,
} from '@/components/viewer/dev-preview';

type HomeRails = Record<string, MediaCardRecord[]>;

/** Real rails come straight from GET /browse/home. Placeholder rails are clearly
 *  marked and exist only to show spacing/flow until the Agent provides them. */
const realRails: [key: string, title: string, href: string, empty: string][] = [
  [
    'continue',
    'Continue Watching',
    '/continue',
    'Start a movie or episode and pick up here later.',
  ],
  [
    'recent_movies',
    'Recently Added Movies',
    '/movies',
    'Newly scanned movies will appear here.',
  ],
  [
    'recent_episodes',
    'Recently Added TV',
    '/shows',
    'Newly scanned episodes will appear here.',
  ],
];

export function ViewerHome() {
  const home = useViewerData<HomeRails>('/browse/home');
  const libraries = useViewerData<{
    items: { id: string; name: string }[];
    total: number;
  }>('/browse/libraries?page=1');

  // DEV-only: render the layout with sample data when there is no Agent session.
  const devFallback = IS_DEV_PREVIEW && !!home.error;
  const rails = home.data ?? (devFallback ? sampleHomeRails : undefined);
  const libraryItems =
    libraries.data?.items ?? (devFallback ? sampleLibraries : undefined);
  const showRails = !home.loading && (!home.error || devFallback);

  return (
    <>
      <DevPreviewBanner />
      <div className="mb-9 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            Your private collection
          </p>
          <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
            Something worth staying in for.
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            Movies and television, served from this workstation.
          </p>
        </div>
        <Link
          href="/search"
          className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
        >
          Find something to watch <ArrowRight className="size-4" aria-hidden="true" />
        </Link>
      </div>

      {!devFallback && <CatalogState {...home} empty={false} />}

      {showRails && (
        <>
          {realRails.map(([key, title, href, empty]) => (
            <MediaRow
              key={key}
              title={title}
              href={href}
              items={rails?.[key] ?? []}
              onChanged={home.reload}
              emptyState={empty}
            />
          ))}

          <MyLibrariesRow
            libraries={libraryItems}
            loading={libraries.loading && !devFallback}
          />

          <DevPlaceholder
            className="mb-10"
            note="Recommendations are not generated yet"
          >
            <MediaRow
              title="Recommended for You"
              items={placeholderRows.recommended}
              sample
              className="mb-0"
            />
          </DevPlaceholder>

          <DevPlaceholder note="This row is a layout preview">
            <MediaRow
              title="Because You Watched"
              items={placeholderRows.because_you_watched}
              sample
              className="mb-0"
            />
          </DevPlaceholder>
        </>
      )}
    </>
  );
}

function MyLibrariesRow({
  libraries,
  loading,
}: {
  libraries: { id: string; name: string }[] | undefined;
  loading: boolean;
}) {
  return (
    <section className="mb-10" aria-label="My Libraries">
      <div className="mb-3 flex items-end justify-between gap-4">
        <h2 className="text-lg font-semibold tracking-tight">My Libraries</h2>
        <Link
          href="/settings#libraries"
          className="rounded-md px-2 py-1 text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          Manage
        </Link>
      </div>
      {loading ? (
        <div className="flex gap-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div
              key={i}
              className="h-24 w-52 shrink-0 animate-pulse rounded-xl bg-muted"
            />
          ))}
        </div>
      ) : libraries?.length ? (
        <div className="flex gap-4 overflow-x-auto pb-2 [scrollbar-width:thin]">
          {libraries.map((lib) => (
            <Link
              key={lib.id}
              href={`/movies?library_id=${encodeURIComponent(lib.id)}`}
              className="group flex h-24 w-52 shrink-0 flex-col justify-between rounded-xl border border-border bg-card p-4 outline-none transition-colors hover:border-primary/50 hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Library
                className="size-5 text-primary/70 transition-colors group-hover:text-primary"
                aria-hidden="true"
              />
              <span className="line-clamp-2 text-sm font-medium">{lib.name}</span>
            </Link>
          ))}
        </div>
      ) : (
        <p className="rounded-xl border border-dashed border-border/70 p-6 text-sm text-muted-foreground">
          No libraries yet. An administrator can add and scan a local library.
        </p>
      )}
    </section>
  );
}
