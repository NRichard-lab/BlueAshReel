import { ViewerShell } from '@/components/viewer-shell';
import { ViewerCatalog } from '@/components/viewer-catalog';
export default function Shows() {
  return (
    <ViewerShell currentPath="/shows">
      <ViewerCatalog mode="shows" />
    </ViewerShell>
  );
}
