import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { PrivacyManager } from '@/components/privacy-manager';

export const metadata: Metadata = { title: 'Privacy' };

export default function PrivacyPage() {
  return <AppShell currentPath="/privacy"><PrivacyManager /></AppShell>;
}
