import { ViewerShell } from '@/components/viewer-shell';
import { ViewerProfile } from '@/components/viewer-profile';
export default function Profile() {
  return (
    <ViewerShell currentPath="/profile">
      <ViewerProfile />
    </ViewerShell>
  );
}
