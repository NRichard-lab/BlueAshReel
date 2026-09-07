'use client';

import { useEffect, useState } from 'react';
import { ArrowRight, Check, Cpu, FileClock, HardDrive, LoaderCircle, Save } from 'lucide-react';
import Link from 'next/link';

import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious } from '@/components/ui/pagination';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { ApiError, apiRequest, jsonBody, type PageResult } from '@/lib/api';

interface SettingsRecord {
  application_data_directory: string;
  temporary_directory: string;
  artwork_directory: string;
  scan_extensions: string[];
  ignored_directories: string[];
}

interface AuditRecord {
  id: number;
  event_type: string;
  target_type?: string | null;
  outcome: string;
  details: Record<string, unknown>;
  created_at: string;
}

export function SettingsManager() {
  const [settings, setSettings] = useState<SettingsRecord>();
  const [extensions, setExtensions] = useState('');
  const [ignored, setIgnored] = useState('');
  const [audit, setAudit] = useState<AuditRecord[]>([]);
  const [auditPage, setAuditPage] = useState(1);
  const [auditTotal, setAuditTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  useEffect(() => {
    Promise.all([
      apiRequest<SettingsRecord>('/settings'),
      apiRequest<PageResult<AuditRecord>>(`/audit-events?page=${auditPage}&page_size=20`),
    ]).then(([configuration, events]) => {
      setSettings(configuration);
      setExtensions(configuration.scan_extensions.join(', '));
      setIgnored(configuration.ignored_directories.join('\n'));
      setAudit(events.items);
      setAuditTotal(events.total);
    }).catch((caught: unknown) => {
      if (caught instanceof ApiError && caught.status === 401) { window.location.replace('/login'); return; }
      setError(caught instanceof Error ? caught.message : 'Settings could not be loaded.');
    }).finally(() => setLoading(false));
  }, [auditPage]);

  const save = async () => {
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      const updated = await apiRequest<SettingsRecord>('/settings', {
        method: 'PATCH',
        body: jsonBody({
          scan_extensions: extensions.split(',').map((value) => value.trim().toLowerCase()).filter(Boolean),
          ignored_directories: ignored.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        }),
      });
      setSettings(updated); setExtensions(updated.scan_extensions.join(', ')); setIgnored(updated.ignored_directories.join('\n')); setNotice('Scanner settings saved. New scans will use these values.');
    } catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'Settings were not saved.'); }
    finally { setSaving(false); }
  };

  return (
    <>
      <PageHeader eyebrow="Local configuration" title="Settings" description="Tune local playback and scanning, review storage, and inspect important Owner actions." />
      <Card className="mt-6"><CardHeader><CardTitle>Remote Access</CardTitle><CardDescription>Pair your Agent, verify its identity, and control the optional encrypted diagnostic connection.</CardDescription></CardHeader><CardContent><Button variant="outline" render={<Link href="/admin/settings/remote-access" />}>Remote Access <ArrowRight className="ml-auto" /></Button></CardContent></Card>
      <Card className="mt-6"><CardHeader><CardTitle className="flex items-center gap-2"><Cpu className="size-4" /> Playback & Transcoding</CardTitle><CardDescription>Choose Automatic, hardware, software, or Direct Play/remux-only behavior. Verify hardware and set safe resource limits.</CardDescription></CardHeader><CardContent><Button variant="outline" render={<Link href="/admin/settings/playback" />}>Playback & Transcoding <ArrowRight className="ml-auto" /></Button></CardContent></Card>
      {error ? <Alert variant="destructive" className="mt-6"><AlertTitle>Settings action failed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
      {notice ? <Alert className="mt-6 border-[var(--success-border)] bg-[var(--success-soft)]"><Check /><AlertTitle>Saved</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert> : null}
      <div className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(340px,.65fr)]">
        <Card>
          <CardHeader className="border-b"><CardTitle>Scanner configuration</CardTitle><CardDescription>Applied to future full and changed-file scans.</CardDescription></CardHeader>
          <CardContent>
            {loading ? <div className="space-y-4"><Skeleton className="h-16" /><Skeleton className="h-24" /></div> : <FieldGroup>
              <Field><FieldLabel htmlFor="extensions">Supported extensions</FieldLabel><Input id="extensions" value={extensions} onChange={(event) => setExtensions(event.target.value)} /><FieldDescription>Comma-separated lowercase extensions, including the leading dot.</FieldDescription></Field>
              <Field><FieldLabel htmlFor="ignored-directories">Ignored directory names</FieldLabel><Textarea id="ignored-directories" value={ignored} onChange={(event) => setIgnored(event.target.value)} rows={7} /><FieldDescription>One directory name per line. These names are skipped anywhere in a library tree.</FieldDescription></Field>
              <Button className="self-end" disabled={saving} onClick={save}>{saving ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <Save data-icon="inline-start" />} Save scanner settings</Button>
            </FieldGroup>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="border-b"><CardTitle>Storage locations</CardTitle><CardDescription>Visible only to an authenticated Owner.</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            {loading ? [0, 1, 2].map((item) => <Skeleton key={item} className="h-14" />) : [
              ['Application data', settings?.application_data_directory], ['Temporary files', settings?.temporary_directory], ['Artwork cache', settings?.artwork_directory],
            ].map(([label, value]) => <div key={label} className="rounded-lg border bg-muted/20 p-3"><p className="text-xs font-medium">{label}</p><code className="mt-1 block truncate text-[11px] text-muted-foreground" title={value}>{value}</code></div>)}
            {!loading ? <Button variant="outline" className="w-full" render={<Link href="/admin/settings/media-storage" />}><HardDrive /> Media Storage <ArrowRight className="ml-auto" /></Button> : null}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-5">
        <CardHeader className="border-b"><CardTitle className="flex items-center gap-2"><FileClock className="size-4" /> Local audit log</CardTitle><CardDescription>Recent setup, authentication, library, scan, privacy, and configuration actions.</CardDescription></CardHeader>
        <CardContent className="p-0">
          <Table><TableHeader><TableRow><TableHead>Event</TableHead><TableHead>Target</TableHead><TableHead>Outcome</TableHead><TableHead>Time</TableHead></TableRow></TableHeader><TableBody>{audit.map((event) => <TableRow key={event.id}><TableCell className="font-medium capitalize">{event.event_type.replaceAll('_', ' ')}</TableCell><TableCell>{event.target_type ?? 'Application'}</TableCell><TableCell><Badge variant={event.outcome === 'success' ? 'secondary' : 'destructive'}>{event.outcome}</Badge></TableCell><TableCell>{new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(event.created_at))}</TableCell></TableRow>)}</TableBody></Table>
          {!loading && !audit.length ? <p className="p-6 text-center text-sm text-muted-foreground">No audit events recorded yet.</p> : null}
          {auditTotal > 20 ? <Pagination className="border-t py-3"><PaginationContent><PaginationItem><PaginationPrevious href="#" aria-disabled={auditPage <= 1} onClick={(event) => { event.preventDefault(); if (auditPage > 1) setAuditPage((current) => current - 1); }} /></PaginationItem><PaginationItem><span className="px-3 text-sm tabular-nums">{auditPage} / {Math.ceil(auditTotal / 20)}</span></PaginationItem><PaginationItem><PaginationNext href="#" aria-disabled={auditPage >= Math.ceil(auditTotal / 20)} onClick={(event) => { event.preventDefault(); if (auditPage < Math.ceil(auditTotal / 20)) setAuditPage((current) => current + 1); }} /></PaginationItem></PaginationContent></Pagination> : null}
        </CardContent>
      </Card>
    </>
  );
}
