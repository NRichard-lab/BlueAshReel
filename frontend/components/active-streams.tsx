'use client';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PageHeader } from '@/components/page-header';
import { apiRequest, jsonBody } from '@/lib/api';
import { clockTime } from '@/lib/viewer';
import { methodLabel } from '@/lib/playback';

interface StreamRecord {
  id: string; username: string; method: keyof typeof methodLabel; state: string;
  source_height: number | null; output_height: number; bitrate_kbps: number;
  observed_bitrate_kbps: number | null;
  speed: number | null; encoder: string; elapsed_seconds: number;
  startup_ms: number | null; temp_bytes: number; error: string | null;
}
export interface PlaybackHealthRecord {
  hardware: Record<string, string>; configured: string; active_encoder: string;
  conversions: number; max_conversions: number; temp_bytes: number; max_temp_bytes: number;
  maintenance_error: string | null;
  auxiliary_processes: number;
}

export function PlaybackHealth() {
  const [data, setData] = useState<PlaybackHealthRecord>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => apiRequest<PlaybackHealthRecord>('/playback-health').then(setData).catch(() => setError('Playback health unavailable.')), []);
  useEffect(() => { void load(); }, [load]);
  async function detect() {
    setBusy(true); setError('');
    try { await apiRequest('/playback-health/detect', { method: 'POST' }); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Hardware test failed.'); }
    finally { setBusy(false); }
  }
  return <section className="mt-6 rounded-xl border bg-card p-5">
    <h2 className="text-lg font-semibold">Local playback engine</h2>
    <p className="mt-2 text-sm text-muted-foreground">Hardware is optional. Only a successful local test encode enables the configured accelerator; software remains the fallback.</p>
    {data && <><dl className="my-4 grid gap-3 text-sm sm:grid-cols-2">
      <div><dt className="text-muted-foreground">Configured / current encoder</dt><dd>{data.configured} / {data.active_encoder}</dd></div>
      <div><dt className="text-muted-foreground">Conversion slots</dt><dd>{data.conversions} / {data.max_conversions}</dd></div>
      <div><dt className="text-muted-foreground">Subtitle / probe processes</dt><dd>{data.auxiliary_processes}</dd></div>
      {Object.entries(data.hardware).map(([name, value]) => <div key={name}><dt className="uppercase text-muted-foreground">{name}</dt><dd>{value}</dd></div>)}
    </dl><p className="mb-4 text-sm">Temporary output: {(data.temp_bytes / 1048576).toFixed(1)} / {(data.max_temp_bytes / 1048576).toFixed(0)} MiB</p>
      {data.maintenance_error && <p role="alert" className="my-3 text-destructive">{data.maintenance_error}</p>}</>}
    <Button disabled={busy} variant="outline" onClick={() => void detect()}>{busy ? 'Testing local encoders…' : 'Test hardware encoders'}</Button>
    {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
  </section>;
}

export function ActiveStreams() {
  const [data, setData] = useState<{ items: StreamRecord[]; total: number; health: PlaybackHealthRecord }>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [policy, setPolicy] = useState({ watched_threshold: 90, minimum_watch_seconds: 30, history_days: 365 });
  const [notice, setNotice] = useState('');
  const load = useCallback(async () => {
    try { setData(await apiRequest('/streams')); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : 'Active streams could not be read.'); }
  }, []);
  useEffect(() => {
    void load(); void apiRequest<typeof policy>('/playback-policy').then(setPolicy).catch(() => setError('History policy unavailable.'));
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);
  async function stop(id: string) {
    setBusy(id); setNotice('');
    try { await apiRequest(`/streams/${id}/stop`, { method: 'POST' }); setNotice('Stream stopped; local process termination and cleanup confirmed.'); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Stop could not be confirmed.'); }
    finally { setBusy(''); }
  }
  async function save(event: React.SyntheticEvent<HTMLFormElement>) {
    event.preventDefault(); setNotice('');
    try { await apiRequest('/playback-policy', { method: 'PATCH', body: jsonBody(policy) }); setNotice('Local history policy saved. Retention runs at least once per minute.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Policy could not be saved.'); }
  }
  return <>
    <PageHeader eyebrow="Owner controls" title="Active streams" description="Playback stays on this server. Stream identities are visible only to Owners; routine logs do not include viewing details." />
    <p className="my-5 text-lg font-medium">{data ? `${data.total} active stream${data.total === 1 ? '' : 's'}` : 'Loading active streams…'}</p>
    {error && <p role="alert" className="my-4 text-sm text-destructive">{error}</p>}
    {notice && <output className="my-4 block text-sm text-primary">{notice}</output>}
    <div className="grid gap-4 xl:grid-cols-2">{data?.items.map(stream => <article key={stream.id} className="rounded-xl border bg-card p-5">
      <div className="flex items-center justify-between gap-4"><h2 className="font-semibold">{stream.username}</h2><span className="text-sm text-primary">{methodLabel[stream.method]}</span></div>
      <dl className="my-4 grid grid-cols-2 gap-3 text-sm">
        <div><dt className="text-muted-foreground">State</dt><dd>{stream.state.replaceAll('_', ' ')}</dd></div>
        <div><dt className="text-muted-foreground">Source → output</dt><dd>{stream.source_height ?? '?'}p → {stream.output_height}p</dd></div>
        <div><dt className="text-muted-foreground">Source / configured output bitrate</dt><dd>{stream.bitrate_kbps || 'Unknown'} kbps</dd></div>
        <div><dt className="text-muted-foreground">Observed output average</dt><dd>{stream.observed_bitrate_kbps == null ? 'Not measured' : `${Math.round(stream.observed_bitrate_kbps)} kbps`}</dd></div>
        <div><dt className="text-muted-foreground">Encoder / observed speed</dt><dd>{stream.encoder} · {stream.speed ? `${stream.speed.toFixed(1)}×` : '—'}</dd></div>
        <div><dt className="text-muted-foreground">Session / first playback</dt><dd>{clockTime(stream.elapsed_seconds)} / {stream.startup_ms == null ? 'Waiting' : `${stream.startup_ms} ms`}</dd></div>
        <div><dt className="text-muted-foreground">Temporary storage</dt><dd>{(stream.temp_bytes / 1048576).toFixed(1)} MiB</dd></div>
      </dl>{stream.error && <p role="alert" className="mb-3 text-destructive">{stream.error}</p>}
      <Button variant="destructive" disabled={busy === stream.id} onClick={() => void stop(stream.id)}>{busy === stream.id ? 'Stopping and confirming…' : 'Stop Stream'}</Button>
    </article>)}</div>
    {data?.total === 0 && <p className="rounded-xl border border-dashed p-8 text-sm text-muted-foreground">No active streams. This page refreshes every five seconds.</p>}
    <form onSubmit={save} className="mt-8 rounded-xl border bg-card p-5">
      <h2 className="text-lg font-semibold">Viewing history policy</h2>
      <p className="my-3 text-sm text-muted-foreground">Stored only in local SQLite. Zero retention days means keep indefinitely. Users can delete their own history; old backups must be expired separately.</p>
      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <label className="space-y-2 text-sm" htmlFor="watched-threshold">Watched threshold (%)<Input id="watched-threshold" type="number" min="50" max="100" value={policy.watched_threshold} onChange={e => setPolicy({ ...policy, watched_threshold: Number(e.target.value) })} /></label>
        <label className="space-y-2 text-sm" htmlFor="minimum-watch">Minimum watch seconds<Input id="minimum-watch" type="number" min="5" max="300" value={policy.minimum_watch_seconds} onChange={e => setPolicy({ ...policy, minimum_watch_seconds: Number(e.target.value) })} /></label>
        <label className="space-y-2 text-sm" htmlFor="history-days">Retention days<Input id="history-days" type="number" min="0" max="3650" value={policy.history_days} onChange={e => setPolicy({ ...policy, history_days: Number(e.target.value) })} /></label>
      </div><Button type="submit">Save history policy</Button>
    </form>
  </>;
}
