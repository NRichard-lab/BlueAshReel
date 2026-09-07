/**
 * PRESENTATION-ONLY placeholder content for the Agent Settings workspace.
 *
 * Nothing in this file is read from or written to the Agent. It exists so the
 * full settings experience can be reviewed before the matching read/write API
 * surface exists. Every value here renders behind a visible "Preview" / "Not
 * connected" marker. When Settings is wired to real Agent configuration, delete
 * this file and replace `settingsDefaults` + the status objects with live data.
 *
 * Rules:
 *  - No real public IPs, credentials, keys, pairing codes, or full internal IDs.
 *  - Addresses/paths use obviously-fake examples.
 *  - Boolean defaults for anything outbound (metadata, analytics, diagnostics,
 *    crash reporting, downloads) are OFF.
 */

/** Section ids — the order here is the order shown in the sidebar. */
export const SETTINGS_SECTIONS = [
  { id: 'general', label: 'General' },
  { id: 'libraries', label: 'Libraries' },
  { id: 'remote-access', label: 'Remote Access' },
  { id: 'transcoding', label: 'Transcoding' },
  { id: 'network', label: 'Network' },
  { id: 'users', label: 'Users & Access' },
  { id: 'player', label: 'Player' },
  { id: 'downloads', label: 'Downloads' },
  { id: 'privacy', label: 'Privacy' },
  { id: 'system', label: 'System' },
  { id: 'about', label: 'About' },
] as const;

export type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]['id'];

/**
 * Editable form defaults. Keys are stable ids so a real adapter can map them to
 * Agent config later. Values are only held in React state for this prototype.
 */
export const settingsDefaults: Record<string, string | boolean | number> = {
  // General
  agent_name: 'Living Room Agent',
  agent_description: 'Family movies and shows, kept on this workstation.',
  language: 'en',
  region: 'US',
  timezone: 'America/New_York',
  date_format: 'MMM D, YYYY',
  time_format: '12h',
  start_with_windows: true,
  auto_reconnect_portal: true,
  open_portal_after_start: false,
  check_for_updates: true,
  update_channel: 'development',
  auto_dev_updates: false,
  anonymous_diagnostics: false,

  // Libraries (section-wide toggles; per-library controls are display-only)
  auto_scan: true,
  scheduled_scan: 'daily-3am',
  scan_on_change: true,
  empty_trash_after_scan: false,
  generate_video_thumbs: true,
  generate_chapter_thumbs: false,
  analyze_audio: true,
  analyze_subtitles: true,

  // Remote Access
  remote_streaming_enabled: true,
  remote_max_quality: '1080p',
  remote_bitrate_limit: '12',
  remote_session_limit: '3',

  // Transcoding
  transcode_mode: 'automatic',
  hw_decoding: true,
  hw_encoding: true,
  allow_software_fallback: true,
  transcode_quality: 'balanced',
  background_preset: 'veryfast',
  max_video_transcodes: '2',
  max_audio_transcodes: '4',
  cpu_thread_limit: '0',
  temp_transcode_dir: 'D:\\AgentCache\\transcode',
  buffer_ahead: '120',
  hls_segment_duration: '4',
  max_output_resolution: '1080p',
  max_output_bitrate: '20',
  hdr_tone_mapping: true,
  subtitle_burn_in: 'image-only',
  delete_segments_after: true,

  // Network
  loopback_port: '18080',
  preferred_interface: 'auto',
  secure_connections: 'preferred',
  relay_connection: true,
  direct_remote: true,
  lan_discovery: true,
  allowed_lan_networks: '192.0.2.0/24',
  remote_bandwidth_limit: '25',
  connection_timeout: '30',
  reconnect_behavior: 'auto',
  proxy_mode: 'none',
  ipv6_support: 'auto',

  // Player
  default_quality: 'original',
  auto_select_quality: true,
  prefer_direct_play: true,
  allow_direct_stream: true,
  subtitle_preference: 'forced-only',
  audio_language_preference: 'original',
  autoplay_next: true,
  resume_behavior: 'ask',
  skip_intro: false,
  remember_subtitle: true,
  show_technical_info: false,
  fullscreen_behavior: 'match-window',
  player_buffer: 'balanced',

  // Downloads (planned)
  downloads_allowed: false,
  download_quality: 'original',
  download_dir: 'D:\\AgentDownloads',
  simultaneous_downloads: '2',
  download_temp_dir: 'D:\\AgentCache\\downloads',
  remove_watched_downloads: false,

  // Privacy
  external_metadata: false,
  external_artwork: false,
  crash_reporting: false,
  usage_analytics: false,

  // Users & Access (per-invite defaults, display only)
  default_allow_playback: true,
  default_allow_downloads: false,
  default_max_quality: '1080p',
  default_max_streams: '2',
  require_portal_mfa: true,
};

/** Header values shown at the top of Settings. */
export const agentHeaderPlaceholders = {
  friendlyName: 'Living Room Agent',
  connectionStatus: 'Connected' as
    | 'Connected'
    | 'Connecting'
    | 'Offline'
    | 'Relay'
    | 'Direct'
    | 'Unavailable',
  restartRequired: false,
};

/** Libraries section — sample library rows (real names come from /browse/libraries). */
export const librarySamples = [
  {
    id: 'sample-lib-movies',
    name: 'Movies',
    type: 'Movies',
    enabled: true,
    itemCount: 128,
    lastScan: '2 hours ago',
    status: 'ok' as 'ok' | 'scanning' | 'error',
    scanProgress: 0,
  },
  {
    id: 'sample-lib-tv',
    name: 'TV Shows',
    type: 'TV',
    enabled: true,
    itemCount: 42,
    lastScan: 'Yesterday',
    status: 'scanning' as const,
    scanProgress: 63,
  },
  {
    id: 'sample-lib-family',
    name: 'Family',
    type: 'Movies',
    enabled: false,
    itemCount: 0,
    lastScan: 'Never',
    status: 'ok' as const,
    scanProgress: 0,
  },
  {
    id: 'sample-lib-docs',
    name: 'Documentaries',
    type: 'Movies',
    enabled: true,
    itemCount: 17,
    lastScan: '4 days ago',
    status: 'error' as const,
    scanProgress: 0,
  },
];

/** Remote Access — status panel (no credentials, shortened ids only). */
export const remoteAccessPlaceholders = {
  portalStatus: 'Connected' as 'Connected' | 'Connecting' | 'Offline',
  pairedAccount: 'ac•••@example.com',
  agentIdShort: 'AGNT-••••-4F9C',
  fingerprintShort: 'F4B3 EAF8 •••• ••••',
  lastConnected: 'Just now',
  connectionMethod: 'Encrypted relay',
  relayStatus: 'Relay' as 'Relay' | 'Unavailable',
  directStatus: 'Unavailable' as 'Direct' | 'Unavailable',
};

/** Transcoding — hardware capability cards. "Not tested yet" until a real test. */
export const hardwareCapabilityCards = [
  {
    id: 'qsv',
    name: 'Intel Quick Sync',
    detail: 'Integrated GPU video engine',
    status: 'Not tested yet' as const,
  },
  {
    id: 'nvenc',
    name: 'NVIDIA NVENC',
    detail: 'Dedicated GPU encoder',
    status: 'Not tested yet' as const,
  },
  {
    id: 'amf',
    name: 'AMD AMF',
    detail: 'Radeon video encoder',
    status: 'Not tested yet' as const,
  },
  {
    id: 'cpu',
    name: 'Software / CPU',
    detail: 'Always available fallback',
    status: 'Available' as const,
  },
];

export const transcodingDetected = {
  gpu: 'Not detected yet',
  backend: 'Not selected',
  capability: 'Not tested yet',
};

/** Network — display-only values. */
export const networkPlaceholders = {
  localAddress: 'http://127.0.0.1:18080',
  publicHint: 'Hidden — shown only after a connection test',
  certificate: 'Using the Agent’s built-in local certificate',
};

/** Users & Access — sample people (no Portal data is touched). */
export const usersPlaceholders = {
  owner: { name: 'You', email: 'ow•••@example.com', role: 'Owner' },
  managers: [{ name: 'Sample Manager', email: 'mg•••@example.com' }],
  viewers: [
    { name: 'Sample Viewer A', email: 'va•••@example.com', libraries: 'Movies, TV Shows' },
    { name: 'Sample Viewer B', email: 'vb•••@example.com', libraries: 'Family' },
  ],
  pendingInvites: [{ email: 'in•••@example.com', sent: '3 days ago' }],
};

/** Privacy — local-first status rows. */
export const privacyStatusRows: { label: string; state: 'local' | 'off' | 'on' }[] = [
  { label: 'Media files', state: 'local' },
  { label: 'Media database', state: 'local' },
  { label: 'Artwork', state: 'local' },
  { label: 'Watch history', state: 'local' },
  { label: 'Transcoding', state: 'local' },
  { label: 'Encrypted relay to Portal', state: 'on' },
  { label: 'External metadata lookups', state: 'off' },
  { label: 'External artwork downloads', state: 'off' },
  { label: 'Anonymous diagnostics', state: 'off' },
  { label: 'Crash reporting', state: 'off' },
  { label: 'Usage analytics', state: 'off' },
];

export const privacyRetentionSummary =
  'Watch progress and library metadata are stored on this Agent until you remove a library or clear history. The Portal keeps only your account and this Agent’s pairing record.';

/** System — resource cards. Unknown values say so. */
export const systemPlaceholders = {
  agentStatus: 'Connected',
  uptime: 'Not measured',
  version: null as string | null, // real value comes from product config
  os: 'Not connected',
  cpu: 'Not connected',
  memory: 'Not measured',
  gpu: 'Not tested yet',
  databaseStatus: 'Not connected',
  databaseSize: 'Not measured',
  artworkCacheSize: 'Not measured',
  tempTranscodeUsage: 'Not measured',
  activeStreams: 'Not connected',
  currentTranscodes: 'Not connected',
  lastBackup: 'Placeholder — never',
  backupLocation: 'D:\\AgentBackups',
};

/** About. */
export const aboutPlaceholders = {
  channelWarning:
    'This is a development-channel Agent. It is unsigned, may change without notice, and is not intended for production use.',
  license: 'Blue Ash Reel is a Blue Ash application. See the bundled notices for component licenses.',
  ffmpegAttribution:
    'Playback uses FFmpeg, licensed under the LGPL/GPL. The exact build and source are included with the Agent.',
  docsUrl: '#',
  supportUrl: '#',
};
