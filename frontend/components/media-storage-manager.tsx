'use client';

import { useEffect, useState } from 'react';
import { ArrowLeft, CheckCircle2, ChevronDown, CircleAlert, FolderOpen, HardDrive, RefreshCw, ShieldCheck } from 'lucide-react';
import Link from 'next/link';

import { FolderBrowserDialog } from '@/components/media-folder-picker';
import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError, apiRequest, type MediaFolderSelection, type MediaPlatform, type MediaRootList, type MediaRootSummary } from '@/lib/api';

function validationTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

export function MediaStorageManager() {
  const [roots, setRoots] = useState<MediaRootSummary[]>([]);
  const [platform, setPlatform] = useState<MediaPlatform>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [browseRoot, setBrowseRoot] = useState<string>();
  const [preview, setPreview] = useState<MediaFolderSelection>();

  const load = async () => {
    setLoading(true); setError(undefined);
    try {
      const result = await apiRequest<MediaRootList>('/media-storage');
      setRoots(result.items);
      setPlatform(result.platform ?? 'docker');
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 401) {
        window.location.replace('/login');
        return;
      }
      setError(caught instanceof Error ? caught.message : 'Approved media storage could not be checked.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { queueMicrotask(() => void load()); }, []);

  return <>
    <Link href="/settings" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Settings</Link>
    <PageHeader
      eyebrow="Settings"
      title="Media Storage"
      description={platform === 'windows' ? 'Review approved Windows folders that BlueReel reads directly. Source media is never modified.' : platform === 'docker' ? 'Review host folders that were explicitly mounted for BlueReel to read.' : 'Review media storage approved for this installation.'}
      actions={<Button variant="outline" disabled={loading} onClick={() => void load()}><RefreshCw className={loading ? 'animate-spin' : ''} /> Check storage</Button>}
    />

    {error ? <Alert variant="destructive" className="mt-6"><CircleAlert /><AlertTitle>Storage check failed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    {preview ? <Alert className="mt-6 border-[var(--success-border)] bg-[var(--success-soft)]"><FolderOpen /><AlertTitle>Folder selected for review</AlertTitle><AlertDescription>{preview.display_path} is readable. Browsing here does not add, scan, or change a library.</AlertDescription></Alert> : null}

    <section className="mt-6 grid gap-4 lg:grid-cols-2" aria-label="Approved media roots">
      {loading ? [0, 1].map((item) => <Skeleton key={item} className="h-72" />) : null}
      {!loading && roots.map((root) => {
        const ready = root.available && root.readable && root.read_only_enforced !== false;
        return <Card key={root.id}>
          <CardHeader className="border-b">
            <div className="flex items-start justify-between gap-3">
              <span className="grid size-10 place-items-center rounded-xl bg-primary/10 text-primary"><HardDrive className="size-5" /></span>
              <Badge variant={ready ? 'secondary' : 'destructive'}>{root.read_only_enforced === false ? 'Not read-only' : ready ? 'Available' : root.status === 'permission_denied' ? 'Permission denied' : 'Unavailable'}</Badge>
            </div>
            <CardTitle>{root.display_name}</CardTitle>
            <CardDescription>Last checked {validationTime(root.last_validated_at)}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <div className="rounded-lg bg-muted/35 p-3"><dt className="text-xs text-muted-foreground">Readable</dt><dd className="mt-1 flex items-center gap-1 font-medium">{root.readable ? <CheckCircle2 className="size-4 text-[var(--success)]" /> : <CircleAlert className="size-4 text-destructive" />}{root.readable ? 'Yes' : 'No'}</dd></div>
              <div className="rounded-lg bg-muted/35 p-3"><dt className="text-xs text-muted-foreground">Read-only enforced</dt><dd className="mt-1 flex items-center gap-1 font-medium"><ShieldCheck className="size-4 text-primary" />{root.read_only_enforced === true ? 'Yes' : root.read_only_enforced === false ? 'No' : 'Not verified'}</dd></div>
            </dl>
            <div>
              <p className="text-xs font-medium text-muted-foreground">Libraries using this storage</p>
              {root.libraries.length ? <ul className="mt-2 flex flex-wrap gap-2">{root.libraries.map((library) => <li key={library.id}><Badge variant="outline">{library.name}</Badge></li>)}</ul> : <p className="mt-1 text-sm">No libraries yet.</p>}
            </div>
            {root.read_only_enforced === false ? <Alert variant="destructive"><ShieldCheck /><AlertTitle>Storage protection check failed</AlertTitle><AlertDescription>{platform === 'windows' ? 'This source does not meet the configured media access policy. Review service-account permissions before using it.' : 'This source is writable inside the container. Re-run bootstrap to restore its read-only bind before browsing or scanning it.'}</AlertDescription></Alert> : null}
            <Button variant="outline" disabled={!ready} onClick={() => setBrowseRoot(root.id)}><FolderOpen /> Browse</Button>
            <Collapsible>
              <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="px-0 text-muted-foreground" />}><ChevronDown /> Advanced</CollapsibleTrigger>
              <CollapsibleContent className="rounded-lg border bg-muted/20 p-3 text-xs">
                <dl className="min-w-0 space-y-2"><div className="min-w-0"><dt className="font-medium">Approved root identifier</dt><dd className="break-all"><code>{root.id}</code></dd></div>{root.internal_path ? <div className="min-w-0"><dt className="font-medium">{platform === 'windows' ? 'Windows path' : 'Internal mounted path'}</dt><dd className="break-all"><code>{root.internal_path}</code></dd></div> : null}</dl>
              </CollapsibleContent>
            </Collapsible>
          </CardContent>
        </Card>;
      })}
    </section>

    {!loading && !roots.length ? <Empty className="mt-6 border"><EmptyHeader><EmptyMedia variant="icon"><HardDrive /></EmptyMedia><EmptyTitle>No approved media storage</EmptyTitle><EmptyDescription>{platform === 'windows' ? 'Use the native installer’s media-folder configuration to approve a Windows folder.' : platform === 'docker' ? 'Use the local bootstrap workflow below, then recreate the BlueReel containers.' : 'Configure an approved media root for this installation.'}</EmptyDescription></EmptyHeader></Empty> : null}

    {platform === 'windows' ? <Card className="mt-5">
      <CardHeader className="border-b"><CardTitle>Add or change a Windows folder</CardTitle><CardDescription>Native BlueReel reads approved Windows folders directly. No container paths or drive remapping are needed.</CardDescription></CardHeader>
      <CardContent className="space-y-4 text-sm leading-relaxed">
        <p>Use the native installer&apos;s media-folder configuration to choose one or more approved roots. A library can browse only inside those roots. BlueReel does not move, rename, modify, or delete source files.</p>
        <Alert><ShieldCheck /><AlertTitle>Service access is different from your sign-in</AlertTitle><AlertDescription>A folder readable by your Windows account may not be readable by the BlueReel service. A missing drive or permission problem must be resolved for that service identity. OS-level read-only enforcement is shown only when it has been verified.</AlertDescription></Alert>
        <p>Network shares are an advanced configuration: use an approved UNC path and explicitly grant the service identity access. Your interactive user&apos;s mapped drives may not be visible to services.</p>
      </CardContent>
    </Card> : platform === 'docker' ? <Card className="mt-5">
      <CardHeader className="border-b"><CardTitle>Add or change a host folder</CardTitle><CardDescription>Docker mounts are controlled by the person operating this computer, never by the web application.</CardDescription></CardHeader>
      <CardContent className="space-y-4 text-sm leading-relaxed">
        <p>Run the bootstrap from the BlueReel repository and explicitly configure one or more media roots. The script validates each host directory, writes only ignored local configuration, and mounts it read-only in the backend and worker.</p>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border bg-muted/20 p-4"><p className="font-medium">Windows PowerShell</p><code className="mt-2 block overflow-x-auto text-xs">.\scripts\bootstrap.ps1 -ConfigureMediaRoots</code></div>
          <div className="rounded-lg border bg-muted/20 p-4"><p className="font-medium">Linux shell</p><code className="mt-2 block overflow-x-auto text-xs">./scripts/bootstrap.sh --configure-media-roots</code></div>
        </div>
        <Alert><RefreshCw /><AlertTitle>Container recreation required</AlertTitle><AlertDescription>Adding or changing a host root takes effect only after the controlled Docker Compose recreation performed by the bootstrap. BlueReel cannot create host mounts from this page.</AlertDescription></Alert>
      </CardContent>
    </Card> : null}

    <FolderBrowserDialog
      open={Boolean(browseRoot)}
      onOpenChange={(open) => { if (!open) setBrowseRoot(undefined); }}
      initialRootId={browseRoot}
      onSelect={(selection) => setPreview(selection)}
    />
  </>;
}
