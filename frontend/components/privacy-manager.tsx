'use client';

import { useEffect, useState } from 'react';
import { Check, CloudOff, HardDrive, LoaderCircle, LockKeyhole, Save, ShieldCheck, WifiOff } from 'lucide-react';

import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { ApiError, apiRequest, jsonBody } from '@/lib/api';
import { networkEnforcementLabel, type NetworkEnforcement } from '@/lib/privacy';

interface PrivacyRecord {
  local_only: boolean;
  telemetry_enabled: boolean;
  runtime_outbound_allowed: boolean;
  integrations: Record<string, boolean>;
  network_enforcement?: NetworkEnforcement;
}

export function PrivacyManager() {
  const [privacy, setPrivacy] = useState<PrivacyRecord>();
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  useEffect(() => {
    apiRequest<PrivacyRecord>('/privacy').then((result) => { setPrivacy(result); setDraft(result.integrations); }).catch((caught: unknown) => {
      if (caught instanceof ApiError && caught.status === 401) { window.location.replace('/login'); return; }
      setError(caught instanceof Error ? caught.message : 'Privacy controls could not be loaded.');
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const refresh = () => { void apiRequest<PrivacyRecord>('/privacy').then(setPrivacy).catch(() => setPrivacy(undefined)); };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener('focus', refresh);
    return () => { window.clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, []);

  const save = async () => {
    setSaving(true); setError(undefined); setNotice(undefined);
    try { const result = await apiRequest<PrivacyRecord>('/privacy', { method: 'PATCH', body: jsonBody({ integrations: draft }) }); setPrivacy(result); setDraft(result.integrations); window.dispatchEvent(new CustomEvent('privacy-posture-changed', { detail: result })); setNotice('Outbound integration controls saved and audited.'); }
    catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'Privacy controls were not saved.'); }
    finally { setSaving(false); }
  };

  return (
    <>
      <PageHeader eyebrow="Private by default" title="Privacy & outbound connections" description="Media, watch activity, users, searches, paths, and library details remain on this server." />
      {error ? <Alert variant="destructive" className="mt-6"><AlertTitle>Privacy action failed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
      {notice ? <Alert className="mt-6 border-[var(--success-border)] bg-[var(--success-soft)]"><Check /><AlertTitle>Saved</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert> : null}

      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {[
          { icon: HardDrive, title: 'Local processing', text: 'Media analysis, subtitles, remuxing and transcoding run on your hardware.' },
          { icon: CloudOff, title: 'No media uploads', text: 'Source media is never sent to an external service.' },
          { icon: LockKeyhole, title: 'No tracking', text: 'No analytics, advertising, tracking pixels, or external crash reporting.' },
        ].map(({ icon: Icon, title, text }) => <Card key={title}><CardHeader><span className="mb-2 grid size-10 place-items-center rounded-xl bg-primary/10 text-primary"><Icon className="size-5" /></span><CardTitle>{title}</CardTitle><CardDescription>{text}</CardDescription></CardHeader></Card>)}
      </div>

      <Card className="mt-5">
        <CardHeader className="border-b"><CardTitle>Runtime posture</CardTitle><CardDescription>Core operation does not require Internet access.</CardDescription></CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          {loading ? [0, 1, 2].map((item) => <Skeleton key={item} className="h-20" />) : !privacy ? <output>Live privacy status is unavailable. No enforcement claim can be shown.</output> : <>
            <div className="rounded-lg border p-3"><div className="flex items-center justify-between"><span className="text-sm font-medium">Local-only</span><Badge variant="secondary">{privacy?.local_only ? 'Yes' : 'No'}</Badge></div><p className="mt-2 text-xs text-muted-foreground">Primary architecture mode</p></div>
            <div className="rounded-lg border p-3"><div className="flex items-center justify-between"><span className="text-sm font-medium">Telemetry</span><Badge variant={privacy?.telemetry_enabled ? 'destructive' : 'secondary'}>{privacy?.telemetry_enabled ? 'On' : 'Off'}</Badge></div><p className="mt-2 text-xs text-muted-foreground">Application analytics</p></div>
            <div className="rounded-lg border p-3"><div className="flex items-center justify-between"><span className="text-sm font-medium">Runtime outbound</span><Badge variant={privacy?.runtime_outbound_allowed ? 'outline' : 'secondary'}>{privacy?.runtime_outbound_allowed ? 'Permitted' : 'Disabled'}</Badge></div><p className="mt-2 text-xs text-muted-foreground">Application policy gate. OS/network enforcement is reported separately below.</p></div>
          </>}
        </CardContent>
      </Card>

      <Card className="mt-5">
        <CardHeader className="border-b"><CardTitle>Network enforcement</CardTitle><CardDescription>Actual deployment enforcement, separate from the saved application policy. No global firewall policy is changed by BlueReel.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {loading ? <Skeleton className="h-20" /> : <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm font-medium">{privacy?.network_enforcement?.platform === 'windows' ? 'Windows application-specific firewall' : privacy?.network_enforcement?.platform === 'docker' ? 'Docker network isolation' : 'Deployment firewall'}</span>
              <Badge variant={privacy?.network_enforcement?.status === 'enforced' ? 'secondary' : privacy?.network_enforcement?.status === 'not_enforced' ? 'destructive' : 'outline'}>{networkEnforcementLabel(privacy?.network_enforcement?.status)}</Badge>
            </div>
            <p className="text-sm text-muted-foreground">{privacy?.network_enforcement?.detail || 'A live enforcement check is unavailable. The application policy alone does not prove network isolation.'}</p>
            {privacy?.network_enforcement?.checked_at ? <p className="text-xs text-muted-foreground">Last checked: {new Date(privacy.network_enforcement.checked_at).toLocaleString()}</p> : null}
            {privacy?.network_enforcement?.platform === 'windows' && privacy.network_enforcement.status !== 'enforced' ? <Alert variant="destructive"><ShieldCheck /><AlertTitle>Strict-local enforcement is not confirmed</AlertTitle><AlertDescription>Review the BlueReel installer/service firewall diagnostics. A detected rule or a saved preference is not enough to claim that DNS-name and direct-IP outbound traffic are blocked.</AlertDescription></Alert> : null}
          </>}
        </CardContent>
      </Card>

      <Card className="mt-5">
        <CardHeader className="border-b"><CardTitle className="flex items-center gap-2"><WifiOff className="size-4" /> Future integration switches</CardTitle><CardDescription>Each outbound provider remains explicit, auditable, and Owner-controlled. No provider is contacted in this phase.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {!loading && privacy && !privacy.runtime_outbound_allowed ? (
            <Alert>
              <CloudOff />
              <AlertTitle>Deployment gate is locked</AlertTitle>
              <AlertDescription>Integrations cannot be enabled while the server-wide outbound gate is off. This prevents a saved preference from becoming active later without a deliberate deployment change.</AlertDescription>
            </Alert>
          ) : null}
          {loading ? [0, 1, 2].map((item) => <Skeleton key={item} className="h-14" />) : Object.entries(draft).length ? Object.entries(draft).map(([name, enabled]) => (
            <Field key={name} orientation="horizontal" className="rounded-lg border p-3"><div className="flex-1"><FieldLabel htmlFor={`integration-${name}`} className="capitalize">{name.replaceAll('_', ' ')}</FieldLabel><FieldDescription>{enabled ? 'Enabled by Owner; implementation may still be unavailable.' : privacy?.runtime_outbound_allowed ? 'Disabled. No outbound requests allowed.' : 'Locked off by the deployment-wide outbound gate.'}</FieldDescription></div><Switch id={`integration-${name}`} checked={enabled} disabled={!privacy?.runtime_outbound_allowed && !enabled} onCheckedChange={(checked) => setDraft((current) => ({ ...current, [name]: checked }))} /></Field>
          )) : <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">No integrations are registered. Outbound metadata access remains disabled.</div>}
          <div className="flex justify-end"><Button disabled={saving} onClick={save}>{saving ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <Save data-icon="inline-start" />} Save controls</Button></div>
        </CardContent>
      </Card>

      <Alert className="mt-5 border-[var(--success-border)] bg-[var(--success-soft)]"><ShieldCheck /><AlertTitle>Offline operation is a supported state</AlertTitle><AlertDescription>The core server, database, library scanner, local analysis, and administration website continue to operate without Internet access.</AlertDescription></Alert>
    </>
  );
}
