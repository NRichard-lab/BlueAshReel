'use client';

import { useCallback, useEffect, useState } from 'react';
import { Check, ChevronLeft, ChevronRight, Film, FolderPlus, LibraryBig, LoaderCircle, Plus, Tv, Video } from 'lucide-react';
import Link from 'next/link';

import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError, apiRequest, jsonBody, type LibraryRecord, type PageResult } from '@/lib/api';

type LibraryType = 'movies' | 'tv' | 'other';

const typeLabels: Record<LibraryType, string> = { movies: 'Movies', tv: 'TV Shows', other: 'Other Videos' };
const typeIcons = { movies: Film, tv: Tv, other: Video };
const pageSize = 12;

function formatDate(value?: string | null): string {
  if (!value) return 'Never scanned';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

export function LibrariesManager() {
  const [libraries, setLibraries] = useState<LibraryRecord[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [success, setSuccess] = useState<string>();
  const [name, setName] = useState('');
  const [libraryType, setLibraryType] = useState<LibraryType>('movies');
  const [path, setPath] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await apiRequest<PageResult<LibraryRecord>>(`/libraries?page=${page}&page_size=${pageSize}`);
      setLibraries(result.items);
      setTotal(result.total);
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 401) {
        window.location.replace('/login');
        return;
      }
      setError(caught instanceof Error ? caught.message : 'Libraries could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);

  const create = async (event: React.SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    setSuccess(undefined);
    try {
      await apiRequest<LibraryRecord>('/libraries', {
        method: 'POST',
        body: jsonBody({ name: name.trim(), library_type: libraryType, enabled: true, paths: [path.trim()] }),
      });
      setName('');
      setPath('');
      setShowForm(false);
      setSuccess('Library created. Start its first scan when you are ready.');
      if (page === 1) await load();
      else setPage(1);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : 'The library could not be created.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Read-only sources"
        title="Libraries"
        description="Organize mounted media directories and run local scans without changing source files."
        actions={<Button onClick={() => setShowForm((visible) => !visible)}><Plus data-icon="inline-start" /> {showForm ? 'Close form' : 'Add library'}</Button>}
      />

      {error ? <Alert variant="destructive" className="mt-6"><AlertTitle>Library action failed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
      {success ? <Alert className="mt-6 border-[var(--success-border)] bg-[var(--success-soft)]"><Check /><AlertTitle>Saved</AlertTitle><AlertDescription>{success}</AlertDescription></Alert> : null}

      {showForm ? (
        <Card className="mt-6 border-none shadow-[0_8px_30px_rgb(18_42_66/6%)]">
          <CardHeader className="border-b"><CardTitle>Add a media library</CardTitle><CardDescription>The server validates the path without exposing a filesystem browser.</CardDescription></CardHeader>
          <CardContent>
            <form onSubmit={create}>
              <FieldGroup>
                <div className="grid gap-5 sm:grid-cols-2">
                  <Field><FieldLabel htmlFor="library-name">Display name</FieldLabel><Input id="library-name" required value={name} onChange={(event) => setName(event.target.value)} placeholder="Documentaries" /></Field>
                  <Field>
                    <FieldLabel htmlFor="library-type">Library type</FieldLabel>
                    <Select value={libraryType} onValueChange={(value) => setLibraryType(value as LibraryType)}>
                      <SelectTrigger id="library-type" className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="movies">Movies</SelectItem><SelectItem value="tv">TV Shows</SelectItem><SelectItem value="other">Other Videos</SelectItem></SelectContent>
                    </Select>
                  </Field>
                </div>
                <Field><FieldLabel htmlFor="library-path">Mounted media directory</FieldLabel><Input id="library-path" required value={path} onChange={(event) => setPath(event.target.value)} placeholder="/media/documentaries" /><FieldDescription>The directory must be inside an explicitly allowed media root and readable by the server.</FieldDescription></Field>
                <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button><Button type="submit" disabled={saving}>{saving ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <FolderPlus data-icon="inline-start" />} Create library</Button></div>
              </FieldGroup>
            </form>
          </CardContent>
        </Card>
      ) : null}

      <section className="mt-7" aria-label="Configured libraries">
        {loading ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((item) => <Skeleton key={item} className="h-56" />)}</div>
        ) : libraries.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {libraries.map((library) => {
              const libraryTypeValue = library.library_type as LibraryType;
              const Icon = typeIcons[libraryTypeValue] ?? LibraryBig;
              return (
                <Link key={library.id} href={`/libraries/${library.id}`} aria-label={`Open ${library.name} library`} className="group rounded-xl focus-visible:ring-2 focus-visible:ring-ring">
                  <Card className="h-full border-none shadow-[0_8px_30px_rgb(18_42_66/5%)] transition-transform group-hover:-translate-y-0.5">
                    <CardHeader className="border-b">
                      <span className="mb-3 grid size-10 place-items-center rounded-xl bg-primary/10 text-primary"><Icon className="size-5" /></span>
                      <CardTitle>{library.name}</CardTitle>
                      <CardDescription>{typeLabels[libraryTypeValue] ?? library.library_type}</CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="grid grid-cols-3 gap-2 text-center">
                        <div className="rounded-lg bg-muted/40 p-2"><p className="text-lg font-semibold">{library.media_count.toLocaleString()}</p><p className="text-[11px] text-muted-foreground">Items</p></div>
                        <div className="rounded-lg bg-muted/40 p-2"><p className="text-lg font-semibold">{library.available_file_count.toLocaleString()}</p><p className="text-[11px] text-muted-foreground">Available</p></div>
                        <div className="rounded-lg bg-muted/40 p-2"><p className="text-lg font-semibold">{library.error_count.toLocaleString()}</p><p className="text-[11px] text-muted-foreground">Errors</p></div>
                      </div>
                      <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground"><span>{formatDate(library.last_successful_scan_at)}</span><Badge variant={library.enabled ? 'secondary' : 'outline'}>{library.enabled ? 'Enabled' : 'Disabled'}</Badge></div>
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        ) : (
          <Empty className="min-h-80 border">
            <EmptyHeader><EmptyMedia variant="icon"><LibraryBig /></EmptyMedia><EmptyTitle>No libraries configured</EmptyTitle><EmptyDescription>Add a Movies, TV Shows, or Other Videos library to begin.</EmptyDescription></EmptyHeader>
            <EmptyContent><Button onClick={() => setShowForm(true)}><Plus data-icon="inline-start" /> Add the first library</Button></EmptyContent>
          </Empty>
        )}
        {!loading && total > pageSize ? (
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
            <p className="text-xs text-muted-foreground">
              Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total.toLocaleString()} libraries
            </p>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>
                <ChevronLeft data-icon="inline-start" /> Previous
              </Button>
              <span className="px-2 text-xs tabular-nums text-muted-foreground">Page {page} of {Math.ceil(total / pageSize)}</span>
              <Button variant="outline" size="sm" disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage((current) => current + 1)}>
                Next <ChevronRight data-icon="inline-end" />
              </Button>
            </div>
          </div>
        ) : null}
      </section>
    </>
  );
}
