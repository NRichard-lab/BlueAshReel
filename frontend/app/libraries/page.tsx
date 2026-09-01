import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { LibrariesManager } from '@/components/libraries-manager';

export const metadata: Metadata = { title: 'Libraries' };

export default function LibrariesPage() {
  return (
    <AppShell currentPath="/libraries">
      <LibrariesManager />
    </AppShell>
  );
}
