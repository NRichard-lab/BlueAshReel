import { ViewerShell } from '@/components/viewer-shell';
import { ViewerCatalog } from '@/components/viewer-catalog';
export default function Movies() {
  return (
    <ViewerShell currentPath="/movies">
      <ViewerCatalog mode="movies" />
    </ViewerShell>
  );
}
