import { describe, expect, it } from 'vitest';

import type { MediaFolderSelection } from '@/lib/api';
import { libraryFolderAddBody, libraryFolderCreateBody } from '@/lib/media-folders';

const selection: MediaFolderSelection = {
  selection_id: 'mf1.opaque.signed',
  root_id: 'primary',
  name: 'Movies',
  display_path: 'Media / Movies',
  available: true,
  readable: true,
  read_only: true,
};

describe('media-folder mutation contracts', () => {
  it('creates setup and regular libraries with a signed folder selection', () => {
    expect(libraryFolderCreateBody('  Movies  ', 'movies', selection)).toEqual({
      name: 'Movies',
      library_type: 'movies',
      enabled: true,
      folder_ids: ['mf1.opaque.signed'],
    });
  });

  it('adds a browsed folder without exposing an internal path', () => {
    expect(libraryFolderAddBody(selection)).toEqual({ folder_id: 'mf1.opaque.signed' });
  });
});
