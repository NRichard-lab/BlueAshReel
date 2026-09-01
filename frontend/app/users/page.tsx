import { AppShell } from '@/components/app-shell';
import { HouseholdUsers } from '@/components/household-users';
export default function Users() {
  return (
    <AppShell currentPath="/users">
      <HouseholdUsers />
    </AppShell>
  );
}
