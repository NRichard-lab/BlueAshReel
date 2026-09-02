'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Check, FolderPlus, LoaderCircle, Play, RefreshCw, Save, Trash2 } from 'lucide-react';
import Link from 'next/link';

import { MediaFolderField } from '@/components/media-folder-picker';
import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { ApiError, apiRequest, jsonBody, type JobRecord, type LibraryRecord, type MediaFolderSelection, type PageResult } from '@/lib/api';
import { libraryFolderAddBody } from '@/lib/media-folders';

function percent(job?: JobRecord): number {
  if (!job?.progress_total) return 0;
  return Math.min(100, Math.round((job.progress_current / job.progress_total) * 100));
}

function elapsed(value?: number | null): string {
  if (value === undefined || value === null) return 'Pending';
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`;
}

export function LibraryDetail({ libraryId }: { libraryId: string }) {
  const [library, setLibrary] = useState<LibraryRecord>();
  const [job, setJob] = useState<JobRecord>();
  const [name, setName] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [newFolder, setNewFolder] = useState<MediaFolderSelection>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  const load = useCallback(async () => {
    try {
      const result = await apiRequest<LibraryRecord>(`/libraries/${libraryId}`);
      setLibrary(result);
      setName(result.name);
      setEnabled(result.enabled);
      if (result.active_job_id) {
        setJob(await apiRequest<JobRecord>(`/jobs/${result.active_job_id}`));
      } else {
        const latest = await apiRequest<PageResult<JobRecord>>(`/jobs?library_id=${encodeURIComponent(libraryId)}&page=1&page_size=1`);
        setJob(latest.items[0]);
      }
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 401) {
        window.location.replace('/login');
        return;
      }
      setError(caught instanceof Error ? caught.message : 'Library details could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [libraryId]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  useEffect(() => {
    if (!job || !['queued', 'running', 'retry_wait'].includes(job.status)) return;
    const timer = window.setInterval(() => {
      apiRequest<JobRecord>(`/jobs/${job.id}`).then((next) => {
        setJob(next);
        if (!['queued', 'running', 'retry_wait'].includes(next.status)) void load();
      }).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [job, load]);

  const active = useMemo(() => Boolean(job && ['queued', 'running', 'retry_wait'].includes(job.status)), [job]);

  const save = async () => {
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      const result = await apiRequest<LibraryRecord>(`/libraries/${libraryId}`, { method: 'PATCH', body: jsonBody({ name: name.trim(), enabled }) });
      setLibrary(result); setNotice('Library settings saved.');
    } catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'Settings were not saved.'); }
    finally { setSaving(false); }
  };

  const addPath = async (event: React.SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newFolder) {
      setError('Choose a readable media folder before adding it.');
      return;
    }
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      await apiRequest(`/libraries/${libraryId}/paths`, { method: 'POST', body: jsonBody(libraryFolderAddBody(newFolder)) });
      setNewFolder(undefined); setNotice('Media folder added.'); await load();
    } catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'The media path was not added.'); }
    finally { setSaving(false); }
  };

  const removePath = async (pathId: string) => {
    if (!window.confirm('Remove this path from the library? Source media will not be changed.')) return;
    setError(undefined); setNotice(undefined);
    try { await apiRequest(`/libraries/${libraryId}/paths/${pathId}`, { method: 'DELETE' }); setNotice('Path removed from the library.'); await load(); }
    catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'The path was not removed.'); }
  };

  const scan = async (mode: 'full' | 'changed') => {
    setError(undefined); setNotice(undefined);
    try {
      const accepted = await apiRequest<{ job_id: string; status: string }>(`/libraries/${libraryId}/scans`, { method: 'POST', body: jsonBody({ mode }) });
      setJob(await apiRequest<JobRecord>(`/jobs/${accepted.job_id}`));
      setNotice(`${mode === 'full' ? 'Full' : 'Changed-file'} scan queued.`);
    } catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'The scan could not be started.'); }
  };

  if (loading) return <div className="space-y-4"><Skeleton className="h-12 w-72" /><Skeleton className="h-64" /></div>;

  return (
    <>
      <Link href="/libraries" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Libraries</Link>
      <PageHeader
        eyebrow={library?.library_type.replaceAll('_', ' ') ?? 'Library'}
        title={library?.name ?? 'Library unavailable'}
        description="Manage read-only paths, scanning, and this library’s local status."
        actions={<><Button variant="outline" disabled={active || !library?.enabled} onClick={() => scan('full')}><RefreshCw data-icon="inline-start" /> Full scan</Button><Button disabled={active || !library?.enabled} onClick={() => scan('changed')}><Play data-icon="inline-start" /> Scan changes</Button></>}
      />
      {error ? <Alert variant="destructive" className="mt-6"><AlertTitle>Action failed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
      {notice ? <Alert className="mt-6 border-[var(--success-border)] bg-[var(--success-soft)]"><Check /><AlertTitle>Updated</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert> : null}

      <div className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          {job ? (
            <Card>
              <CardHeader className="border-b"><CardTitle>Latest scan job</CardTitle><CardDescription className="capitalize">{job.job_type.replaceAll('_', ' ')}</CardDescription><Badge className="self-start capitalize" variant={job.status === 'failed' ? 'destructive' : 'secondary'}>{job.status}</Badge></CardHeader>
              <CardContent>
                <Progress value={percent(job)} aria-label="Scan progress" />
                <div className="mt-2 flex justify-between text-xs text-muted-foreground"><span>{job.progress_current.toLocaleString()} processed</span><span>{job.progress_total?.toLocaleString() ?? 'Discovering total'}</span></div>
                {job.scan ? <div className="mt-4 grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-3 lg:grid-cols-6">
                  {[
                    ['Discovered', job.scan.discovered_files.toLocaleString()],
                    ['Analyzed', job.scan.processed_files.toLocaleString()],
                    ['Unchanged', job.scan.unchanged_files.toLocaleString()],
                    ['Missing', job.scan.missing_files.toLocaleString()],
                    ['Errors', job.scan.error_count.toLocaleString()],
                    ['Duration', elapsed(job.scan.duration_ms)],
                  ].map(([label, value]) => <div key={label} className={`rounded-lg p-2 ${label === 'Errors' && job.scan?.error_count ? 'bg-destructive/8 text-destructive' : 'bg-muted/40'}`}><strong className="block text-sm">{value}</strong>{label}</div>)}
                </div> : null}
                {job.error_summary ? <p className="mt-4 rounded-lg bg-destructive/8 p-3 text-sm text-destructive">{job.error_summary}</p> : null}
                {job.scan?.error_count ? <p className="mt-4 rounded-lg bg-destructive/8 p-3 text-sm text-destructive">{job.scan.error_count.toLocaleString()} file{job.scan.error_count === 1 ? '' : 's'} could not be analyzed. Their technical metadata is withheld until a later scan succeeds.</p> : null}
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader className="border-b"><CardTitle>Media directories</CardTitle><CardDescription>Mounted sources are validated and treated as read-only.</CardDescription></CardHeader>
            <CardContent className="space-y-4">
              {library?.paths.map((path) => (
                <div key={path.id} className="flex items-center gap-3 rounded-lg border bg-muted/20 p-3">
                  <code className="min-w-0 flex-1 truncate text-xs">{path.path}</code>
                  <Badge variant={path.enabled ? 'secondary' : 'outline'}>{path.enabled ? 'Enabled' : 'Disabled'}</Badge>
                  <Button size="icon-sm" variant="ghost" aria-label="Remove media path" onClick={() => removePath(path.id)}><Trash2 /></Button>
                </div>
              ))}
              <form onSubmit={addPath} className="space-y-3 border-t pt-4">
                <MediaFolderField id="additional-library-folder" selection={newFolder} onSelectionChange={setNewFolder} />
                <div className="flex justify-end"><Button type="submit" disabled={saving}><FolderPlus data-icon="inline-start" /> Add folder</Button></div>
              </form>
            </CardContent>
          </Card>
        </div>

        <Card className="h-fit">
          <CardHeader className="border-b"><CardTitle>Library settings</CardTitle><CardDescription>Display and scanning availability.</CardDescription></CardHeader>
          <CardContent className="space-y-5">
            <Field><FieldLabel htmlFor="edit-library-name">Display name</FieldLabel><Input id="edit-library-name" value={name} onChange={(event) => setName(event.target.value)} /></Field>
            <Field orientation="horizontal"><Switch id="library-enabled" checked={enabled} onCheckedChange={setEnabled} /><div><FieldLabel htmlFor="library-enabled">Library enabled</FieldLabel><FieldDescription>Disabled libraries cannot start new scans.</FieldDescription></div></Field>
            <div className="grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded-lg bg-muted/40 p-2"><strong className="block text-lg">{library?.media_count.toLocaleString() ?? 0}</strong> Items</div>
              <div className="rounded-lg bg-muted/40 p-2"><strong className="block text-lg">{library?.available_file_count.toLocaleString() ?? 0}</strong> Files</div>
              <div className="rounded-lg bg-muted/40 p-2"><strong className="block text-lg">{library?.error_count.toLocaleString() ?? 0}</strong> Errors</div>
            </div>
            <Button className="w-full" disabled={saving || !name.trim()} onClick={save}>{saving ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <Save data-icon="inline-start" />} Save settings</Button>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
