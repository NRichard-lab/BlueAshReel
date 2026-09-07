import type { Metadata } from 'next';
import { ViewerShell } from '@/components/viewer-shell';
import { ViewerSettings } from '@/components/viewer/settings/viewer-settings';

export const metadata: Metadata = { title: 'Settings' };

export default function SettingsPage() {
  return (
    <ViewerShell currentPath="/settings">
      <ViewerSettings />
    </ViewerShell>
  );
}
