'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { Film, Check, Play, List, Grid2X2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  NativeSelect,
  NativeSelectOption as Option,
} from '@/components/ui/native-select';
import { PageResult } from '@/lib/api';
import { MediaCardRecord, useViewerData, clockTime } from '@/lib/viewer';

export function MediaCard({
  item,
  compact = false,
}: {
  item: MediaCardRecord;
  compact?: boolean;
}) {
  const [failedArtwork, setFailedArtwork] = useState<string | null>(null);
  return (
    <article
      className={`min-w-0 ${compact ? 'flex items-center gap-5 rounded-xl border bg-card p-3' : ''}`}
    >
      <Link
        href={`/watch/${item.id}`}
        className={`group relative block overflow-hidden rounded-xl border border-border bg-card ${compact ? 'w-16 shrink-0' : 'w-full'}`}
        aria-label={`Details for ${item.title}`}
      >
        {item.poster_url && failedArtwork !== item.poster_url ? (
          <Image unoptimized width={480} height={720}
            src={item.poster_url}
            alt=""
            loading="lazy"
            onError={() => setFailedArtwork(item.poster_url)}
            className="aspect-[2/3] w-full object-cover"
          />
        ) : (
          <div className="flex aspect-[2/3] flex-col items-center justify-center gap-3 p-3 text-center">
            <Film
              className={`${compact ? 'size-5' : 'size-10'} text-primary/60`}
              aria-hidden="true"
            />
            {!compact && (
              <span className="text-[11px] text-muted-foreground">
                No local artwork
              </span>
            )}
          </div>
        )}
        {item.watched && (
          <span
            className="absolute right-2 top-2 rounded-full bg-primary p-1 text-primary-foreground"
            aria-label="Watched"
          >
            <Check className="size-3" />
          </span>
        )}
        {!item.watched && item.completion > 0 && (
          <div className="absolute bottom-0 h-1 w-full bg-black/50">
            <div
              className="h-full bg-primary"
              style={{ width: `${item.completion}%` }}
            />
          </div>
        )}
      </Link>
      <div className={`min-w-0 ${compact ? 'flex-1' : 'mt-3'}`}>
        <Link
          href={`/watch/${item.id}`}
          className="line-clamp-2 text-sm font-medium hover:text-primary"
        >
          {item.title}
        </Link>
        <p className="mt-1 text-xs text-muted-foreground">
          {item.season_number != null
            ? `S${String(item.season_number).padStart(2, '0')} E${String(item.episode_number).padStart(2, '0')} · `
            : ''}
          {item.year ? `${item.year} · ` : ''}
          {item.duration_seconds
            ? clockTime(item.duration_seconds)
            : item.kind === 'series'
              ? 'TV show'
              : 'Duration unknown'}
          {item.height ? ` · ${item.height}p` : ''}
        </p>
        {item.kind !== 'series' && (
          <p className="mt-2 text-xs">
            {item.available ? (
              <Link
                href={`/player/${item.id}`}
                className="inline-flex items-center gap-1 text-primary"
              >
                <Play className="size-3" />
                {item.position_seconds >= 5 && !item.watched
                  ? `Resume ${clockTime(item.position_seconds)}`
                  : 'Play'}
              </Link>
            ) : (
              <span className="text-muted-foreground">Unavailable</span>
            )}
          </p>
        )}
      </div>
    </article>
  );
}

export function CatalogState({
  loading,
  error,
  empty,
  reload,
}: {
  loading: boolean;
  error: string;
  empty: boolean;
  reload: () => void;
}) {
  if (error)
    return (
      <div role="alert" className="rounded-xl border border-destructive/40 p-5">
        <p>{error}</p>
        <Button className="mt-3" onClick={reload}>
          Try again
        </Button>
      </div>
    );
  if (loading)
    return (
      <output className="block py-12 text-muted-foreground">
        Loading your collection…
      </output>
    );
  if (empty)
    return (
      <div className="rounded-xl border border-dashed p-10 text-center">
        <Film className="mx-auto mb-4 size-8 text-primary" />
        <h2 className="font-medium">Nothing here yet</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Try different filters, or ask an administrator to assign and scan a
          library.
        </p>
      </div>
    );
  return null;
}

export function ViewerCatalog({
  mode,
}: {
  mode: 'movies' | 'shows' | 'search' | 'continue';
}) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('title');
  const [library, setLibrary] = useState('');
  const [watched, setWatched] = useState('');
  const [availability, setAvailability] = useState('');
  const [resolution, setResolution] = useState('');
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query);
      setPage(1);
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);
  const params = new URLSearchParams({
    page: String(page),
    page_size: '24',
    sort,
  });
  if (mode === 'movies') params.set('kind', 'movie');
  if (mode === 'shows') params.set('kind', 'series');
  if (mode === 'continue') params.set('history', 'continue');
  if (search) params.set('q', search);
  if (library) params.set('library_id', library);
  if (watched) params.set('watched', watched);
  if (availability) params.set('available', availability);
  if (resolution) params.set('resolution', resolution);
  const result = useViewerData<PageResult<MediaCardRecord>>(
    `/browse/media?${params}`,
  );
  const [libraryPage, setLibraryPage] = useState(1);
  const libraries = useViewerData<{ items: { id: string; name: string }[]; total: number }>(
    `/browse/libraries?page=${libraryPage}`,
  );
  const title = {
    movies: 'Movies',
    shows: 'TV Shows',
    search: 'Search your collection',
    continue: 'Continue Watching',
  }[mode];
  function filter(set: (value: string) => void, value: string) {
    set(value);
    setPage(1);
  }
  return (
    <>
      <div className="mb-7 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {result.data
              ? `${result.data.total} items in your libraries`
              : 'Your own collection. No outside recommendations.'}
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => setCompact(!compact)}
          aria-label={compact ? 'Use poster grid' : 'Use compact list'}
        >
          {compact ? <Grid2X2 /> : <List />}
        </Button>
      </div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Input
          className="max-w-sm"
          aria-label="Search media"
          placeholder="Title, year, or S01E02…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <NativeSelect
          aria-label="Sort media"
          value={sort}
          onChange={(e) => filter(setSort, e.target.value)}
        >
          <Option value="title">Title</Option>
          <Option value="year">Year</Option>
          <Option value="added">Recently added</Option>
          <Option value="duration">Duration</Option>
          <Option value="watch">Watch status</Option>
        </NativeSelect>
        <NativeSelect
          aria-label="Filter library"
          value={library}
          onChange={(e) => filter(setLibrary, e.target.value)}
        >
          <Option value="">All libraries</Option>
          {libraries.data?.items.map((l) => (
            <Option key={l.id} value={l.id}>
              {l.name}
            </Option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Filter watched status"
          value={watched}
          onChange={(e) => filter(setWatched, e.target.value)}
        >
          <Option value="">All watch states</Option>
          <Option value="false">Unwatched</Option>
          <Option value="true">Watched</Option>
        </NativeSelect>
        {mode !== 'shows' && (
          <>
            <NativeSelect
              aria-label="Filter availability"
              value={availability}
              onChange={(e) => filter(setAvailability, e.target.value)}
            >
              <Option value="">Any availability</Option>
              <Option value="true">Available</Option>
              <Option value="false">Unavailable</Option>
            </NativeSelect>
            <NativeSelect
              aria-label="Minimum resolution"
              value={resolution}
              onChange={(e) => filter(setResolution, e.target.value)}
            >
              <Option value="">Any resolution</Option>
              <Option value="720">720p+</Option>
              <Option value="1080">1080p+</Option>
              <Option value="2160">4K</Option>
            </NativeSelect>
          </>
        )}
      </div>
      <CatalogState {...result} empty={!result.data?.items.length} />
      {(libraries.data?.total ?? 0) > 100 && <div className="mb-4 flex gap-3 text-sm">
        <Button variant="outline" disabled={libraryPage === 1} onClick={() => { setLibraryPage(libraryPage - 1); filter(setLibrary, ''); }}>Previous library choices</Button>
        <Button variant="outline" disabled={libraryPage * 100 >= (libraries.data?.total ?? 0)} onClick={() => { setLibraryPage(libraryPage + 1); filter(setLibrary, ''); }}>More library choices</Button>
      </div>}
      {!result.loading && !result.error && (
        <div
          className={
            compact
              ? 'space-y-3'
              : 'grid grid-cols-2 gap-x-5 gap-y-8 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6'
          }
        >
          {result.data?.items.map((item) => (
            <MediaCard key={item.id} item={item} compact={compact} />
          ))}
        </div>
      )}
      {(result.data?.total ?? 0) > 24 && (
        <div className="mt-8 flex items-center justify-center gap-5">
          <Button
            variant="outline"
            disabled={page === 1 || result.loading}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </Button>
          <span className="text-sm">
            Page {page} of {Math.ceil((result.data?.total ?? 0) / 24)}
          </span>
          <Button
            variant="outline"
            disabled={page * 24 >= (result.data?.total ?? 0) || result.loading}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </>
  );
}
