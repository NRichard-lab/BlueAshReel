import type { Metadata } from 'next';
import { AppShell } from '@/components/app-shell';
import { PlaybackSettings } from '@/components/playback-settings';

export const metadata: Metadata = { title: 'Playback & Transcoding' };

export default function PlaybackSettingsPage() {
  return <AppShell currentPath="/admin/settings/playback"><PlaybackSettings /></AppShell>;
}
