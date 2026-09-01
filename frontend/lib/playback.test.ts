import { describe, expect, it } from 'vitest';
import { boundedPosition, browserCapabilities, disableCaptions } from './playback';
describe('local playback helpers', () => {
  it('disables old caption tracks before replacing a representation', () => {
    const tracks = [{ mode: 'showing' }, { mode: 'hidden' }];
    disableCaptions({ textTracks: tracks } as unknown as HTMLVideoElement);
    expect(tracks.map(track => track.mode)).toEqual(['disabled', 'disabled']);
  });
  it('bounds seeking without allowing nonfinite positions', () => {
    expect(boundedPosition(-20, 100)).toBe(0);
    expect(boundedPosition(120, 100)).toBe(99.9);
    expect(boundedPosition(NaN, 100)).toBe(0);
  });
  it('reports only minimal capability hints, no device identity', () => {
    const capabilities = browserCapabilities({ canPlayType: () => '' } as unknown as HTMLVideoElement);
    expect(capabilities.h264).toBe(false);
    expect(capabilities.aac).toBe(false);
    expect(JSON.stringify(capabilities)).not.toMatch(/userAgent|screen|fingerprint|deviceId/);
  });
});
