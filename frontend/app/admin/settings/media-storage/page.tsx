import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { MediaStorageManager } from '@/components/media-storage-manager';

export const metadata: Metadata = { title: 'Media Storage' };

export default function MediaStoragePage() {
  return <AppShell currentPath="/admin/settings/media-storage"><MediaStorageManager /></AppShell>;
}
