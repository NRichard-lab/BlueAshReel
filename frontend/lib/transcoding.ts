export const transcodingModes = {
  automatic: {
    label: 'Automatic — Recommended',
    description: 'Direct Play first, remux second, then verified hardware when needed. CPU/software is the safe fallback.',
  },
  hardware_preferred: {
    label: 'Hardware Preferred',
    description: 'Prefer a verified hardware encoder when transcoding is needed. Clearly report any CPU/software fallback.',
  },
  software_only: {
    label: 'Software Only',
    description: 'Use CPU/software for all transcoding. Never switch to a GPU.',
  },
  direct_only: {
    label: 'Direct Play and Remux Only',
    description: 'Do not transcode video or audio. Incompatible media is reported with an explanation.',
  },
  hardware_required: {
    label: 'Hardware Required',
    description: 'Advanced: use only the selected, verified hardware encoder. Fail clearly instead of falling back to the CPU.',
  },
} as const;

export type TranscodingMode = keyof typeof transcodingModes;
export type HardwareEncoder = 'auto' | 'qsv' | 'nvenc' | 'amf';
export const hardwareEncoderLabels: Record<HardwareEncoder, string> = {
  auto: 'Choose a verified encoder automatically',
  qsv: 'Intel Quick Sync',
  nvenc: 'NVIDIA NVENC',
  amf: 'AMD AMF',
};
export const cpuPresets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow'] as const;

export interface TranscodingPolicy {
  mode: TranscodingMode;
  max_processes: number;
  max_height: number;
  max_bitrate_kbps: number;
  cpu_preset: typeof cpuPresets[number];
  preferred_hardware: HardwareEncoder;
  hardware_device: string;
  allow_4k: boolean;
  temp_directory: string;
  max_storage_mb: number;
  inactive_session_seconds: number;
}

export interface TranscodingPolicyRecord extends TranscodingPolicy {
  subtitle_behavior: string;
  changes_apply_to: 'new_streams';
}

export interface HardwareTest {
  encoder: string;
  gpu: string | null;
  test_status: string;
  last_test_at: string | null;
  available_codecs: string[];
  device: string;
  failure: string | null;
}

export interface PlaybackHealthRecord {
  hardware: Record<string, string>;
  configured: string;
  active_encoder: string;
  selected_encoder?: string;
  conversions: number;
  max_conversions: number;
  temp_bytes: number;
  max_temp_bytes: number;
  maintenance_error: string | null;
  auxiliary_processes: number;
  hardware_tests?: HardwareTest[];
  detected_gpus?: string[];
  selected_mode?: TranscodingMode;
  software_fallback?: boolean;
}

export function transcodingPolicyBody(policy: TranscodingPolicy): TranscodingPolicy {
  // Explicit projection keeps derived status/read-only server fields out of PATCH.
  return {
    mode: policy.mode, max_processes: policy.max_processes,
    max_height: policy.max_height, max_bitrate_kbps: policy.max_bitrate_kbps,
    cpu_preset: policy.cpu_preset, preferred_hardware: policy.preferred_hardware,
    hardware_device: policy.hardware_device.trim(), allow_4k: policy.allow_4k,
    temp_directory: policy.temp_directory.trim(), max_storage_mb: policy.max_storage_mb,
    inactive_session_seconds: policy.inactive_session_seconds,
  };
}

export function modeLabel(mode?: string): string {
  return mode && mode in transcodingModes
    ? transcodingModes[mode as TranscodingMode].label
    : 'Not reported';
}

export function hardwareTestTime(value: string | null): string {
  if (!value) return 'Never tested';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Test time unavailable'
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

export function activeStreamMethod(stream: {
  method: string;
  method_label?: string;
  encoder?: string;
  fallback?: boolean;
}): string {
  if (stream.method === 'direct') return 'Direct Play';
  if (stream.method === 'remux') return 'Remux';
  if (stream.method_label) return stream.method_label;
  // Infer only from the actual stream encoder, never the configured policy/GPU list.
  if (stream.method === 'transcode') {
    if (stream.encoder && /(?:qsv|nvenc|amf)/i.test(stream.encoder)) return 'Hardware Transcode';
    if (stream.encoder && /(?:libx264|libx265|aac|software)/i.test(stream.encoder)) return 'Software Transcode';
    return 'Transcode — encoder not reported';
  }
  return 'Playback method not reported';
}
