'use client';

import Link from 'next/link';
import { ArrowRight } from 'lucide-react';
import { MediaCard, CatalogState } from '@/components/viewer-catalog';
import { MediaCardRecord, useViewerData } from '@/lib/viewer';

const rails = [
  ['continue', 'Continue Watching', '/continue'],
  ['recent_movies', 'Recently Added Movies', '/movies'],
  ['recent_episodes', 'Recently Added Episodes', '/shows'],
  ['movies', 'Movies', '/movies'],
  ['shows', 'TV Shows', '/shows'],
  ['recent_watched', 'Recently Watched', '/profile'],
];

export function ViewerHome() {
  const result =
    useViewerData<Record<string, MediaCardRecord[]>>('/browse/home');
  return (
    <>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs uppercase tracking-[.18em] text-primary">
            Your private collection
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">
            Something worth staying in for.
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            Movies and television, right here at home.
          </p>
        </div>
        <Link
          href="/search"
          className="flex items-center gap-2 rounded-lg border px-4 py-2 text-sm"
        >
          Find something to watch <ArrowRight className="size-4" />
        </Link>
      </div>
      <CatalogState {...result} empty={false} />
      {!result.loading &&
        !result.error &&
        rails.map(([key, title, href]) => (
          <section key={key} className="mb-10" aria-label={title}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold">{title}</h2>
              <Link href={href} className="text-xs text-primary">
                View all →
              </Link>
            </div>
            {result.data?.[key]?.length ? (
              <div className="grid grid-cols-2 gap-5 sm:grid-cols-4 xl:grid-cols-8">
                {result.data[key].map((item) => (
                  <MediaCard item={item} key={item.id} />
                ))}
              </div>
            ) : (
              <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
                {key === 'continue'
                  ? 'Start a movie or episode and pick up here later.'
                  : key === 'recent_watched'
                    ? 'Your viewing activity stays on this server.'
                    : 'No items yet. Ask an administrator to assign and scan a local library.'}
              </p>
            )}
          </section>
        ))}
    </>
  );
}
