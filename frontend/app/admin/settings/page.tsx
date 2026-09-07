import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { SettingsManager } from '@/components/settings-manager';

export const metadata: Metadata = { title: 'Settings' };

export default function SettingsPage() {
  return <AppShell currentPath="/settings"><SettingsManager /></AppShell>;
}
