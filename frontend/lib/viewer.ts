'use client';
import { useCallback, useEffect, useState } from 'react';
import { apiRequest, ApiError } from '@/lib/api';

export interface MediaCardRecord {
  id: string;
  library_id: string;
  kind: string;
  title: string;
  year: number | null;
  available: boolean;
  file_id: string | null;
  duration_seconds: number | null;
  height: number | null;
  poster_url: string | null;
  position_seconds: number;
  watched: boolean;
  completion: number;
  season_number: number | null;
  episode_number: number | null;
  show_id: string | null;
  show_title?: string | null;
}
export interface HomeLibrary {
  id: string;
  name: string;
  library_type: 'movies' | 'tv' | 'other';
  enabled: boolean;
}
export interface HomeResponse {
  continue: MediaCardRecord[];
  recent_movies: MediaCardRecord[];
  recent_episodes: MediaCardRecord[];
  libraries: HomeLibrary[];
}
export interface MediaFileRecord {
  id: string;
  available: boolean;
  container: string | null;
  duration_seconds: number | null;
  bitrate: number | null;
  video: {
    index: number;
    codec: string;
    width: number;
    height: number;
    profile: string | null;
    level: number | null;
    pixel_format: string | null;
    bit_depth: number | null;
  }[];
  audio: {
    index: number;
    codec: string;
    channels: number;
    language: string | null;
    title: string | null;
  }[];
  subtitles: {
    index: number;
    codec: string;
    language: string | null;
    title: string | null;
    text_supported: boolean;
  }[];
  size_bytes?: number;
  analyzed_at?: string;
  analysis_error?: string;
}
export interface MediaDetailRecord extends MediaCardRecord {
  files: MediaFileRecord[];
  background_url: string | null;
}
export interface ProfileRecord {
  id: string;
  username: string;
  roles: string[];
  auto_next: boolean;
  next_countdown: number;
}

export function useViewerData<T>(path: string | null) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    if (!path) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setData(undefined);
    setLoading(true);
    setError('');
    apiRequest<T>(path, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch((e) => {
        if (controller.signal.aborted) return;
        if (e instanceof ApiError && e.status === 401)
          window.location.assign('/login');
        else
          setError(
            e instanceof Error
              ? e.message
              : 'The server could not complete this request.',
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  return { data, error, loading, reload };
}
export function clockTime(seconds: number | null | undefined) {
  const value = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(value / 3600);
  const minutes = String(Math.floor(value / 60) % 60);
  return `${hours ? `${hours}:${minutes.padStart(2, '0')}` : minutes}:${String(value % 60).padStart(2, '0')}`;
}
