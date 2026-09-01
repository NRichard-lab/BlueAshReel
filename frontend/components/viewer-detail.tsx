'use client';
import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { Film, Play, RotateCcw, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  NativeSelect,
  NativeSelectOption as Option,
} from '@/components/ui/native-select';
import { MediaCard, CatalogState } from '@/components/viewer-catalog';
import { apiRequest, jsonBody, PageResult } from '@/lib/api';
import {
  MediaCardRecord,
  MediaDetailRecord,
  useViewerData,
  clockTime,
} from '@/lib/viewer';

function SeasonEpisodes({ seasonId }: { seasonId: string }) {
  const [page, setPage] = useState(1);
  const result = useViewerData<PageResult<MediaCardRecord>>(
    `/browse/seasons/${seasonId}/episodes?page=${page}`,
  );
  return (
    <>
      <CatalogState {...result} empty={!result.data?.items.length} />
      <div className="space-y-3">
        {result.data?.items.map((item) => (
          <MediaCard key={item.id} item={item} compact />
        ))}
      </div>
      {(result.data?.total ?? 0) > 30 && (
        <div className="mt-4 flex gap-4">
          <Button disabled={result.loading || page === 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button
            disabled={result.loading || page * 30 >= (result.data?.total ?? 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </>
  );
}
function Seasons({ mediaId }: { mediaId: string }) {
  const [seasonPage, setSeasonPage] = useState(1);
  const result = useViewerData<{
    items: { id: string; season_number: number; episode_count: number }[];
    total: number;
  }>(`/browse/shows/${mediaId}/seasons?page=${seasonPage}`);
  const [selected, setSelected] = useState('');
  const seasonId = selected || result.data?.items[0]?.id;
  return (
    <section className="mt-10">
      <h2 className="mb-4 text-xl font-semibold">Episodes</h2>
      <CatalogState {...result} empty={!result.data?.items.length} />
      {!!result.data?.items.length && !result.error && <NativeSelect
        aria-label="Season"
        value={seasonId ?? ''}
        onChange={(e) => setSelected(e.target.value)}
      >
        {result.data?.items.map((s) => (
          <Option value={s.id} key={s.id}>
            Season {s.season_number} · {s.episode_count} episodes
          </Option>
        ))}
      </NativeSelect>}
      {(result.data?.total ?? 0) > 100 && <div className="mt-3 flex gap-3">
        <Button disabled={result.loading || seasonPage === 1} onClick={() => { setSeasonPage(seasonPage - 1); setSelected(''); }}>Previous seasons</Button>
        <Button disabled={result.loading || seasonPage * 100 >= (result.data?.total ?? 0)} onClick={() => { setSeasonPage(seasonPage + 1); setSelected(''); }}>More seasons</Button>
      </div>}
      <div className="mt-5">
        {seasonId && <SeasonEpisodes key={seasonId} seasonId={seasonId} />}
      </div>
    </section>
  );
}
export function ViewerDetail({ mediaId }: { mediaId: string }) {
  const result = useViewerData<MediaDetailRecord>(`/browse/media/${mediaId}`);
  const next = useViewerData<{ item: MediaCardRecord | null }>(
    `/browse/media/${mediaId}/next`,
  );
  const [message, setMessage] = useState('');
  const [failedPoster, setFailedPoster] = useState<string | null>(null);
  const [failedBackground, setFailedBackground] = useState<string | null>(null);
  const item = result.data;
  const file = item?.files.find((f) => f.available) ?? item?.files[0];
  async function mark() {
    if (!item) return;
    try {
      await apiRequest(`/browse/media/${mediaId}/watched`, {
        method: 'PUT',
        body: jsonBody({ watched: !item.watched }),
      });
      result.reload();
    } catch (e) {
      setMessage(
        e instanceof Error ? e.message : 'Could not save watch status.',
      );
    }
  }
  return (
    <>
      <CatalogState {...result} empty={false} />
      {item && !result.loading && !result.error && (
        <>
          <Link
            href={
              item.show_id
                ? `/watch/${item.show_id}`
                : item.kind === 'series'
                  ? '/shows'
                  : '/movies'
            }
            className="mb-6 inline-block text-sm text-primary"
          >
            ← {item.show_id ? 'Back to show' : 'Back to collection'}
          </Link>
          {item.background_url && failedBackground !== item.background_url && (
            <Image unoptimized width={1600} height={400}
              src={item.background_url}
              alt=""
              onError={() => setFailedBackground(item.background_url)}
              className="mb-7 max-h-64 w-full rounded-xl object-cover"
            />
          )}
          <div className="grid gap-8 sm:grid-cols-[190px_minmax(0,1fr)] lg:grid-cols-[240px_minmax(0,1fr)]">
            <div className="hidden sm:block">
              {item.poster_url && failedPoster !== item.poster_url ? (
                <Image unoptimized width={480} height={720}
                  src={item.poster_url}
                  alt={`${item.title} local poster`}
                  onError={() => setFailedPoster(item.poster_url)}
                  className="aspect-[2/3] w-full rounded-xl object-cover"
                />
              ) : (
                <div className="flex aspect-[2/3] flex-col items-center justify-center gap-4 rounded-xl border bg-card">
                  <Film className="size-12 text-primary/60" />
                  <span className="text-xs text-muted-foreground">
                    No local artwork
                  </span>
                </div>
              )}
            </div>
            <div>
              <p className="mb-2 text-xs uppercase tracking-widest text-primary">
                {item.kind}
                {item.episode_number != null
                  ? ` · Season ${item.season_number}, Episode ${item.episode_number}`
                  : ''}
              </p>
              <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
                {item.title}
              </h1>
              <p className="mt-4 text-sm text-muted-foreground">
                {item.year ?? 'Year not identified'}
                {file?.duration_seconds
                  ? ` · ${clockTime(file.duration_seconds)}`
                  : ''}
                {file?.video[0]
                  ? ` · ${file.video[0].width}×${file.video[0].height} · ${file.video[0].codec?.toUpperCase()}`
                  : ''}
              </p>
              <p className="mt-5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                Details come from your local files. No external metadata is
                requested. Filename-derived titles may be incomplete.
              </p>
              <div className="mt-6 flex flex-wrap items-center gap-3">
                {item.file_id ? (
                  <>
                    <Link
                      href={`/player/${item.id}`}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground"
                    >
                      <Play className="size-4" />
                      {item.position_seconds >= 5 && !item.watched
                        ? `Resume at ${clockTime(item.position_seconds)}`
                        : 'Play'}
                    </Link>
                    <Link
                      href={`/player/${item.id}?restart=1`}
                      className="inline-flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm"
                    >
                      <RotateCcw className="size-4" />
                      Restart
                    </Link>
                  </>
                ) : item.kind !== 'series' ? (
                  <span className="rounded-lg border px-4 py-2 text-sm text-muted-foreground">
                    No available file to play
                  </span>
                ) : null}
                {item.kind !== 'series' && (
                  <Button variant="outline" onClick={mark}>
                    <Check />
                    {item.watched ? 'Mark Unwatched' : 'Mark Watched'}
                  </Button>
                )}
                {next.data?.item && (
                  <Link
                    href={`/player/${next.data.item.id}`}
                    className="rounded-lg border px-4 py-2 text-sm text-primary"
                  >
                    {item.kind === 'series'
                      ? 'Play next unwatched episode'
                      : 'Play next episode'}
                  </Link>
                )}
              </div>
              {message && (
                <p role="alert" className="mt-4 text-sm text-destructive">
                  {message}
                </p>
              )}
              {file && (
                <div className="mt-8 grid gap-5 md:grid-cols-2">
                  <div>
                    <h2 className="text-sm font-semibold">Audio tracks</h2>
                    <ul className="mt-2 space-y-2 text-sm text-muted-foreground">
                      {file.audio.map((a) => (
                        <li key={a.index}>
                          {a.title || `Track ${a.index}`} ·{' '}
                          {a.language || 'Language unknown'} · {a.codec} ·{' '}
                          {a.channels} channels
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h2 className="text-sm font-semibold">Subtitles</h2>
                    <ul className="mt-2 space-y-2 text-sm text-muted-foreground">
                      {file.subtitles.map((s) => (
                        <li key={s.index}>
                          {s.title || `Track ${s.index}`} ·{' '}
                          {s.language || 'Language unknown'} · {s.codec}
                          {!s.text_supported
                            ? ' · local conversion required'
                            : ''}
                        </li>
                      ))}
                    </ul>
                    {!file.subtitles.length && (
                      <p className="mt-2 text-sm text-muted-foreground">
                        No embedded subtitles identified
                      </p>
                    )}
                  </div>
                </div>
              )}
              {file?.size_bytes != null && (
                <details className="mt-6 rounded-lg border p-4 text-sm">
                  <summary className="cursor-pointer">
                    Owner technical information
                  </summary>
                  <p className="mt-3 text-muted-foreground">
                    {(file.size_bytes / 1048576).toFixed(1)} MiB ·{' '}
                    {file.container} ·{' '}
                    {file.bitrate
                      ? `${Math.round(file.bitrate / 1000)} kbps`
                      : 'Bitrate unknown'}
                  </p>
                  <p className="mt-2 text-muted-foreground">
                    {file.video[0]?.profile ?? 'Profile unknown'} ·{' '}
                    {file.video[0]?.pixel_format ?? 'Pixel format unknown'}
                  </p>
                </details>
              )}
            </div>
          </div>
          {item.kind === 'series' && <Seasons mediaId={item.id} />}
        </>
      )}
    </>
  );
}
