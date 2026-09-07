import type { Metadata } from 'next';
import { AppShell } from '@/components/app-shell';
import { RemoteAccessSettings } from '@/components/remote-access-settings';

export const metadata: Metadata = { title: 'Remote Access' };

export default function RemoteAccessPage() {
  return <AppShell currentPath="/settings/remote-access"><RemoteAccessSettings /></AppShell>;
}
