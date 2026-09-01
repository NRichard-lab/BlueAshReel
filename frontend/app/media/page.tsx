import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { MediaResults } from '@/components/media-results';

export const metadata: Metadata = { title: 'Local media' };

export default function MediaPage() {
  return <AppShell currentPath="/media"><MediaResults /></AppShell>;
}
