import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { JobsManager } from '@/components/jobs-manager';

export const metadata: Metadata = { title: 'Background jobs' };

export default function JobsPage() {
  return <AppShell currentPath="/jobs"><JobsManager /></AppShell>;
}
