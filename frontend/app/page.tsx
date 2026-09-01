import { ViewerShell } from '@/components/viewer-shell';
import { ViewerHome } from '@/components/viewer-home';

export default function Home() {
  return (
    <ViewerShell currentPath="/">
      <ViewerHome />
    </ViewerShell>
  );
}
