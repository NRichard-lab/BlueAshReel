import { AppShell } from '@/components/app-shell';
import { DashboardOverview } from '@/components/dashboard-overview';

export default function Home() {
  return (
    <AppShell currentPath="/">
      <DashboardOverview />
    </AppShell>
  );
}
