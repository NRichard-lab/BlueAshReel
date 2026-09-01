'use client';

import { useCallback, useEffect, useState } from 'react';
import { Check, CircleGauge, Database, Film, HardDrive, RefreshCw, Server, TriangleAlert } from 'lucide-react';

import { PageHeader } from '@/components/page-header';
import { PlaybackHealth } from '@/components/active-streams';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError, apiRequest, type DashboardResult } from '@/lib/api';

interface ReadyResponse { status: string; checks: Record<string, string | boolean> }
interface VersionResponse { version: string; product?: string; api_version?: string }

export function SystemHealth() {
  const [live, setLive] = useState<string>();
  const [ready, setReady] = useState<ReadyResponse>();
  const [version, setVersion] = useState<VersionResponse>();
  const [dashboard, setDashboard] = useState<DashboardResult>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [checkedAt, setCheckedAt] = useState<Date>();

  const load = useCallback(async () => {
    setError(undefined);
    try {
      const [liveResult, readyResult, versionResult, dashboardResult] = await Promise.all([
        apiRequest<{ status: string }>('/health/live'), apiRequest<ReadyResponse>('/health/ready'), apiRequest<VersionResponse>('/version'), apiRequest<DashboardResult>('/dashboard'),
      ]);
      setLive(liveResult.status); setReady(readyResult); setVersion(versionResult); setDashboard(dashboardResult); setCheckedAt(new Date());
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 401) { window.location.replace('/login'); return; }
      setError(caught instanceof Error ? caught.message : 'Health checks could not complete.');
    }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { queueMicrotask(() => void load()); const timer = window.setInterval(() => void load(), 15000); return () => window.clearInterval(timer); }, [load]);
  const readyOk = ready?.status === 'ready' || ready?.status === 'ok';

  return (
    <>
      <PageHeader eyebrow="Local observability" title="System health" description="Operational checks avoid secrets, user information, media titles, and full filesystem paths." actions={<Button variant="outline" onClick={load}><RefreshCw data-icon="inline-start" /> Check now</Button>} />
      {error ? <Alert variant="destructive" className="mt-6"><TriangleAlert /><AlertTitle>Health check incomplete</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {[
          { label: 'Server', value: live, ok: live === 'ok', icon: Server, detail: 'Liveness response' },
          { label: 'Readiness', value: ready?.status, ok: readyOk, icon: CircleGauge, detail: 'Required local dependencies' },
          { label: 'Database', value: ready?.checks?.database, ok: ready?.checks?.database === true || ready?.checks?.database === 'ok', icon: Database, detail: 'SQLite WAL connection' },
          { label: 'FFprobe', value: ready?.checks?.ffprobe, ok: ready?.checks?.ffprobe === true || ready?.checks?.ffprobe === 'ok', icon: Film, detail: 'Local media analysis' },
          { label: 'FFmpeg', value: ready?.checks?.ffmpeg, ok: ready?.checks?.ffmpeg === true || ready?.checks?.ffmpeg === 'ok', icon: Film, detail: 'Local playback processing' },
        ].map(({ label, value, ok, icon: Icon, detail }) => <Card key={label}><CardHeader><span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><Icon className="size-4" /></span><CardTitle className="mt-2">{label}</CardTitle><CardDescription>{detail}</CardDescription></CardHeader><CardContent>{loading ? <Skeleton className="h-6 w-20" /> : <Badge variant={ok ? 'secondary' : 'destructive'} className="capitalize">{ok ? <Check data-icon="inline-start" /> : null}{String(value ?? 'unknown')}</Badge>}</CardContent></Card>)}
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card><CardHeader className="border-b"><CardTitle>Application build</CardTitle><CardDescription>Version information safe for local diagnostics.</CardDescription></CardHeader><CardContent className="space-y-3"><div className="flex justify-between border-b pb-3 text-sm"><span className="text-muted-foreground">Version</span><code>{version?.version ?? '—'}</code></div><div className="flex justify-between border-b pb-3 text-sm"><span className="text-muted-foreground">API</span><code>{version?.api_version ?? 'v1'}</code></div><div className="flex justify-between text-sm"><span className="text-muted-foreground">Last checked</span><span>{checkedAt?.toLocaleTimeString() ?? '—'}</span></div></CardContent></Card>
        <Card><CardHeader className="border-b"><CardTitle>Configured storage</CardTitle><CardDescription>Only readiness is shown here; filesystem paths remain on the server.</CardDescription></CardHeader><CardContent className="space-y-3">{dashboard ? Object.entries(dashboard.storage).map(([name, ok]) => <div key={name} className="flex items-center gap-3 rounded-lg border p-3"><span className="grid size-8 place-items-center rounded-lg bg-muted"><HardDrive className="size-4" /></span><span className="flex-1 text-sm capitalize">{name.replaceAll('_', ' ')}</span><Badge variant={ok ? 'secondary' : 'destructive'}>{ok ? 'Ready' : 'Check'}</Badge></div>) : [0, 1, 2].map((item) => <Skeleton key={item} className="h-14" />)}</CardContent></Card>
      </div>
      <PlaybackHealth />
    </>
  );
}
