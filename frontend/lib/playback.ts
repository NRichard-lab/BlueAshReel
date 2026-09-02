export function browserCapabilities(video: HTMLVideoElement) {
  const supports = (type: string) => video.canPlayType(type) !== '';
  return {
    h264: supports('video/mp4; codecs="avc1.4d401f"'),
    aac: supports('audio/mp4; codecs="mp4a.40.2"'),
    webm_vp9: supports('video/webm; codecs="vp9"'),
    opus: supports('audio/webm; codecs="opus"'),
    hls: supports('application/vnd.apple.mpegurl') ||
      (typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported('video/mp4; codecs="avc1.4d401f,mp4a.40.2"')),
    max_height: 2160,
    max_h264_level: 51,
  };
}

export function boundedPosition(value: number, duration: number) {
  return Number.isFinite(value) ? Math.max(0, Math.min(value, Math.max(0, duration - 0.1))) : 0;
}

export function disableCaptions(video: Pick<HTMLVideoElement, 'textTracks'>) {
  // Chromium can retain rendered cues while an HLS representation is replaced.
  // Disable the old tracks before detaching the source or removing track nodes.
  for (const track of Array.from(video.textTracks)) track.mode = 'disabled';
}

export interface PlaybackRecord {
  id: string;
  decision: { method: 'direct' | 'remux' | 'transcode'; reason: string; mime: string };
  position_seconds: number;
  duration_seconds: number;
  video_offset?: number;
  url: string;
  subtitle_index: number | null;
  audio_index: number | null;
  quality: string;
  method_label?: string;
  selected_mode?: string;
  fallback?: boolean;
  fallback_reason?: string | null;
}

export const methodLabel = { direct: 'Direct Play', remux: 'Local Remux', transcode: 'Local Transcode' };

/** A single server-authorized hardware recovery per media playback, never a retry loop. */
export class HardwareRecoveryGate {
  private pending: string | null = null;
  private attempted = false;

  request(sessionId: string): 'check' | 'pending' | 'exhausted' {
    if (this.pending) return 'pending';
    if (this.attempted) return 'exhausted';
    this.pending = sessionId;
    return 'check';
  }

  resolve(sessionId: string, recoverable: boolean): boolean {
    if (this.pending !== sessionId) return false;
    this.pending = null;
    if (!recoverable || this.attempted) return false;
    this.attempted = true;
    return true;
  }
}
