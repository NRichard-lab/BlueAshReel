import { AppShell } from '@/components/app-shell';
import { ActiveStreams } from '@/components/active-streams';
export const metadata = { title: 'Active streams' };
export default function StreamsPage() {
  return <AppShell currentPath="/streams"><ActiveStreams /></AppShell>;
}
