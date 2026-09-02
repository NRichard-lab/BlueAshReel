import { describe, expect, it } from 'vitest';
import { activeStreamMethod, hardwareTestTime, modeLabel, transcodingModes, transcodingPolicyBody, type TranscodingPolicyRecord } from './transcoding';

describe('Owner transcoding policy', () => {
  it('offers all supported modes and labels the conservative default', () => {
    expect(Object.keys(transcodingModes)).toEqual(['automatic', 'hardware_preferred', 'software_only', 'direct_only', 'hardware_required']);
    expect(modeLabel('automatic')).toBe('Automatic — Recommended');
    expect(modeLabel('hardware_required')).toBe('Hardware Required');
    expect(modeLabel('future_mode')).toBe('Not reported');
  });

  it('sends only editable policy fields and preserves Software Only', () => {
    const record: TranscodingPolicyRecord = {
      mode: 'software_only', max_processes: 2, max_height: 1080, max_bitrate_kbps: 8000,
      cpu_preset: 'veryfast', preferred_hardware: 'nvenc', hardware_device: ' auto ',
      allow_4k: false, temp_directory: ' C:\\ProgramData\\BlueReel\\temp ',
      max_storage_mb: 4096, inactive_session_seconds: 90,
      subtitle_behavior: 'Local text subtitles', changes_apply_to: 'new_streams',
    };
    const body = transcodingPolicyBody(record);
    expect(body.mode).toBe('software_only');
    expect(body.hardware_device).toBe('auto');
    expect(body.temp_directory).toBe('C:\\ProgramData\\BlueReel\\temp');
    expect(body).not.toHaveProperty('changes_apply_to');
    expect(body).not.toHaveProperty('subtitle_behavior');
    expect(body).not.toHaveProperty('hardware_tests');
  });

  it('does not fabricate test dates', () => {
    expect(hardwareTestTime(null)).toBe('Never tested');
    expect(hardwareTestTime('invalid')).toBe('Test time unavailable');
    expect(hardwareTestTime('2026-09-01T12:00:00Z')).not.toMatch(/Never|unavailable/);
  });
});

describe('truthful active-stream method labels', () => {
  it.each([
    [{ method: 'direct', encoder: 'h264_nvenc' }, 'Direct Play'],
    [{ method: 'remux', encoder: 'copy' }, 'Remux'],
    [{ method: 'transcode', encoder: 'h264_nvenc' }, 'Hardware Transcode'],
    [{ method: 'transcode', encoder: 'h264_qsv' }, 'Hardware Transcode'],
    [{ method: 'transcode', encoder: 'h264_amf' }, 'Hardware Transcode'],
    [{ method: 'transcode', encoder: 'libx264' }, 'Software Transcode'],
    [{ method: 'transcode', encoder: 'aac (audio only)' }, 'Software Transcode'],
    [{ method: 'transcode', encoder: 'libx264', fallback: true }, 'Hardware-to-software fallback'],
    [{ method: 'transcode', encoder: '' }, 'Transcode — encoder not reported'],
    [{ method: 'unexpected' }, 'Playback method not reported'],
  ])('reports actual processing for %j', (stream, expected) => {
    expect(activeStreamMethod(stream)).toBe(expected);
  });

  it('prefers the server method label for reported transcodes', () => {
    expect(activeStreamMethod({ method: 'transcode', method_label: 'Software Transcode (audio only)' })).toBe('Software Transcode (audio only)');
  });
});
