# Local remuxing, conversion and hardware

Version-1 compatibility rules consider capabilities, container, codec, profile/level,
pixel format/bit depth, resolution/bitrate limits and selected tracks. Unknown
combinations are converted conservatively or rejected explicitly.

- **Direct Play:** compatible MP4 H.264/AAC or conservative 8-bit VP9/Opus WebM.
- **Remux:** compatible streams copied into progressive local HLS, including explicit
  multi-audio mapping. Native embedded audio switching is not assumed reliable.
- **Transcode:** incompatible video to H.264, incompatible/multichannel audio to stereo
  AAC, resolution/bitrate reduction, or precise offset seeking. Compatible video is
  copied when only audio needs conversion.
- **Unsupported:** no safe browser output, unknown duration, or image-based subtitles.

The bundled hls.js dependency is served locally, with its worker disabled to stay
within the reference CSP. Native HLS is a fallback. Generated EVENT playlists start
before complete processing. Every request checks the original login, session state,
library grant, enabled path and source size/mtime fingerprint. Sources are never
modified; paths never appear in playback responses or routine logs.

## Limits and lifecycle

Defaults: eight streams, two per user, two conversion reservations, two decoder/encoder
threads per job, single-thread filters, 2160p/12 Mbps maximum, 4 GiB combined temporary
budget, 45-second startup and 90-second missed-heartbeat expiry. Two separately bounded
subtitle conversions are allowed. Hardware testing is an explicit Owner operation.

Output allocation is divided by conversion slots; allow space for a full representation
(about 5.4 GB/hour at 12 Mbps). Increase the budget for long media or select a lower
quality. Storage polling may overshoot by output produced during a 250 ms interval;
leave disk headroom. Retained orphan bytes count toward reporting/admission.

Cache identity includes source identity/fingerprint, rule version, tracks, quality,
encoder and logical offset. Completed matching output is reusable by authorized active
sessions. The last reference stopping deletes output. No speculative encoding or
unbounded persistent cache. New scans yield to converted playback; existing scans may
finish current work. HLS seeks transcode both video/audio for accurate timestamp alignment;
subtitle cues are retimed by the returned logical offset.

Supervisors use parent-pipe ownership. Stop, expiry, revoked access, source loss and
shutdown terminate/reap FFmpeg before releasing directory locks. Only unlocked, marked,
generated directories directly below TEMP_DIR/playback are removed. Symlinks/junctions
and unrelated files are not followed. Failed/unconfirmed stops remain visible to Owners
and deny further playback; success waits for confirmed termination and cleanup.

## Optional hardware

TRANSCODE_HARDWARE accepts software, qsv, nvenc, or amf. System Health's **Test hardware
encoders** runs tiny local encodes without household media. A configured accelerator
is enabled only after a successful test; failed startup falls back to libx264. Stream
copy never claims hardware encoding. Detection is process-local and must be rerun
after restart. Container GPU access is not enabled in base Compose; use reviewed narrow
device mappings and suitable workstation drivers without privileged mode or egress.

Implementation references: [FFmpeg HLS](https://ffmpeg.org/ffmpeg-formats.html#hls-2),
[FFmpeg seeking](https://ffmpeg.org/ffmpeg.html),
[hls.js API](https://github.com/video-dev/hls.js/blob/master/docs/API.md).
