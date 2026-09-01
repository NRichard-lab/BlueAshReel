import { productConfig } from '@/lib/product-config';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function cookieValue(name: string): string | undefined {
  if (typeof document === 'undefined') return undefined;
  const prefix = `${encodeURIComponent(name)}=`;
  return document.cookie
    .split('; ')
    .find((entry) => entry.startsWith(prefix))
    ?.slice(prefix.length);
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');

  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = cookieValue('csrf_token');
    if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf));
  }

  const response = await fetch(`${productConfig.api_prefix}${path}`, {
    ...init,
    headers,
    credentials: 'same-origin',
    cache: 'no-store',
  });

  const contentType = response.headers.get('content-type') ?? '';
  const payload = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === 'object' && payload && 'detail' in payload
        ? String(payload.detail)
        : typeof payload === 'object' && payload && 'error' in payload &&
            typeof payload.error === 'object' && payload.error && 'message' in payload.error
          ? String(payload.error.message)
        : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, payload);
  }

  return payload as T;
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value);
}

export interface PublicConfig {
  name: string;
  subtitle: string;
  version: string;
  setup_required?: boolean;
  outbound_integrations_enabled?: boolean;
}

export interface CurrentUser {
  id: string;
  username: string;
  roles: string[];
}

export interface LibraryPath {
  id: string;
  path: string;
  enabled: boolean;
}

export interface LibraryRecord {
  id: string;
  name: string;
  library_type: 'movies' | 'tv' | 'other';
  enabled: boolean;
  paths: LibraryPath[];
  media_count: number;
  available_file_count: number;
  error_count: number;
  last_successful_scan_at?: string | null;
  active_job_id?: string | null;
}

export interface JobRecord {
  id: string;
  job_type: string;
  status: string;
  progress_current: number;
  progress_total?: number | null;
  attempts: number;
  max_attempts: number;
  cancel_requested: boolean;
  error_summary?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  scan?: {
    library_id: string;
    mode: 'full' | 'changed';
    discovered_files: number;
    processed_files: number;
    unchanged_files: number;
    missing_files: number;
    error_count: number;
    duration_ms?: number | null;
  } | null;
}

export interface MediaRecord {
  id: string;
  library_id: string;
  title: string;
  kind: string;
  year?: number | null;
  match_confidence: number;
  available: boolean;
  files: Array<{
    id: string;
    available: boolean;
    container?: string | null;
    duration_seconds?: number | null;
    analysis_error?: string | null;
    video_streams: number;
    audio_streams: number;
    subtitle_streams: number;
    video: Array<{
      index: number;
      codec?: string | null;
      width?: number | null;
      height?: number | null;
      bitrate?: number | null;
      frame_rate?: number | null;
      language?: string | null;
    }>;
    audio: Array<{
      index: number;
      codec?: string | null;
      channels?: number | null;
      channel_layout?: string | null;
      bitrate?: number | null;
      language?: string | null;
      title?: string | null;
    }>;
    subtitles: Array<{
      index: number;
      codec?: string | null;
      language?: string | null;
      title?: string | null;
      forced?: boolean;
      hearing_impaired?: boolean;
    }>;
  }>;
}

export interface PageResult<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages?: number;
}

export interface HealthResult {
  status: string;
  checks?: Record<string, string | boolean>;
  database?: string | boolean;
  ffprobe?: string | boolean;
  ffmpeg?: string | boolean;
  worker?: string | boolean;
  version?: string;
}

export interface DashboardResult {
  server_status: string;
  database_status: string;
  ffprobe_available: boolean;
  ffmpeg_available: boolean;
  library_count: number;
  media_count: number;
  active_job_count: number;
  last_scan_at?: string | null;
  storage: Record<string, boolean>;
  storage_details: Record<string, {
    configured_path: string;
    ready: boolean;
    free_bytes?: number | null;
  }>;
}
