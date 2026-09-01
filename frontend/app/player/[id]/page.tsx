import { ViewerShell } from '@/components/viewer-shell';
import { MediaPlayer } from '@/components/media-player';
export const metadata = {
  title: 'Private local player', description: 'Authenticated local media playback.',
  openGraph: { title: 'Private local player', description: 'Authenticated local media playback.', images: [] },
};
export default async function Player({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ViewerShell currentPath=""><MediaPlayer key={id} mediaId={id} /></ViewerShell>;
}
