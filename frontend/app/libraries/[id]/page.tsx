import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { LibraryDetail } from '@/components/library-detail';

export const metadata: Metadata = { title: 'Library details' };

export default async function LibraryDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <AppShell currentPath="/libraries">
      <LibraryDetail libraryId={id} />
    </AppShell>
  );
}
