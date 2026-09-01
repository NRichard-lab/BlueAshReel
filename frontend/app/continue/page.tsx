import { ViewerShell } from '@/components/viewer-shell';
import { ViewerCatalog } from '@/components/viewer-catalog';
export default function Continue() {
  return (
    <ViewerShell currentPath="/continue">
      <ViewerCatalog mode="continue" />
    </ViewerShell>
  );
}
