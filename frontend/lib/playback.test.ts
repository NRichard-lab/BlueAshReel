import { describe, expect, it } from 'vitest';
import { boundedPosition, browserCapabilities, disableCaptions, HardwareRecoveryGate } from './playback';
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

describe('bounded hardware recovery', () => {
  it('coalesces concurrent browser/HLS/poll failures and permits one authorized recovery', () => {
    const gate = new HardwareRecoveryGate();
    expect(gate.request('gpu-session')).toBe('check');
    expect(gate.request('gpu-session')).toBe('pending');
    expect(gate.resolve('gpu-session', true)).toBe(true);
    expect(gate.request('cpu-recovery-session')).toBe('exhausted');
    expect(gate.resolve('gpu-session', true)).toBe(false);
  });

  it('never recovers when the backend says the failure is ineligible', () => {
    const gate = new HardwareRecoveryGate();
    expect(gate.request('source-missing')).toBe('check');
    expect(gate.resolve('source-missing', false)).toBe(false);
    expect(gate.request('another-session')).toBe('check');
  });

  it('ignores stale responses instead of consuming a current recovery', () => {
    const gate = new HardwareRecoveryGate();
    gate.request('current');
    expect(gate.resolve('stale', true)).toBe(false);
    expect(gate.request('current')).toBe('pending');
    expect(gate.resolve('current', true)).toBe(true);
  });
});
