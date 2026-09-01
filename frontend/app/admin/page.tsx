import { AppShell } from '@/components/app-shell';
import { DashboardOverview } from '@/components/dashboard-overview';

export default function Administration() {
  return (
    <AppShell currentPath="/admin">
      <DashboardOverview />
    </AppShell>
  );
}
