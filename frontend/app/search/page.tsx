import { ViewerShell } from '@/components/viewer-shell';
import { ViewerCatalog } from '@/components/viewer-catalog';
export default function Search() {
  return (
    <ViewerShell currentPath="/search">
      <ViewerCatalog mode="search" />
    </ViewerShell>
  );
}
