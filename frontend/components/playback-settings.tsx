'use client';

import { productConfig } from '@/lib/product-config';

import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Check, LoaderCircle, Save } from 'lucide-react';
import Link from 'next/link';
import { PageHeader } from '@/components/page-header';
import { PlaybackHardware } from '@/components/playback-hardware';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOptGroup, NativeSelectOption } from '@/components/ui/native-select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { ApiError, apiRequest, jsonBody } from '@/lib/api';
import {
  cpuPresets, hardwareEncoderLabels, transcodingModes, transcodingPolicyBody,
  type HardwareEncoder, type PlaybackHealthRecord, type TranscodingMode,
  type TranscodingPolicy, type TranscodingPolicyRecord,
} from '@/lib/transcoding';

export function PlaybackSettings() {
  const [draft, setDraft] = useState<TranscodingPolicyRecord>();
  const [health, setHealth] = useState<PlaybackHealthRecord>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const [healthError, setHealthError] = useState('');
  const [notice, setNotice] = useState('');

  const loadHealth = useCallback(async () => {
    try { setHealth(await apiRequest<PlaybackHealthRecord>('/playback-health')); setHealthError(''); }
    catch (caught: unknown) { setHealth(undefined); setHealthError(caught instanceof Error ? caught.message : 'Hardware status could not be read.'); }
  }, []);

  useEffect(() => {
    void apiRequest<TranscodingPolicyRecord>('/transcoding-policy')
      .then(setDraft)
      .catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.status === 401) { window.location.replace('/login'); return; }
        setError(caught instanceof Error ? caught.message : 'Playback settings could not be loaded.');
      }).finally(() => setLoading(false));
    void loadHealth();
    const timer = window.setInterval(() => void loadHealth(), 15000);
    return () => window.clearInterval(timer);
  }, [loadHealth]);

  function update<K extends keyof TranscodingPolicy>(key: K, value: TranscodingPolicy[K]) {
    setDraft(current => current ? { ...current, [key]: value } : current);
    setNotice('');
  }

  async function save(event: React.SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft) return;
    if (draft.mode === 'hardware_required' && draft.preferred_hardware === 'auto') {
      setError('Hardware Required needs an explicitly selected Intel Quick Sync, NVIDIA NVENC, or AMD AMF encoder.');
      return;
    }
    setSaving(true); setError(''); setNotice('');
    try {
      const result = await apiRequest<TranscodingPolicyRecord>('/transcoding-policy', {
        method: 'PATCH', body: jsonBody(transcodingPolicyBody(draft)),
      });
      setDraft(result);
      setNotice('Playback settings saved. New streams use these settings; existing playback is not interrupted.');
      await loadHealth();
    } catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'Playback settings were not saved.'); }
    finally { setSaving(false); }
  }

  async function retest() {
    setTesting(true); setHealthError('');
    try { await apiRequest('/playback-health/detect', { method: 'POST' }); await loadHealth(); }
    catch (caught: unknown) { setHealth(undefined); setHealthError(caught instanceof Error ? caught.message : 'Hardware test did not complete.'); }
    finally { setTesting(false); }
  }

  const softwareLocked = draft?.mode === 'direct_only' || draft?.mode === 'hardware_required';
  const hardwareLocked = draft?.mode === 'software_only' || draft?.mode === 'direct_only';
  return <>
    <Link href="/settings" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Settings</Link>
    <PageHeader eyebrow="Owner controls" title="Playback & Transcoding" description="Choose how this server plays local media. Changes apply to new streams, without terminating existing playback." />
    {error ? <Alert variant="destructive" className="mt-6"><AlertTitle>Playback settings action failed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    {notice ? <Alert className="mt-6 border-[var(--success-border)] bg-[var(--success-soft)]"><Check /><AlertTitle>Saved</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert> : null}
    {loading ? <Skeleton className="mt-6 h-80" /> : draft ? <form onSubmit={save} className="mt-6">
      <fieldset disabled={saving} className="space-y-5">
        <Card>
          <CardHeader className="border-b"><CardTitle>Playback mode</CardTitle><CardDescription>Compatible media still uses Direct Play or remux in every mode. Automatic is the default for new installations.</CardDescription></CardHeader>
          <CardContent><FieldGroup>
            <Field>
              <FieldLabel htmlFor="transcoding-mode">Mode</FieldLabel>
              <NativeSelect id="transcoding-mode" className="w-full" value={draft.mode} onChange={event => update('mode', event.target.value as TranscodingMode)}>
                {Object.entries(transcodingModes).filter(([key]) => key !== 'hardware_required').map(([key, value]) => <NativeSelectOption key={key} value={key}>{value.label}</NativeSelectOption>)}
                <NativeSelectOptGroup label="Advanced"><NativeSelectOption value="hardware_required">Hardware Required</NativeSelectOption></NativeSelectOptGroup>
              </NativeSelect>
              <FieldDescription>{transcodingModes[draft.mode].description}</FieldDescription>
            </Field>
            {draft.mode === 'hardware_required' ? <Alert><AlertTitle>Hardware Required has no CPU fallback</AlertTitle><AlertDescription>Select a hardware encoder and device that pass the local test. New incompatible streams fail clearly if this encoder is unavailable or fails.</AlertDescription></Alert> : null}
            {draft.mode === 'direct_only' ? <Alert><AlertTitle>Transcoding is disabled</AlertTitle><AlertDescription>Video and audio that need conversion cannot play in this mode. The limits below are retained for use when transcoding is enabled again.</AlertDescription></Alert> : null}
          </FieldGroup></CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b"><CardTitle>Resource limits</CardTitle><CardDescription>Conservative limits help leave capacity for other work on this computer. These do not resize or modify your source files.</CardDescription></CardHeader>
          <CardContent className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
            <Field><FieldLabel htmlFor="max-transcodes">Maximum simultaneous transcodes</FieldLabel><Input id="max-transcodes" type="number" required min="1" max="8" step="1" value={draft.max_processes} onChange={event => update('max_processes', Number(event.target.value))} /><FieldDescription>Limits local conversions, including remux jobs. Lower this if playback overloads your computer.</FieldDescription></Field>
            <Field><FieldLabel htmlFor="max-output-height">Maximum output resolution</FieldLabel><NativeSelect id="max-output-height" className="w-full" value={draft.max_height} onChange={event => update('max_height', Number(event.target.value))}>{[240, 360, 480, 720, 1080, 1440, 2160, 4320].map(height => <NativeSelectOption key={height} value={height}>{height}p</NativeSelectOption>)}{![240, 360, 480, 720, 1080, 1440, 2160, 4320].includes(draft.max_height) ? <NativeSelectOption value={draft.max_height}>{draft.max_height}p (configured)</NativeSelectOption> : null}</NativeSelect><FieldDescription>Ceiling for converted video, not compatible Direct Play.</FieldDescription></Field>
            <Field><FieldLabel htmlFor="max-output-bitrate">Maximum output bitrate (kbps)</FieldLabel><Input id="max-output-bitrate" type="number" required min="500" max="50000" step="1" value={draft.max_bitrate_kbps} onChange={event => update('max_bitrate_kbps', Number(event.target.value))} /><FieldDescription>Higher bitrates use more local storage and network bandwidth.</FieldDescription></Field>
            <Field><FieldLabel htmlFor="cpu-preset">CPU encoding preset</FieldLabel><NativeSelect id="cpu-preset" className="w-full" disabled={softwareLocked} value={draft.cpu_preset} onChange={event => update('cpu_preset', event.target.value as TranscodingPolicy['cpu_preset'])}>{cpuPresets.map(preset => <NativeSelectOption key={preset} value={preset}>{preset}</NativeSelectOption>)}</NativeSelect><FieldDescription>Veryfast is a conservative starting point. Slower presets require more CPU.</FieldDescription></Field>
            <Field orientation="horizontal" className="rounded-lg border p-3"><div className="flex-1"><FieldLabel htmlFor="allow-4k">Permit 4K transcoding</FieldLabel><FieldDescription>4K conversion is expensive. Direct Play of compatible 4K media is unaffected.</FieldDescription></div><Switch id="allow-4k" checked={draft.allow_4k} onCheckedChange={checked => update('allow_4k', checked)} /></Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b"><CardTitle>Hardware preference</CardTitle><CardDescription>Preferences do not certify hardware. The test results below determine actual availability.</CardDescription></CardHeader>
          <CardContent className="grid gap-5 sm:grid-cols-2">
            <Field><FieldLabel htmlFor="preferred-hardware">Preferred hardware encoder</FieldLabel><NativeSelect id="preferred-hardware" className="w-full" disabled={hardwareLocked} value={draft.preferred_hardware} onChange={event => update('preferred_hardware', event.target.value as HardwareEncoder)}>{Object.entries(hardwareEncoderLabels).map(([key, label]) => <NativeSelectOption key={key} value={key}>{label}</NativeSelectOption>)}</NativeSelect><FieldDescription>Unavailable encoders are never silently represented as verified.</FieldDescription></Field>
            <Field><FieldLabel htmlFor="hardware-device">Hardware device</FieldLabel><Input id="hardware-device" disabled={hardwareLocked} required pattern="auto|[0-9]+" value={draft.hardware_device} onChange={event => update('hardware_device', event.target.value)} /><FieldDescription>Use auto, or a numeric adapter index when multiple devices exist. Save, then retest the selected device.</FieldDescription></Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b"><CardTitle>Temporary storage & subtitles</CardTitle><CardDescription>Only {productConfig.name}-owned temporary output is cleaned up. Source media is never removed.</CardDescription></CardHeader>
          <CardContent className="grid gap-5 sm:grid-cols-2">
            <Field className="sm:col-span-2"><FieldLabel htmlFor="transcode-directory">Temporary transcode directory</FieldLabel><Input id="transcode-directory" required value={draft.temp_directory} onChange={event => update('temp_directory', event.target.value)} /><FieldDescription>Use an absolute, writable directory dedicated to {productConfig.name} within application-managed storage. Do not choose a media folder.</FieldDescription></Field>
            <Field><FieldLabel htmlFor="max-temp-storage">Maximum temporary-storage use (MiB)</FieldLabel><Input id="max-temp-storage" type="number" required min="64" max="1048576" step="1" value={draft.max_storage_mb} onChange={event => update('max_storage_mb', Number(event.target.value))} /><FieldDescription>Playback stops with an explanation if its safe storage limit is reached.</FieldDescription></Field>
            <Field><FieldLabel htmlFor="inactive-cleanup">Inactive-session cleanup (seconds)</FieldLabel><Input id="inactive-cleanup" type="number" required min="30" max="600" step="1" value={draft.inactive_session_seconds} onChange={event => update('inactive_session_seconds', Number(event.target.value))} /><FieldDescription>Abandoned sessions and their local conversion processes are cleaned up after this timeout.</FieldDescription></Field>
            <Field className="sm:col-span-2"><FieldLabel>Currently supported subtitle behavior</FieldLabel><p className="text-sm">{draft.subtitle_behavior}</p><FieldDescription>Choose an available subtitle track or Off in the player. Unsupported formats are reported; no unimplemented burn-in option is offered.</FieldDescription></Field>
          </CardContent>
        </Card>
        <div className="flex justify-end"><Button type="submit">{saving ? <LoaderCircle className="animate-spin" /> : <Save />} Save playback settings</Button></div>
      </fieldset>
    </form> : null}
    <PlaybackHardware data={health} busy={testing} error={healthError} onRetest={() => void retest()} />
  </>;
}
