# Local playback

The viewer opens an authenticated playback session before requesting media. All
file, subtitle, manifest, and segment requests require the same active household
login and recheck assigned/enabled libraries and source fingerprints. Stream URLs
contain opaque IDs, never server paths; they are not permanent public links.

## Browser player

Use a current Chromium, Firefox, or Safari browser with H.264/AAC support. Browser
capability hints feed the versioned policy in `services/compatibility.py`; actual
decoding is checked by the player. Unsupported combinations report errors rather
than claiming success. Native fullscreen availability depends on the browser.

Direct Play streams compatible MP4 (or conservative VP9/Opus WebM) in bounded
chunks. Single byte ranges and HEAD support seeking; malformed or multiple ranges
return 416. Connected transfers periodically recheck authorization. The composite
player supports Space/K, arrow seeking, M mute, F fullscreen, and ordinary Tab
navigation. Audio/subtitle/quality choices take effect with **Apply playback
options**, creating a new authorized representation at the current position.

Text subtitles are converted locally to sanitized WebVTT. Formatting/style markup
is removed, timestamps retained, conversion time/output/concurrency bounded. No
subtitle lookup or upload occurs. Image subtitles need conversion or an explicit
unsupported error; native embedded audio switching is not assumed reliable.

## Progress and history

Progress checkpoints occur every 15 seconds and on pause, seek, and normal exit.
Browser termination/network loss can lose the final checkpoint. Ordered updates
prevent stale responses from rewinding progress. Resume survives logout and server
restart because it is stored in SQLite, not browser storage. Each user is isolated.

Restart creates a new session and preserves the old resume point until at least
two seconds of plausible playback advance. Seeking alone gives no watch credit.
Automatic completion defaults to 90% plus at least 30 seconds of actual progress
(half the duration for short clips). Completed items leave Continue Watching;
manual Watched/Unwatched remains available. The most recent active session owns
the resume point when multiple tabs play the same item.

Profile controls automatic next-episode playback and its countdown (5–60 seconds).
Browsers may require a Play gesture after navigation. Deleting your history also
invalidates your active playback sessions so old checkpoints cannot recreate it.
Owner retention settings apply locally; 0 means keep indefinitely, not disable
tracking. Backups include history until they are separately expired or deleted.

The direct-play layer precedes the local conversion/process-monitoring layer;
see the completed Phase 2 validation report for verified playback methods and
workstation limitations.
