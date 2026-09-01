import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { SystemHealth } from '@/components/system-health';

export const metadata: Metadata = { title: 'System health' };

export default function HealthPage() {
  return <AppShell currentPath="/health"><SystemHealth /></AppShell>;
}
