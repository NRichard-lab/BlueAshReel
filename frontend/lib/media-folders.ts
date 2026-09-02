import type { MediaFolderSelection } from '@/lib/api';

export type LibraryKind = 'movies' | 'tv' | 'other';

export function libraryFolderCreateBody(
  name: string,
  libraryType: LibraryKind,
  folder: MediaFolderSelection,
) {
  return {
    name: name.trim(),
    library_type: libraryType,
    enabled: true,
    folder_ids: [folder.selection_id],
  };
}

export function libraryFolderAddBody(folder: MediaFolderSelection) {
  return { folder_id: folder.selection_id };
}
