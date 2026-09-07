'use client';
import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { Film, SkipForward, Star, Clock, Calendar } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  NativeSelect,
  NativeSelectOption as Option,
} from '@/components/ui/native-select';
import { MediaCard, CatalogState } from '@/components/viewer-catalog';
import { PageResult } from '@/lib/api';
import {
  MediaCardRecord,
  MediaDetailRecord,
  MediaFileRecord,
  useViewerData,
  clockTime,
} from '@/lib/viewer';
import { HeroBackdrop } from '@/components/viewer/hero-backdrop';
import { PrimaryActions } from '@/components/viewer/primary-actions';
import { CastRow } from '@/components/viewer/cast-row';
import { MediaRow } from '@/components/viewer/media-row';
import { DevPlaceholder, DevBadge } from '@/components/viewer/dev-placeholder';
import {
  placeholderDetailMeta,
  placeholderRows,
  sampleDetail,
} from '@/lib/presentation-placeholders';
import {
  IS_DEV_PREVIEW,
  DevPreviewBanner,
} from '@/components/viewer/dev-preview';

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
      <h2 className="mb-4 text-lg font-semibold tracking-tight">Episodes</h2>
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

function Poster({ url, title }: { url: string | null; title: string }) {
  const [failed, setFailed] = useState(false);
  if (url && !failed) {
    return (
      <Image
        unoptimized
        width={420}
        height={630}
        src={url}
        alt={`${title} poster`}
        onError={() => setFailed(true)}
        className="aspect-[2/3] w-full rounded-xl border border-border object-cover shadow-xl"
      />
    );
  }
  return (
    <div className="flex aspect-[2/3] w-full flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card shadow-xl">
      <Film className="size-12 text-primary/60" aria-hidden="true" />
      <span className="text-xs text-muted-foreground">No local artwork</span>
    </div>
  );
}

function StreamSummary({ file }: { file: MediaFileRecord }) {
  const v = file.video[0];
  const audio = file.audio[0];
  return (
    <section className="mt-8">
      <h2 className="mb-3 text-lg font-semibold tracking-tight">This file</h2>
      <div className="grid gap-4 sm:grid-cols-3">
        <SummaryCard label="Video">
          {v
            ? `${v.width}×${v.height} · ${v.codec?.toUpperCase() ?? 'unknown'}`
            : 'No video stream identified'}
          {file.duration_seconds ? ` · ${clockTime(file.duration_seconds)}` : ''}
        </SummaryCard>
        <SummaryCard label="Audio">
          {file.audio.length
            ? `${file.audio.length} track${file.audio.length > 1 ? 's' : ''}` +
              (audio
                ? ` · ${audio.language || 'lang unknown'} · ${audio.codec} · ${audio.channels}ch`
                : '')
            : 'No audio stream identified'}
        </SummaryCard>
        <SummaryCard label="Subtitles">
          {file.subtitles.length
            ? `${file.subtitles.length} embedded · ${file.subtitles
                .map((s) => s.language || 'und')
                .slice(0, 3)
                .join(', ')}`
            : 'No embedded subtitles identified'}
        </SummaryCard>
      </div>
      {file.size_bytes != null && (
        <details className="mt-4 rounded-lg border border-border p-4 text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Technical details
          </summary>
          <p className="mt-3 text-muted-foreground">
            {(file.size_bytes / 1048576).toFixed(1)} MiB · {file.container} ·{' '}
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
    </section>
  );
}

function SummaryCard({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1.5 text-sm">{children}</p>
    </div>
  );
}

export function ViewerDetail({ mediaId }: { mediaId: string }) {
  const result = useViewerData<MediaDetailRecord>(`/browse/media/${mediaId}`);
  const next = useViewerData<{ item: MediaCardRecord | null }>(
    `/browse/media/${mediaId}/next`,
  );
  const devFallback = IS_DEV_PREVIEW && !!result.error;
  const item = result.data ?? (devFallback ? sampleDetail : undefined);
  const file = item?.files.find((f) => f.available) ?? item?.files[0];
  const canPlay = !!item && item.kind !== 'series' && !!item.file_id;
  const meta = placeholderDetailMeta;

  return (
    <>
      <DevPreviewBanner />
      {!devFallback && <CatalogState {...result} empty={false} />}
      {item && !result.loading && (!result.error || devFallback) && (
        <>
          <HeroBackdrop backgroundUrl={item.background_url}>
            <Link
              href={
                item.show_id
                  ? `/watch/${item.show_id}`
                  : item.kind === 'series'
                    ? '/shows'
                    : '/movies'
              }
              className="mb-6 inline-flex items-center gap-1 text-sm text-primary outline-none hover:underline focus-visible:underline"
            >
              ← {item.show_id ? 'Back to show' : 'Back to collection'}
            </Link>

            <div className="grid gap-8 sm:grid-cols-[180px_minmax(0,1fr)] lg:grid-cols-[240px_minmax(0,1fr)]">
              <div className="hidden max-w-[240px] sm:block">
                <Poster url={item.poster_url} title={item.title} />
              </div>

              <div className="min-w-0">
                <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
                  {item.kind}
                  {item.episode_number != null
                    ? ` · Season ${item.season_number}, Episode ${item.episode_number}`
                    : ''}
                </p>
                <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
                  {item.title}
                </h1>

                <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-muted-foreground">
                  <span>{item.year ?? 'Year not identified'}</span>
                  {file?.duration_seconds ? (
                    <span className="inline-flex items-center gap-1">
                      <Clock className="size-3.5" aria-hidden="true" />
                      {clockTime(file.duration_seconds)}
                    </span>
                  ) : null}
                  <span className="inline-flex items-center gap-1">
                    <span className="rounded border border-border px-1.5 py-0.5 text-[11px]">
                      {meta.contentRating}
                    </span>
                    <DevBadge label="Placeholder" />
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Star className="size-3.5" aria-hidden="true" />
                    {meta.rating.value}
                    <DevBadge label="Placeholder" />
                  </span>
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {meta.genres.map((g) => (
                    <span
                      key={g}
                      className="rounded-full border border-border bg-background/40 px-2.5 py-0.5 text-xs text-muted-foreground"
                    >
                      {g}
                    </span>
                  ))}
                  <DevBadge label="Placeholder genres" />
                </div>

                {!item.watched && item.position_seconds >= 5 && file?.duration_seconds ? (
                  <p className="mt-4 text-xs text-muted-foreground">
                    {clockTime(file.duration_seconds - item.position_seconds)} left
                    · resumes at {clockTime(item.position_seconds)}
                  </p>
                ) : null}

                <div className="mt-6">
                  <PrimaryActions
                    mediaId={item.id}
                    canPlay={canPlay}
                    positionSeconds={item.position_seconds}
                    watched={item.watched}
                    kind={item.kind}
                    onChanged={result.reload}
                  />
                  {next.data?.item ? (
                    <Link
                      href={`/player/${next.data.item.id}`}
                      className="mt-3 inline-flex items-center gap-2 text-sm text-primary outline-none hover:underline focus-visible:underline"
                    >
                      <SkipForward className="size-4" aria-hidden="true" />
                      {item.kind === 'series'
                        ? 'Play next unwatched episode'
                        : 'Play next episode'}
                    </Link>
                  ) : null}
                </div>
              </div>
            </div>
          </HeroBackdrop>

          <div className="mt-8 max-w-3xl">
            <h2 className="text-lg font-semibold tracking-tight">Overview</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Details come from your local files. No external metadata is
              requested, so filename-derived titles may be incomplete.
            </p>
            <div className="mt-3">
              <DevPlaceholder note="Synopsis is not provided by the Agent yet">
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {meta.synopsis}
                </p>
              </DevPlaceholder>
            </div>
          </div>

          <section className="mt-8">
            <h2 className="mb-3 text-lg font-semibold tracking-tight">Details</h2>
            <DevPlaceholder note="These fields are placeholders until the Agent supplies metadata">
              <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                <MetaItem term="Director" value={meta.director} />
                <MetaItem term="Studio" value={meta.studio} />
                <MetaItem
                  term="Release date"
                  value={meta.releaseDate}
                  icon={<Calendar className="size-3.5" aria-hidden="true" />}
                />
                <MetaItem term="Content rating" value={meta.contentRating} />
                <MetaItem term="Genres" value={meta.genres.join(', ')} />
                <MetaItem
                  term={meta.rating.label}
                  value={`${meta.rating.value} — ${meta.rating.note}`}
                />
              </dl>
            </DevPlaceholder>
          </section>

          {file && <StreamSummary file={file} />}

          <CastRow />

          <DevPlaceholder
            className="mt-10"
            note="Related titles are not computed yet"
          >
            <MediaRow
              title="Related Titles"
              items={placeholderRows.related}
              sample
              className="mb-0"
            />
          </DevPlaceholder>

          {item.kind === 'series' && <Seasons mediaId={item.id} />}
        </>
      )}
    </>
  );
}

function MetaItem({
  term,
  value,
  icon,
}: {
  term: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {term}
      </dt>
      <dd className="mt-0.5 flex items-center gap-1.5 text-sm">
        {icon}
        {value}
      </dd>
    </div>
  );
}
