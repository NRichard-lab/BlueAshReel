'use client';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PageHeader } from '@/components/page-header';
import { apiRequest, jsonBody } from '@/lib/api';
import { clockTime } from '@/lib/viewer';
import Link from 'next/link';
import { PlaybackHardware } from '@/components/playback-hardware';
import { activeStreamMethod, modeLabel, type PlaybackHealthRecord } from '@/lib/transcoding';

export type { PlaybackHealthRecord } from '@/lib/transcoding';

interface StreamRecord {
  id: string; username: string; method: string; method_label?: string; state: string;
  source_height: number | null; output_height: number | null; bitrate_kbps: number | null;
  observed_bitrate_kbps: number | null;
  speed: number | null; encoder: string; elapsed_seconds: number;
  startup_ms: number | null; temp_bytes: number; error: string | null;
  selected_mode?: string; fallback?: boolean; fallback_reason?: string | null;
}
interface StreamFailure {
  id: string; username: string; error: string; method_label: string;
  selected_mode: string; ended_at: string | null;
}

export function PlaybackHealth() {
  const [data, setData] = useState<PlaybackHealthRecord>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setData(await apiRequest<PlaybackHealthRecord>('/playback-health')); setError(''); }
    catch { setData(undefined); setError('Playback health unavailable.'); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function detect() {
    setBusy(true); setError('');
    try { await apiRequest('/playback-health/detect', { method: 'POST' }); await load(); }
    catch (e) { setData(undefined); setError(e instanceof Error ? e.message : 'Hardware test failed.'); }
    finally { setBusy(false); }
  }
  return <PlaybackHardware data={data} busy={busy} error={error} onRetest={() => void detect()} />;
}

export function ActiveStreams() {
  const [data, setData] = useState<{ items: StreamRecord[]; total: number; health: PlaybackHealthRecord; failures?: StreamFailure[] }>();
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
    <Link href="/settings/playback" className="mt-4 inline-block text-sm text-primary underline underline-offset-4">Playback & Transcoding settings</Link>
    <p className="my-5 text-lg font-medium">{data ? `${data.total} active stream${data.total === 1 ? '' : 's'}` : 'Loading active streams…'}</p>
    {error && <p role="alert" className="my-4 text-sm text-destructive">{error}</p>}
    {notice && <output className="my-4 block text-sm text-primary">{notice}</output>}
    <div className="grid gap-4 xl:grid-cols-2">{data?.items.map(stream => <article key={stream.id} className="rounded-xl border bg-card p-5">
      <div className="flex items-center justify-between gap-4"><h2 className="font-semibold">{stream.username}</h2><span className="text-sm text-primary">{activeStreamMethod(stream)}</span></div>
      <dl className="my-4 grid grid-cols-2 gap-3 text-sm">
        <div><dt className="text-muted-foreground">State</dt><dd>{stream.state.replaceAll('_', ' ')}</dd></div>
        <div><dt className="text-muted-foreground">Mode when started</dt><dd>{modeLabel(stream.selected_mode)}</dd></div>
        <div><dt className="text-muted-foreground">Software fallback</dt><dd>{stream.fallback ? 'Hardware-to-software fallback' : 'Not used'}</dd></div>
        <div><dt className="text-muted-foreground">Source → output</dt><dd>{stream.source_height == null ? 'Unknown' : stream.source_height + 'p'} → {stream.output_height == null ? 'Unchanged' : stream.output_height + 'p'}</dd></div>
        <div><dt className="text-muted-foreground">Source / configured output bitrate</dt><dd>{stream.bitrate_kbps || 'Unknown'} kbps</dd></div>
        <div><dt className="text-muted-foreground">Observed output average</dt><dd>{stream.observed_bitrate_kbps == null ? 'Not measured' : `${Math.round(stream.observed_bitrate_kbps)} kbps`}</dd></div>
        <div><dt className="text-muted-foreground">Encoder / observed speed</dt><dd>{stream.encoder} · {stream.speed ? `${stream.speed.toFixed(1)}×` : '—'}</dd></div>
        <div><dt className="text-muted-foreground">Session / first playback</dt><dd>{clockTime(stream.elapsed_seconds)} / {stream.startup_ms == null ? 'Waiting' : `${stream.startup_ms} ms`}</dd></div>
        <div><dt className="text-muted-foreground">Temporary storage</dt><dd>{(stream.temp_bytes / 1048576).toFixed(1)} MiB</dd></div>
      </dl>{stream.fallback_reason && <p className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">Fallback reason: {stream.fallback_reason}</p>}{stream.error && <p role="alert" className="mb-3 text-destructive">{stream.error}</p>}
      <Button variant="destructive" disabled={busy === stream.id} onClick={() => void stop(stream.id)}>{busy === stream.id ? 'Stopping and confirming…' : 'Stop Stream'}</Button>
    </article>)}</div>
    {data?.total === 0 && <p className="rounded-xl border border-dashed p-8 text-sm text-muted-foreground">No active streams. This page refreshes every five seconds.</p>}
    {data?.failures?.length ? <section className="mt-6 rounded-xl border bg-card p-5" aria-labelledby="recent-stream-failures">
      <h2 id="recent-stream-failures" className="text-lg font-semibold">Recent playback failures</h2>
      <p className="mt-2 text-sm text-muted-foreground">These attempts are not active streams. Details are visible only to the Owner.</p>
      <div className="mt-4 space-y-3">{data.failures.map(failure => <article key={failure.id} className="rounded-lg border border-destructive/30 p-3">
        <h3 className="font-medium">{failure.username}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{failure.method_label} · {modeLabel(failure.selected_mode)}</p>
        <p className="mt-2 text-sm text-destructive">{failure.error}</p>
      </article>)}</div>
    </section> : null}
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
