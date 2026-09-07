'use client';
// The player is a focusable composite control with documented keyboard shortcuts.
/* oxlint-disable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import Hls from 'hls.js';
import { Play, Pause, Volume2, VolumeX, Maximize, RotateCcw, SkipForward, ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NativeSelect, NativeSelectOption as Option } from '@/components/ui/native-select';
import { CatalogState } from '@/components/viewer-catalog';
import { apiRequest, jsonBody } from '@/lib/api';
import { MediaDetailRecord, MediaCardRecord, ProfileRecord, useViewerData, clockTime } from '@/lib/viewer';
import { boundedPosition, browserCapabilities, disableCaptions, HardwareRecoveryGate, methodLabel, PlaybackRecord } from '@/lib/playback';

type Reason = 'periodic' | 'playing' | 'pause' | 'seek' | 'exit' | 'ended' | 'restart';

export function MediaPlayer({ mediaId }: { mediaId: string }) {
  const result = useViewerData<MediaDetailRecord>(`/browse/media/${mediaId}`);
  const next = useViewerData<{ item: MediaCardRecord | null }>(`/browse/media/${mediaId}/next`);
  const profile = useViewerData<ProfileRecord>('/profile');
  const videoRef = useRef<HTMLVideoElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const sessionRef = useRef<PlaybackRecord | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const sequence = useRef(0);
  const alive = useRef(true);
  const starting = useRef(false);
  const autoStarted = useRef(false);
  const recoveryGate = useRef(new HardwareRecoveryGate());
  const startRef = useRef<((at?: number, restart?: boolean, recoveryFrom?: string) => Promise<void>) | null>(null);
  const scrub = useRef<number | null>(null);
  const [draftPosition, setDraftPosition] = useState<number | null>(null);
  const desired = useRef(0);
  const [session, setSession] = useState<PlaybackRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [status, setStatus] = useState('Choose Start playback to open an authenticated local stream.');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [audio, setAudio] = useState('');
  const [subtitle, setSubtitle] = useState('');
  const [quality, setQuality] = useState('original');
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [watched, setWatched] = useState<boolean | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);
  const item = result.data;
  const file = item?.files.find(f => f.id === item.file_id);
  const duration = session?.duration_seconds ?? file?.duration_seconds ?? 0;

  const checkpoint = useCallback(async (reason: Reason, end = false) => {
    const current = sessionRef.current;
    const video = videoRef.current;
    if (!current || !video) return;
    const body = jsonBody({
      position_seconds: Math.min(current.duration_seconds, (current.video_offset ?? 0) + video.currentTime),
      playing: !end && !video.paused && !video.ended,
      reason, sequence: ++sequence.current,
    });
    if (end) sessionRef.current = null;
    try {
      await apiRequest(`/playback/${current.id}/${end ? 'end' : 'progress'}`, { method: 'POST', body, keepalive: end });
      if (alive.current && !end) setNotice('Progress saved locally');
    } catch (e) {
      if (end) {
        // A dropped final checkpoint must not leave a conversion running.
        try { await apiRequest(`/playback/${current.id}/stop`, { method: 'POST', keepalive: true }); }
        catch { if (alive.current) setNotice('Stream stop could not be confirmed. The Owner can stop it in Active Streams.'); }
      } else if (alive.current) setNotice(e instanceof Error ? e.message : 'Progress could not be saved.');
    }
  }, []);

  const detach = useCallback(() => {
    const video = videoRef.current;
    if (video) disableCaptions(video);
    hlsRef.current?.destroy();
    hlsRef.current = null;
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
  }, []);

  const fail = useCallback((message: string, expectedId?: string) => {
    if (!alive.current || (expectedId && sessionRef.current?.id !== expectedId)) return;
    const current = sessionRef.current;
    const finishFailure = () => {
      if (!alive.current || (current && sessionRef.current?.id !== current.id)) return;
      sessionRef.current = null;
      detach();
      setPlaying(false); setSession(null); setBusy(false);
      setError(message); setStatus('Playback stopped');
      if (current) void apiRequest('/playback/' + current.id + '/stop', { method: 'POST' }).catch(() => undefined);
    };
    if (!current) { finishFailure(); return; }
    const gate = recoveryGate.current;
    const action = gate.request(current.id);
    if (action === 'pending') return;
    if (action === 'exhausted') { finishFailure(); return; }
    const resumeAt = boundedPosition((current.video_offset ?? 0) + (videoRef.current?.currentTime ?? 0), current.duration_seconds);
    setBusy(true); setStatus('Checking local stream recovery…');
    void (async () => {
      try {
        const permission = await apiRequest<{ recoverable: boolean; reason: string | null }>('/playback/' + current.id + '/recovery');
        if (!alive.current || sessionRef.current?.id !== current.id) { gate.resolve(current.id, false); return; }
        if (gate.resolve(current.id, permission.recoverable) && startRef.current) {
          await startRef.current(resumeAt, false, current.id);
        } else finishFailure();
      } catch { gate.resolve(current.id, false); finishFailure(); }
      finally { if (alive.current && sessionRef.current?.id === current.id) setBusy(false); }
    })();
  }, [detach]);

  const start = useCallback(async (at?: number, restart = false, recoveryFrom?: string) => {
    const video = videoRef.current;
    if (!file || !video || starting.current) return;
    starting.current = true;
    setBusy(true); setError(''); setNotice(''); setCountdown(null);
    setStatus(recoveryFrom ? 'Hardware failed; reconnecting with local CPU/software…' : 'Preparing a local stream…');
    if (recoveryFrom) sessionRef.current = null;
    else await checkpoint('exit', true);
    detach();
    try {
      const current = await apiRequest<PlaybackRecord>('/playback/sessions', {
        method: 'POST', body: jsonBody({ file_id: file.id, capabilities: browserCapabilities(video),
          audio_index: audio === '' ? null : Number(audio), subtitle_index: subtitle === '' ? null : Number(subtitle),
          quality, restart: restart || new URLSearchParams(window.location.search).get('restart') === '1',
          position_seconds: at, recovery_from: recoveryFrom,
        }),
      });
      if (!alive.current) { await apiRequest(`/playback/${current.id}/stop`, { method: 'POST' }); return; }
      sessionRef.current = current; sequence.current = 0;
      setSession(current); setPosition(current.position_seconds);
      if (current.fallback) setNotice(current.fallback_reason || 'Hardware-to-software fallback is active.');
      desired.current = current.position_seconds - (current.video_offset ?? 0);
      setStatus(`${(current.method_label ?? methodLabel[current.decision.method])} · waiting for decoded video`);
      if (current.decision.method === 'direct') {
        video.src = current.url;
      } else if (Hls.isSupported()) {
        const hls = new Hls({ enableWorker: false, startPosition: Math.max(0, desired.current),
          maxBufferLength: 30, backBufferLength: 30, debug: false });
        hlsRef.current = hls;
        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) fail('Local streaming failed. Retry playback; if it repeats, ask the Owner to check Active Streams.', current.id);
        });
        hls.loadSource(current.url); hls.attachMedia(video);
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = current.url;
      } else throw new Error('This browser does not support the local streaming output.');
      void video.play().catch(() => { if (alive.current && sessionRef.current?.id === current.id) setStatus('Stream ready. Press Play to begin.'); });
    } catch (e) { fail(e instanceof Error ? e.message : 'Playback could not start.'); }
    finally { starting.current = false; if (alive.current) setBusy(false); }
  }, [file, audio, subtitle, quality, checkpoint, detach, fail]);

  useEffect(() => { startRef.current = start; }, [start]);
  useEffect(() => { recoveryGate.current = new HardwareRecoveryGate(); }, [mediaId]);

  useEffect(() => {
    if (file && !autoStarted.current && new URLSearchParams(window.location.search).get('autoplay') === '1') {
      autoStarted.current = true;
      void start();
    }
  }, [file, start]);

  useEffect(() => {
    alive.current = true;
    const periodic = window.setInterval(() => { void checkpoint('periodic'); }, 15000);
    const poll = window.setInterval(() => {
      const current = sessionRef.current;
      if (current) void apiRequest(`/playback/${current.id}`).catch(e => fail(e instanceof Error ? e.message : 'This stream is no longer available.', current.id));
    }, 5000);
    const exit = () => { void checkpoint('exit', true); detach(); };
    const visibility = () => { if (document.visibilityState === 'hidden') void checkpoint('periodic'); };
    window.addEventListener('pagehide', exit);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      alive.current = false; window.clearInterval(periodic); window.clearInterval(poll);
      window.removeEventListener('pagehide', exit); document.removeEventListener('visibilitychange', visibility);
      exit();
    };
  }, [checkpoint, detach, fail]);

  useEffect(() => {
    if (countdown === null || !next.data?.item) return;
    if (countdown <= 0) { window.location.assign(`/player/${next.data.item.id}?autoplay=1`); return; }
    const timer = window.setTimeout(() => setCountdown(countdown - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown, next.data]);

  function togglePlay() {
    const video = videoRef.current;
    if (!video || !sessionRef.current) return;
    if (video.paused) void video.play().catch(() => setError('The browser could not decode this stream. Try a lower quality or another supported browser.'));
    else video.pause();
  }
  function seek(value: number) {
    const video = videoRef.current;
    const current = sessionRef.current;
    if (!video || !current) return;
    const target = boundedPosition(value, duration);
    if (current.decision.method !== 'direct') {
      // Regenerate from the requested time, including seeks beyond produced segments.
      void start(target);
    } else { video.currentTime = target; setPosition(target); void checkpoint('seek'); }
  }
  function changeVolume(value: number) {
    if (videoRef.current) videoRef.current.volume = value;
    setVolume(value);
  }
  function finishScrub() {
    const value = scrub.current;
    scrub.current = null; setDraftPosition(null);
    if (value !== null) seek(value);
  }
  function toggleMute() {
    if (videoRef.current) { videoRef.current.muted = !videoRef.current.muted; setMuted(videoRef.current.muted); }
  }
  async function fullscreen() {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await frameRef.current?.requestFullscreen(); }
    catch { setNotice('Fullscreen is not available in this browser window.'); }
  }
  async function mark() {
    const value = !(watched ?? item?.watched ?? false);
    try { await apiRequest(`/browse/media/${mediaId}/watched`, { method: 'PUT', body: jsonBody({ watched: value }) });
      setWatched(value); setNotice(value ? 'Marked watched' : 'Marked unwatched');
    } catch { setNotice('Watch status could not be saved.'); }
  }
  function keyboard(event: React.KeyboardEvent) {
    if (event.target instanceof HTMLElement && /INPUT|SELECT|BUTTON|A/.test(event.target.tagName)) return;
    const key = event.key.toLowerCase();
    if ([' ', 'k', 'arrowleft', 'arrowright', 'm', 'f'].includes(key)) event.preventDefault();
    if (key === ' ' || key === 'k') togglePlay();
    if (key === 'arrowleft') seek(position - 10);
    if (key === 'arrowright') seek(position + 10);
    if (key === 'm') toggleMute();
    if (key === 'f') void fullscreen();
  }

  return <>
    <CatalogState {...result} empty={false} />
    {item && <>
      <div className="mb-5 flex items-center gap-3">
        <Link href={`/watch/${mediaId}`} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">
          <ArrowLeft className="size-4" aria-hidden="true" />Back to details
        </Link>
        <h1 className="min-w-0 truncate text-xl font-semibold">{item.title}</h1>
      </div>
      <div ref={frameRef} className="mx-auto w-full max-w-5xl overflow-hidden rounded-xl border border-border bg-black text-white shadow-xl [&:fullscreen]:flex [&:fullscreen]:max-w-none [&:fullscreen]:flex-col [&:fullscreen]:justify-center [&:fullscreen]:rounded-none [&:fullscreen]:border-0" tabIndex={0}
        role="application" aria-label="Video player. Space or K to play, arrows to seek, M to mute, F for fullscreen."
        onKeyDown={keyboard}>
        {/* oxlint-disable-next-line jsx-a11y/media-has-caption -- The caption track below is conditional on an available selected local track. */}
        <video ref={videoRef} className="mx-auto aspect-video max-h-[78vh] w-full bg-black in-[:fullscreen]:max-h-none in-[:fullscreen]:flex-1" playsInline preload="metadata"
          aria-label={`${item.title} video`} onClick={togglePlay}
          onLoadedMetadata={() => { const v = videoRef.current; if (v && desired.current > 0) { v.currentTime = desired.current; desired.current = 0; } }}
          onPlaying={() => {
            const current = sessionRef.current; const video = videoRef.current;
            if (!current || !video) return;
            setPlaying(true); setError(''); setStatus('Playing · checking video frames'); void checkpoint('playing');
            video.requestVideoFrameCallback?.(() => {
              if (alive.current && sessionRef.current?.id === current.id && video.videoWidth > 0 && video.videoHeight > 0)
                setStatus('Playing · video decoded locally');
            });
          }}
          onPause={() => { setPlaying(false); if (sessionRef.current) { setStatus('Paused'); void checkpoint('pause'); } }}
          onWaiting={() => { if (sessionRef.current) setStatus('Buffering local video…'); }}
          onTimeUpdate={() => { const v = videoRef.current; if (v && sessionRef.current) setPosition(Math.min(sessionRef.current.duration_seconds, (sessionRef.current.video_offset ?? 0) + v.currentTime)); }}
          onError={() => { if (sessionRef.current && !starting.current) fail('The browser could not decode this source. Try a lower quality or ask the Owner to check the file.'); }}
          onEnded={() => {
            const current = sessionRef.current;
            if (current && (current.video_offset ?? 0) + (videoRef.current?.currentTime ?? 0) < current.duration_seconds - 2) {
              fail('The local stream ended before the media finished. Reconnect to resume; ask the Owner to check the source if it repeats.', current.id); return;
            }
            void checkpoint('ended', true).then(() => { if (alive.current) { setWatched(null); result.reload(); } });
            setSession(null); setPlaying(false); setStatus('Playback finished');
            if (profile.data?.auto_next && next.data?.item) setCountdown(profile.data.next_countdown); }}>
            {session?.subtitle_index != null && file?.subtitles.find(s => s.index === session.subtitle_index)?.text_supported &&
              <track key={`${session.id}-${session.subtitle_index}`} kind="captions" label="Selected local subtitles" srcLang={file.subtitles.find(s => s.index === session.subtitle_index)?.language || 'und'} default
                src={`/api/v1/playback/${session.id}/subtitles/${session.subtitle_index}.vtt`}
                onLoad={event => { event.currentTarget.track.mode = sessionRef.current?.id === session.id ? 'showing' : 'disabled'; }}
                onError={() => { if (sessionRef.current?.id === session.id) setNotice('This subtitle track could not be converted locally. Choose another track or turn subtitles off.'); }} />}
        </video>
        <div className="space-y-4 p-4">
          <label className="block"><span className="sr-only">Seek position</span>
            <input aria-label="Seek position" type="range" className="w-full accent-blue-400" min="0" max={duration || 1} step="1"
              value={Math.min(draftPosition ?? position, duration || 1)} disabled={!session || busy}
              onChange={e => { scrub.current = Number(e.target.value); setDraftPosition(scrub.current); }}
              onPointerUp={finishScrub} onKeyUp={finishScrub} onBlur={finishScrub}
              aria-valuetext={`${clockTime(position)} of ${clockTime(duration)}`} /></label>
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <Button aria-label={playing ? 'Pause' : 'Play'} disabled={!session || busy} variant="secondary" size="icon" onClick={togglePlay}>{playing ? <Pause /> : <Play />}</Button>
            <Button aria-label="Back 10 seconds" variant="secondary" disabled={!session || busy} onClick={() => seek(position - 10)}>−10</Button>
            <Button aria-label="Forward 10 seconds" variant="secondary" disabled={!session || busy} onClick={() => seek(position + 10)}>+10</Button>
            <output aria-label="Playback time" aria-live="off" className="mr-auto text-sm tabular-nums">{clockTime(draftPosition ?? position)} / {clockTime(duration)}</output>
            <Button aria-label={muted ? 'Unmute' : 'Mute'} variant="secondary" size="icon" onClick={toggleMute}>{muted ? <VolumeX /> : <Volume2 />}</Button>
            <input aria-label="Volume" type="range" className="w-20 accent-blue-400" min="0" max="1" step="0.05" value={volume} onChange={e => changeVolume(Number(e.target.value))} />
            <Button aria-label="Fullscreen" variant="secondary" size="icon" onClick={() => void fullscreen()}><Maximize /></Button>
          </div>
        </div>
      </div>
      <div className="mx-auto mt-4 max-w-5xl space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${error ? 'bg-destructive' : playing ? 'bg-[var(--success)]' : 'bg-muted-foreground/50'}`} />
        <output>{status}</output>
      </div>
      {error && <p role="alert" className="rounded-lg border border-destructive/60 bg-destructive/10 p-4 text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap gap-3">
        <Button disabled={!file || busy} onClick={() => void start()}><Play />{busy ? 'Preparing…' : session ? 'Reconnect / resume' : item.position_seconds >= 5 && !item.watched ? `Resume at ${clockTime(item.position_seconds)}` : 'Start playback'}</Button>
        <Button variant="outline" disabled={!file || busy} onClick={() => void start(0, true)}><RotateCcw />Restart from beginning</Button>
        <Button variant="outline" onClick={() => void mark()}>{(watched ?? item.watched) ? 'Mark Unwatched' : 'Mark Watched'}</Button>
        {next.data?.item && <Link href={`/player/${next.data.item.id}`} className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm"><SkipForward className="size-4" />Next episode</Link>}
      </div>
      {countdown != null && <div className="flex items-center gap-4 rounded-xl border border-border p-4"><output>Next episode in {countdown}s</output><Button variant="outline" onClick={() => setCountdown(null)}>Cancel autoplay</Button></div>}
      <details className="rounded-xl border border-border bg-card p-4 text-sm">
        <summary className="cursor-pointer text-muted-foreground">Stream details</summary>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-border px-3 py-1 text-xs">{session ? (session.method_label ?? methodLabel[session.decision.method]) : 'Method determined when playback starts'}</span>
          {session?.fallback ? <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-xs">Hardware-to-software fallback: {session.fallback_reason || 'Using local CPU/software.'}</span> : null}
        </div>
      </details>
      <div className="grid gap-4 rounded-xl border border-border bg-card p-5 sm:grid-cols-3">
        <label htmlFor="audio-track" className="space-y-2 text-sm"><span>Audio track</span><NativeSelect id="audio-track" aria-label="Audio track" value={audio} onChange={e => setAudio(e.target.value)}>
          <Option value="">Default track</Option>{file?.audio.map(a => <Option key={a.index} value={a.index}>{a.title || `Track ${a.index}`} · {a.language || 'und'} · {a.codec}</Option>)}
        </NativeSelect></label>
        <label htmlFor="subtitle-track" className="space-y-2 text-sm"><span>Subtitles</span><NativeSelect id="subtitle-track" aria-label="Subtitles" value={subtitle} onChange={e => setSubtitle(e.target.value)}>
          <Option value="">Off</Option>{file?.subtitles.map(s => <Option key={s.index} value={s.index}>{s.title || `Track ${s.index}`} · {s.codec}{!s.text_supported ? ' · unsupported' : ''}</Option>)}
        </NativeSelect></label>
        <label htmlFor="quality-limit" className="space-y-2 text-sm"><span>Quality limit</span><NativeSelect id="quality-limit" aria-label="Quality limit" value={quality} onChange={e => setQuality(e.target.value)}>
          <Option value="original">Original (server limits apply)</Option><Option value="1080p">1080p · up to 6 Mbps</Option><Option value="720p">720p · up to 3 Mbps</Option><Option value="480p">480p · up to 1.2 Mbps</Option>
        </NativeSelect></label>
        <div className="sm:col-span-3 flex flex-wrap items-center gap-4"><Button variant="secondary" disabled={!file || busy} onClick={() => void start(session ? position : undefined)}>Apply playback options</Button>
          <p className="text-xs text-muted-foreground">Changes create a local representation at the current position. No source file is modified.</p></div>
      </div>
      {notice && <output className="block text-sm text-muted-foreground">{notice}</output>}
      <p className="text-xs text-muted-foreground">Focus the player: Space / K play or pause · ← / → seek 10 seconds · M mute · F fullscreen. Progress stays on this server.</p>
      </div>
    </>}
  </>;
}
