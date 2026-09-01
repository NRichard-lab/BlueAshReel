import { ViewerShell } from '@/components/viewer-shell';
import { ViewerDetail } from '@/components/viewer-detail';
// Private media titles/artwork never appear in public share metadata.
export const metadata = {
  title: 'Private media details',
  description: 'Sign in to view this local media item.',
  openGraph: {
    title: 'Private media details',
    description: 'Sign in to view this local media item.',
    images: [],
  },
  twitter: {
    title: 'Private media details',
    description: 'Sign in to view this local media item.',
    images: [],
  },
};
export default async function Watch({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <ViewerShell currentPath="">
      <ViewerDetail mediaId={id} />
    </ViewerShell>
  );
}
