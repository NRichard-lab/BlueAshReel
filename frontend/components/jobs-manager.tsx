'use client';

import { useCallback, useEffect, useState } from 'react';
import { Ban, BriefcaseBusiness, LoaderCircle, RefreshCw } from 'lucide-react';

import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious } from '@/components/ui/pagination';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ApiError, apiRequest, type JobRecord, type PageResult } from '@/lib/api';

function progress(job: JobRecord): number {
  return job.progress_total ? Math.min(100, Math.round((job.progress_current / job.progress_total) * 100)) : 0;
}

function when(value?: string | null): string {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '—';
}

function elapsed(value?: number | null): string {
  if (value === undefined || value === null) return '—';
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`;
}

export function JobsManager() {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [cancelling, setCancelling] = useState<string>();

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const result = await apiRequest<PageResult<JobRecord>>(`/jobs?page=${page}&page_size=25`);
      setJobs(result.items);
      setTotal(result.total);
    }
    catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 401) { window.location.replace('/login'); return; }
      if (!quiet) setError(caught instanceof Error ? caught.message : 'Jobs could not be loaded.');
    } finally { if (!quiet) setLoading(false); }
  }, [page]);

  useEffect(() => { queueMicrotask(() => void load()); const timer = window.setInterval(() => void load(true), 4000); return () => window.clearInterval(timer); }, [load]);

  const cancel = async (job: JobRecord) => {
    setCancelling(job.id); setError(undefined);
    try { await apiRequest(`/jobs/${job.id}/cancel`, { method: 'POST' }); await load(true); }
    catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'Cancellation could not be requested.'); }
    finally { setCancelling(undefined); }
  };

  return (
    <>
      <PageHeader eyebrow="Durable local queue" title="Background jobs" description="Scans and analysis continue outside page requests and recover safely after restarts." actions={<Button variant="outline" onClick={() => load()}><RefreshCw data-icon="inline-start" /> Refresh</Button>} />
      {error ? <Alert variant="destructive" className="mt-6"><AlertTitle>Queue action failed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
      <Card className="mt-6 border-none shadow-[0_8px_30px_rgb(18_42_66/5%)]"><CardContent className="p-0">
        {loading ? <div className="space-y-3 p-5">{[0, 1, 2].map((item) => <Skeleton key={item} className="h-16" />)}</div> : jobs.length ? (
          <Table>
            <TableHeader><TableRow><TableHead>Job</TableHead><TableHead>Status</TableHead><TableHead className="min-w-52">Progress</TableHead><TableHead>Attempts</TableHead><TableHead>Created</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader>
            <TableBody>{jobs.map((job) => {
              const canCancel = ['queued', 'running', 'retry_wait'].includes(job.status) && !job.cancel_requested;
              return <TableRow key={job.id}><TableCell><span className="font-medium capitalize">{job.job_type.replaceAll('_', ' ')}</span><span className="block max-w-64 truncate text-xs text-muted-foreground">{job.error_summary ?? job.id}</span>{job.scan ? <span className={`mt-1 block text-[11px] ${job.scan.error_count ? 'text-destructive' : 'text-muted-foreground'}`}>{job.scan.discovered_files.toLocaleString()} discovered · {job.scan.processed_files.toLocaleString()} analyzed · {job.scan.unchanged_files.toLocaleString()} unchanged · {job.scan.missing_files.toLocaleString()} missing · {job.scan.error_count.toLocaleString()} errors · {elapsed(job.scan.duration_ms)}</span> : null}</TableCell><TableCell><Badge variant={job.status === 'failed' || Boolean(job.scan?.error_count) ? 'destructive' : 'secondary'} className="capitalize">{job.status}{job.scan?.error_count ? ` · ${job.scan.error_count} errors` : ''}</Badge></TableCell><TableCell><Progress value={progress(job)} aria-label={`${job.job_type} progress`} /><span className="mt-1 block text-[11px] text-muted-foreground">{job.progress_current.toLocaleString()} / {job.progress_total?.toLocaleString() ?? 'discovering'}</span></TableCell><TableCell>{job.attempts} / {job.max_attempts}</TableCell><TableCell>{when(job.created_at)}</TableCell><TableCell>{canCancel ? <Button variant="ghost" size="sm" disabled={cancelling === job.id} onClick={() => cancel(job)}>{cancelling === job.id ? <LoaderCircle className="animate-spin" /> : <Ban />} Cancel</Button> : null}</TableCell></TableRow>;
            })}</TableBody>
          </Table>
        ) : <Empty className="min-h-72"><EmptyHeader><EmptyMedia variant="icon"><BriefcaseBusiness /></EmptyMedia><EmptyTitle>No background jobs</EmptyTitle><EmptyDescription>Library scans and future local processing work will appear here.</EmptyDescription></EmptyHeader></Empty>}
      </CardContent></Card>
      {total > 25 ? <Pagination className="mt-6"><PaginationContent><PaginationItem><PaginationPrevious href="#" aria-disabled={page <= 1} onClick={(event) => { event.preventDefault(); if (page > 1) setPage((current) => current - 1); }} /></PaginationItem><PaginationItem><span className="px-3 text-sm tabular-nums">Page {page} of {Math.ceil(total / 25)}</span></PaginationItem><PaginationItem><PaginationNext href="#" aria-disabled={page >= Math.ceil(total / 25)} onClick={(event) => { event.preventDefault(); if (page < Math.ceil(total / 25)) setPage((current) => current + 1); }} /></PaginationItem></PaginationContent></Pagination> : null}
    </>
  );
}
