'use client';

import { useCallback, useEffect, useState } from 'react';
import { Captions, Film, LibraryBig, Music2, Search, Video } from 'lucide-react';

import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious } from '@/components/ui/pagination';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError, apiRequest, type MediaRecord, type PageResult } from '@/lib/api';

function duration(seconds?: number | null): string | undefined {
  if (!seconds) return undefined;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function MediaResults() {
  const [result, setResult] = useState<PageResult<MediaRecord>>({ items: [], page: 1, page_size: 24, total: 0 });
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [availability, setAvailability] = useState('true');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const load = useCallback(async () => {
    setLoading(true); setError(undefined);
    const parameters = new URLSearchParams({ page: String(page), page_size: '24' });
    if (submittedQuery) parameters.set('search', submittedQuery);
    if (availability !== 'all') parameters.set('available', availability);
    try {
      setResult(await apiRequest<PageResult<MediaRecord>>(`/media?${parameters}`));
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 401) { window.location.replace('/login'); return; }
      setError(caught instanceof Error ? caught.message : 'Media results could not be loaded.');
    } finally { setLoading(false); }
  }, [availability, page, submittedQuery]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  const pages = Math.max(1, result.pages ?? Math.ceil(result.total / result.page_size));

  return (
    <>
      <PageHeader eyebrow="Local catalog" title="Media" description="Paginated results derived only from filenames, embedded stream data, and local sidecar files." />
      <form
        className="mt-6 flex flex-col gap-2 rounded-xl border bg-card p-3 sm:flex-row"
        onSubmit={(event) => { event.preventDefault(); setPage(1); setSubmittedQuery(query.trim()); }}
      >
        <div className="relative min-w-0 flex-1"><Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input className="pl-8" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search local titles" aria-label="Search local media titles" /></div>
        <Select value={availability} onValueChange={(value) => { setAvailability(String(value)); setPage(1); }}>
          <SelectTrigger className="w-full sm:w-44" aria-label="Filter by availability"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="true">Available</SelectItem><SelectItem value="false">Missing</SelectItem></SelectContent>
        </Select>
        <Button type="submit">Search</Button>
      </form>

      <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground"><span>{result.total.toLocaleString()} local result{result.total === 1 ? '' : 's'}</span><span>Page {result.page} of {pages}</span></div>
      {error ? <Alert variant="destructive" className="mt-4"><AlertTitle>Results unavailable</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}

      {loading ? (
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">{Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="aspect-[4/3]" />)}</div>
      ) : result.items.length ? (
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {result.items.map((item, index) => {
            const file = item.files.find((candidate) => candidate.available && !candidate.analysis_error)
              ?? item.files.find((candidate) => candidate.available)
              ?? item.files[0];
            return (
              <Card key={item.id} className="border-none shadow-[0_8px_30px_rgb(18_42_66/5%)]">
                <div className={`aspect-[16/9] bg-gradient-to-br ${['from-[#15466f] to-[#0b1e34]', 'from-[#2b5c76] to-[#102d42]', 'from-[#356b70] to-[#132f36]', 'from-[#395783] to-[#172643]'][index % 4]} p-4 text-white`}>
                  <div className="flex h-full flex-col justify-between"><div className="flex justify-between"><Film className="size-6 text-white/60" /><Badge className="bg-black/20 text-white">{item.available ? 'Available' : 'Missing'}</Badge></div><p className="text-[10px] uppercase tracking-wider text-white/65">{file?.container ?? 'Local media'}</p></div>
                </div>
                <CardContent>
                  <h2 className="truncate font-medium" title={item.title}>{item.title}</h2>
                  <p className="mt-1 text-xs capitalize text-muted-foreground">{item.kind.replaceAll('_', ' ')}{item.year ? ` · ${item.year}` : ''}{duration(file?.duration_seconds) ? ` · ${duration(file?.duration_seconds)}` : ''}</p>
                  {file ? <>
                    <div className="mt-3 flex gap-3 text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><Video className="size-3" />{file.video_streams}</span><span className="flex items-center gap-1"><Music2 className="size-3" />{file.audio_streams}</span><span className="flex items-center gap-1"><Captions className="size-3" />{file.subtitle_streams}</span></div>
                    <p className={`mt-2 truncate text-[11px] ${file.analysis_error ? 'text-destructive' : 'text-muted-foreground'}`} title={file.analysis_error ?? undefined}>
                      {file.analysis_error ?? ([file.video[0]?.codec, file.video[0]?.width && file.video[0]?.height ? `${file.video[0].width}×${file.video[0].height}` : null, file.audio.map((stream) => stream.language).filter(Boolean).join('/'), file.subtitles.map((stream) => stream.language).filter(Boolean).join('/')].filter(Boolean).join(' · ') || 'Analysis pending')}
                    </p>
                  </> : null}
                </CardContent>
              </Card>
            );
          })}
        </div>
      ) : (
        <Empty className="mt-5 min-h-72 border"><EmptyHeader><EmptyMedia variant="icon"><LibraryBig /></EmptyMedia><EmptyTitle>No matching media</EmptyTitle><EmptyDescription>Try a different search, or scan an enabled library.</EmptyDescription></EmptyHeader></Empty>
      )}

      {pages > 1 ? <Pagination className="mt-7"><PaginationContent><PaginationItem><PaginationPrevious href="#" aria-disabled={page <= 1} onClick={(event) => { event.preventDefault(); if (page > 1) setPage(page - 1); }} /></PaginationItem><PaginationItem><span className="px-3 text-sm tabular-nums">{page} / {pages}</span></PaginationItem><PaginationItem><PaginationNext href="#" aria-disabled={page >= pages} onClick={(event) => { event.preventDefault(); if (page < pages) setPage(page + 1); }} /></PaginationItem></PaginationContent></Pagination> : null}
    </>
  );
}
