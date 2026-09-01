'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowRight,
  Check,
  CircleGauge,
  Database,
  Film,
  FolderKanban,
  HardDrive,
  LibraryBig,
  LoaderCircle,
  Play,
  RefreshCw,
} from 'lucide-react';
import Link from 'next/link';

import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { buttonVariants } from '@/components/ui/button';
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import {
  ApiError,
  apiRequest,
  type DashboardResult,
  type JobRecord,
  type LibraryRecord,
  type MediaRecord,
  type PageResult,
} from '@/lib/api';
import { cn } from '@/lib/utils';

function percentage(job: JobRecord): number {
  if (!job.progress_total || job.progress_total <= 0) return 0;
  return Math.min(100, Math.round((job.progress_current / job.progress_total) * 100));
}

function shortDate(value?: string | null): string {
  if (!value) return 'Not yet scanned';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

function formatBytes(value?: number | null): string {
  if (value === undefined || value === null) return 'Space unavailable';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]} free`;
}

export function DashboardOverview() {
  const [dashboard, setDashboard] = useState<DashboardResult>();
  const [libraries, setLibraries] = useState<LibraryRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [media, setMedia] = useState<MediaRecord[]>([]);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const setup = await apiRequest<{ setup_required: boolean }>('/setup/status');
        if (setup.setup_required) {
          window.location.replace('/setup');
          return;
        }
        const [dashboardResult, libraryResult, jobResult, mediaResult] = await Promise.all([
          apiRequest<DashboardResult>('/dashboard'),
          apiRequest<PageResult<LibraryRecord>>('/libraries?page=1&page_size=3'),
          apiRequest<PageResult<JobRecord>>('/jobs?page=1&page_size=5'),
          apiRequest<PageResult<MediaRecord>>('/media?page=1&page_size=4'),
        ]);
        setDashboard(dashboardResult);
        setLibraries(libraryResult.items);
        setJobs(jobResult.items);
        setMedia(mediaResult.items);
      } catch (caught: unknown) {
        if (caught instanceof ApiError && caught.status === 401) {
          window.location.replace('/login');
          return;
        }
        setError(caught instanceof Error ? caught.message : 'The dashboard could not be loaded.');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const activeJob = useMemo(() => jobs.find((job) => ['queued', 'running', 'retry_wait'].includes(job.status)), [jobs]);
  const storageReady = dashboard ? Object.values(dashboard.storage).filter(Boolean).length : 0;

  const stats = [
    { label: 'Libraries', value: dashboard?.library_count, detail: `${libraries.filter((library) => library.enabled).length} visible here`, icon: LibraryBig },
    { label: 'Media items', value: dashboard?.media_count, detail: 'Local database records', icon: Film },
    { label: 'Active jobs', value: dashboard?.active_job_count, detail: activeJob ? activeJob.job_type.replaceAll('_', ' ') : 'Queue is quiet', icon: Activity },
    { label: 'Storage areas', value: dashboard ? storageReady : undefined, detail: dashboard ? `${Object.keys(dashboard.storage).length} configured` : 'Checking', icon: HardDrive },
  ];

  return (
    <>
      <PageHeader
        eyebrow="Home server"
        title="Everything at home, ready when you are."
        description="Monitor libraries, local processing, and privacy from one quiet control room."
        actions={<Link href="/libraries" className={buttonVariants()}><FolderKanban data-icon="inline-start" /> Manage libraries</Link>}
      />

      {error ? (
        <Alert variant="destructive" className="mt-6">
          <AlertTitle>Dashboard unavailable</AlertTitle>
          <AlertDescription>{error} The server can still be checked from System Health.</AlertDescription>
        </Alert>
      ) : null}

      <div className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map(({ label, value, detail, icon: Icon }) => (
          <Card key={label} className="border-none shadow-[0_8px_30px_rgb(18_42_66/5%)]">
            <CardHeader>
              <CardDescription>{label}</CardDescription>
              <CardAction><span className="grid size-8 place-items-center rounded-lg bg-primary/8 text-primary"><Icon className="size-4" /></span></CardAction>
            </CardHeader>
            <CardContent>
              {loading ? <Skeleton className="h-8 w-20" /> : <p className="text-2xl font-semibold tracking-[-0.04em]">{value?.toLocaleString() ?? '—'}</p>}
              <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(300px,.75fr)]">
        <Card className="border-none shadow-[0_8px_30px_rgb(18_42_66/5%)]">
          <CardHeader className="border-b">
            <CardTitle>Library activity</CardTitle>
            <CardDescription>{activeJob ? 'Local work is running in the background' : `Last scan: ${shortDate(dashboard?.last_scan_at)}`}</CardDescription>
            {activeJob ? <CardAction><Badge variant="secondary"><RefreshCw data-icon="inline-start" className="animate-spin [animation-duration:3s]" /> {activeJob.status}</Badge></CardAction> : null}
          </CardHeader>
          <CardContent className="space-y-5">
            {activeJob ? (
              <div>
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="font-medium capitalize">{activeJob.job_type.replaceAll('_', ' ')}</span>
                  <span className="tabular-nums text-muted-foreground">{activeJob.progress_current.toLocaleString()} / {activeJob.progress_total?.toLocaleString() ?? '—'}</span>
                </div>
                <Progress value={percentage(activeJob)} aria-label="Active job progress" />
              </div>
            ) : null}
            {libraries.length ? (
              <div className="grid gap-3 sm:grid-cols-3">
                {libraries.map((library) => (
                  <Link key={library.id} href={`/libraries/${library.id}`} className="rounded-lg border bg-muted/30 p-3 transition-colors hover:bg-muted/60">
                    <div className="flex items-center justify-between">
                      <span className="truncate text-sm font-medium">{library.name}</span>
                      {library.error_count ? <Badge variant="destructive">{library.error_count}</Badge> : <Check className="size-4 text-[var(--success)]" aria-label="Healthy" />}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">{library.media_count.toLocaleString()} items · {shortDate(library.last_successful_scan_at)}</p>
                  </Link>
                ))}
              </div>
            ) : loading ? (
              <div className="grid gap-3 sm:grid-cols-3">{[0, 1, 2].map((item) => <Skeleton key={item} className="h-20" />)}</div>
            ) : (
              <Empty className="border">
                <EmptyHeader><EmptyMedia variant="icon"><LibraryBig /></EmptyMedia><EmptyTitle>No libraries yet</EmptyTitle><EmptyDescription>Add a read-only media directory to begin.</EmptyDescription></EmptyHeader>
              </Empty>
            )}
            <Link href="/jobs" className={cn(buttonVariants({ variant: 'ghost' }), 'w-full justify-between')}>
              View background jobs <ArrowRight data-icon="inline-end" />
            </Link>
          </CardContent>
        </Card>

        <Card className="border-none shadow-[0_8px_30px_rgb(18_42_66/5%)]">
          <CardHeader className="border-b">
            <CardTitle>System health</CardTitle>
            <CardDescription>No private paths or titles exposed</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1">
            {[
              { label: 'Server', detail: dashboard?.server_status ?? 'Checking', ready: dashboard?.server_status === 'ok', icon: CircleGauge },
              { label: 'Database', detail: dashboard?.database_status ?? 'Checking', ready: dashboard?.database_status === 'ok', icon: Database },
              { label: 'FFprobe', detail: dashboard?.ffprobe_available ? 'Available locally' : 'Unavailable', ready: Boolean(dashboard?.ffprobe_available), icon: Play },
              { label: 'FFmpeg', detail: dashboard?.ffmpeg_available ? 'Available locally' : 'Unavailable', ready: Boolean(dashboard?.ffmpeg_available), icon: Film },
            ].map(({ label, detail, ready, icon: Icon }) => (
              <div key={label} className="flex items-center gap-3 border-b py-3 last:border-0">
                <span className="grid size-9 place-items-center rounded-lg bg-muted text-muted-foreground"><Icon className="size-4" /></span>
                <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{label}</span><span className="block truncate text-xs capitalize text-muted-foreground">{detail}</span></span>
                {loading ? <LoaderCircle className="size-4 animate-spin text-muted-foreground" /> : ready ? <Check className="size-4 text-[var(--success)]" aria-label="Ready" /> : <span className="size-2 rounded-full bg-destructive" aria-label="Unavailable" />}
              </div>
            ))}
            <Link href="/health" className={cn(buttonVariants({ variant: 'ghost' }), 'mt-2 w-full justify-between')}>Full health report <ArrowRight data-icon="inline-end" /></Link>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-5 border-none shadow-[0_8px_30px_rgb(18_42_66/5%)]">
        <CardHeader className="border-b">
          <CardTitle>Storage paths</CardTitle>
          <CardDescription>Authenticated container locations and currently available space. Host paths remain in Settings.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          {dashboard ? Object.entries(dashboard.storage_details).map(([name, detail]) => (
            <div key={name} className="rounded-lg border bg-muted/25 p-3">
              <div className="flex items-center justify-between"><span className="text-xs font-medium capitalize">{name.replaceAll('_', ' ')}</span>{detail.ready ? <Check className="size-4 text-[var(--success)]" aria-label="Ready" /> : <span className="size-2 rounded-full bg-destructive" aria-label="Unavailable" />}</div>
              <code className="mt-2 block truncate text-[11px] text-muted-foreground" title={detail.configured_path}>{detail.configured_path}</code>
              <p className="mt-1 text-xs text-muted-foreground">{formatBytes(detail.free_bytes)}</p>
            </div>
          )) : [0, 1, 2].map((item) => <Skeleton key={item} className="h-20" />)}
        </CardContent>
      </Card>

      <section className="mt-8" aria-labelledby="recent-heading">
        <div className="mb-4 flex items-center justify-between">
          <div><h2 id="recent-heading" className="text-lg font-semibold tracking-[-0.02em]">Recently discovered</h2><p className="text-sm text-muted-foreground">Local filenames and embedded details only</p></div>
          <Link href="/media" className={buttonVariants({ variant: 'ghost' })}>View all <ArrowRight data-icon="inline-end" /></Link>
        </div>
        {media.length ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {media.map((item, index) => (
              <article key={item.id} className="overflow-hidden rounded-xl border bg-card shadow-[0_8px_30px_rgb(18_42_66/4%)]">
                <div className={`aspect-[16/10] bg-gradient-to-br ${['from-[#15466f] to-[#0b1e34]', 'from-[#2b5c76] to-[#102d42]', 'from-[#356b70] to-[#132f36]', 'from-[#395783] to-[#172643]'][index % 4]} p-4`}>
                  <div className="flex h-full items-end justify-between"><Film className="size-7 text-white/55" /><span className="rounded-md bg-black/20 px-2 py-1 text-[10px] font-medium text-white/80 backdrop-blur">LOCAL</span></div>
                </div>
                <div className="p-3"><h3 className="truncate text-sm font-medium">{item.title}</h3><p className="mt-1 truncate text-xs capitalize text-muted-foreground">{item.kind.replaceAll('_', ' ')}{item.year ? ` · ${item.year}` : ''}</p></div>
              </article>
            ))}
          </div>
        ) : loading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[0, 1, 2, 3].map((item) => <Skeleton key={item} className="aspect-[16/10]" />)}</div>
        ) : (
          <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Film /></EmptyMedia><EmptyTitle>No media discovered yet</EmptyTitle><EmptyDescription>Scan an enabled library to populate local results.</EmptyDescription></EmptyHeader></Empty>
        )}
      </section>
    </>
  );
}
