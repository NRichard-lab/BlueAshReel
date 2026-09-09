/**
 * PRESENTATION-ONLY placeholder content for the redesigned media interface.
 *
 * Nothing in this file is real Agent data. It exists so the new layout can be
 * reviewed with representative spacing, hierarchy and flow before the matching
 * API fields exist. Every value here is rendered behind a visible
 * "Sample content" marker (see <DevPlaceholder>). When these areas are wired to
 * real Agent endpoints, delete this file and the imports that reference it.
 *
 * Rules kept here on purpose:
 *  - No real file paths, filenames, media titles from the user's library, or
 *    external artwork URLs.
 *  - IDs are prefixed `sample-` and are not routable to a real media item.
 */

import type {
  MediaCardRecord,
  MediaDetailRecord,
  MediaFileRecord,
} from '@/lib/viewer';

/** Whether placeholder scaffolding should render at all. */
export const PLACEHOLDERS_ENABLED = true;

/** A neutral sample poster card. Poster art is intentionally absent so the
 *  polished "no artwork" fallback is what reviewers see. */
function sampleCard(
  seed: number,
  overrides: Partial<MediaCardRecord> = {},
): MediaCardRecord {
  return {
    id: `sample-${seed}`,
    library_id: 'sample-library',
    kind: 'movie',
    title: `Sample Title ${seed}`,
    year: 2016 + (seed % 9),
    available: false,
    file_id: null,
    duration_seconds: 60 * (82 + ((seed * 7) % 45)),
    height: 1080,
    poster_url: null,
    position_seconds: 0,
    watched: false,
    completion: 0,
    season_number: null,
    episode_number: null,
    show_id: null,
    ...overrides,
  };
}

function sampleRow(count: number, seedBase: number): MediaCardRecord[] {
  return Array.from({ length: count }, (_, i) => sampleCard(seedBase + i));
}

/** Horizontal rows on detail that have no data source yet. */
export const placeholderRows: Record<string, MediaCardRecord[]> = {
  related: sampleRow(8, 60),
};

/** Detail-page metadata fields with no Agent source yet. */
export const placeholderDetailMeta = {
  rating: { label: 'Critics', value: '—', note: 'Ratings not yet provided by the Agent' },
  contentRating: 'NR',
  genres: ['Genre A', 'Genre B', 'Genre C'],
  director: 'Director name',
  studio: 'Studio name',
  releaseDate: 'Release date',
  synopsis:
    'A longer synopsis will appear here once the Agent supplies descriptive metadata. Until then the interface shows the locally derived information only.',
};

/** Sample cast/crew for the detail page. Names are obviously generic. */
export const placeholderCast: { name: string; role: string }[] = [
  { name: 'Cast Member One', role: 'Character' },
  { name: 'Cast Member Two', role: 'Character' },
  { name: 'Cast Member Three', role: 'Character' },
  { name: 'Cast Member Four', role: 'Character' },
  { name: 'Cast Member Five', role: 'Character' },
  { name: 'Cast Member Six', role: 'Character' },
];

/** Rows for the future "Select Version" pop-out. No paths or filenames. */
export const placeholderVersions: {
  id: string;
  label: string;
  resolution: string;
  videoCodec: string;
  audioCodec: string;
  container: string;
  bitrate: string;
  directPlay: boolean;
  size: string;
  isDefault: boolean;
}[] = [
  {
    id: 'sample-version-1',
    label: 'Original',
    resolution: '1080p',
    videoCodec: 'H.264',
    audioCodec: 'EAC3 5.1',
    container: 'MKV',
    bitrate: '8.4 Mbps',
    directPlay: true,
    size: '4.1 GB',
    isDefault: true,
  },
  {
    id: 'sample-version-2',
    label: 'Compatibility',
    resolution: '720p',
    videoCodec: 'H.264',
    audioCodec: 'AAC 2.0',
    container: 'MP4',
    bitrate: '3.0 Mbps',
    directPlay: true,
    size: '1.6 GB',
    isDefault: false,
  },
];

/* -------------------------------------------------------------------------- */
/* DEV-preview fixtures — see components/viewer/dev-preview.tsx.              */
/* Used only when import.meta.env.DEV is true so the redesigned screens can   */
/* be reviewed without the Portal or a signed-in session.                    */
/* -------------------------------------------------------------------------- */

/** One page of poster-grid results (Movies / TV Shows) for dev preview. */
export const sampleCatalogPage: MediaCardRecord[] = sampleRow(18, 200).map(
  (c, i) => ({
    ...c,
    title: `Sample Title ${i + 1}`,
    available: true,
    file_id: `sample-file-c${i}`,
    watched: i % 5 === 0,
    completion: i % 4 === 1 ? 30 + i : 0,
    position_seconds: i % 4 === 1 ? 60 * (20 + i) : 0,
  }),
);

const sampleFile: MediaFileRecord = {
  id: 'sample-file-detail',
  available: true,
  container: 'mkv',
  duration_seconds: 60 * 118,
  bitrate: 8_400_000,
  video: [
    {
      index: 0,
      codec: 'h264',
      width: 1920,
      height: 1080,
      profile: 'High',
      level: 41,
      pixel_format: 'yuv420p',
      bit_depth: 8,
    },
  ],
  audio: [
    { index: 1, codec: 'eac3', channels: 6, language: 'eng', title: 'Surround 5.1' },
    { index: 2, codec: 'aac', channels: 2, language: 'eng', title: 'Stereo' },
  ],
  subtitles: [
    { index: 3, codec: 'subrip', language: 'eng', title: 'English', text_supported: true },
    { index: 4, codec: 'hdmv_pgs_subtitle', language: 'spa', title: 'Español', text_supported: false },
  ],
  size_bytes: 4_402_341_000,
};

/** A full media-detail record for the dev preview of /watch/:id. */
export const sampleDetail: MediaDetailRecord = {
  ...sampleCard(300, {
    title: 'Sample Feature Presentation',
    year: 2024,
    available: true,
    file_id: 'sample-file-detail',
    duration_seconds: 60 * 118,
    completion: 36,
    position_seconds: 60 * 42,
  }),
  files: [sampleFile],
  background_url: null,
};
