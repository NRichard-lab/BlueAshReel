# Playback & Transcoding

Docker and native Windows use the same Owner-only `/api/v1/transcoding-policy`
API, playback engine and `application_settings` record. Each new stream stores a
validated policy snapshot in its existing `playback_sessions.decision` JSON.
No platform-specific database or migration is introduced. Changing a policy does
not stop streams or change their encoder, timeout, scratch directory or preset.

| Mode | Compatible media | Video conversion |
| --- | --- | --- |
| Automatic — Recommended | Direct Play, then Remux | Verified preferred/available H.264 hardware encoder, otherwise software |
| Hardware Preferred | Direct Play, then Remux | Verified preferred/available H.264 hardware encoder, otherwise software |
| Software Only | Direct Play, then Remux | CPU `libx264` only; no GPU initialization |
| Direct Play and Remux Only | Direct Play or stream-copy Remux | Rejected, including audio-only conversion and precise remux seeking |
| Hardware Required (advanced) | Direct Play, then Remux | Explicit selected, tested encoder/device only; no software video fallback |

Hardware Preferred does not waste resources converting already-compatible media.
Automatic and Hardware Preferred both report why software is being used when
hardware is unavailable. Hardware Required rejects audio-only transcoding because
the available hardware encoders accelerate video, not AAC audio.

## Conservative limits

Defaults are two conversions, two FFmpeg threads, 12 Mbps output, a `veryfast`
CPU preset, 4 GiB scratch budget and 90-second inactive-session expiry. Conversion
of 4K sources is disabled until the Owner opts in. Compatible 4K Direct Play and
stream-copy Remux remain available. Maximum output height defaults to 2160 but
does not override the separate 4K-transcoding permission.

Completed HLS output retains a storage reservation until its last referencing
stream stops. Reservations bound future growth, not merely bytes already written.
Changing limits affects new admissions; it never kills existing playback to make
the new limit fit. Scratch directories must be absolute local paths, writable by
the service, outside approved source-media roots and Windows program directories,
and free of symbolic links/junctions. Multiple old/new scratch roots can coexist
while streams finish. Unavailable configured scratch storage is reported in health
and blocks conversions without preventing the Owner from correcting Settings.

## Verified hardware, not GPU guesses

The self-test launches the exact configured (bundled on native Windows) FFmpeg
through the existing bounded process supervisor. It encodes three generated
128×128 frames using each of `h264_qsv`, `h264_nvenc` and `h264_amf`; no source
media or network resources are involved. Adapter input/output options and pixel
format match those used for normal encoding. An encoder is usable only after a
successful test for the same executable identity and device selection. A changed
executable or selected device invalidates eligibility. The health response records
the tested executable's SHA-256, test time, device and tested codec (`h264` only).
GPU inventory and encoder listings alone never enable acceleration.

The Owner can retest from Settings when no conversions are running. Automatic
testing is also attempted before the first video conversion when the binary or
device has changed and the manager is idle. Otherwise new streams safely use CPU,
or fail explicitly in Hardware Required. Tests and conversions are local-only.

Adapter `auto` lets the chosen encoder select its default device. Numeric device
indices are passed explicitly: NVENC `-gpu`, QSV `child_device` (DirectX index on
Windows; render-node index on Linux), and AMF D3D11 device selection on Windows.
Different encoder vendors have different device-index namespaces; the numeric
selection must pass its own retest. Unsupported choices are never silently treated
as verified. See the [FFmpeg hardware device documentation](https://ffmpeg.org/ffmpeg.html#Advanced-Video-options).

This build accelerates H.264 **video encoding only**. Decode, AAC audio encoding,
scaling and text-subtitle conversion use CPU. Text subtitles convert to local
WebVTT; image subtitles and burn-in remain unsupported and are rejected clearly.

## Failure, recovery and reporting

If hardware fails or times out during stream startup, Automatic and Hardware
Preferred perform one software attempt. Software Only never initializes a GPU.
Hardware Required never performs the software attempt. The failed hardware is
disabled until a successful retest.

If hardware fails after playback starts, the player can request one authenticated
software reconnect at its current position. `/playback/{id}/recovery` reports
eligibility; `recovery_from` on session creation atomically consumes it. Recovery
requires the same user, login session and source file, a confirmed failed hardware
encoder, and the original Automatic/Hardware Preferred policy. It preserves the
original track/quality/policy snapshot and forces software. Neither software
failures nor Hardware Required can enter this path. Source changes reject recovery.
This reconnect may briefly interrupt playback; it is not seamless encoder swapping.

Active Streams reports the actual video encoder, audio encoder, mode snapshot,
fallback flag/reason and method label. Recent failures are separate from the count
of active streams; client cleanup preserves failure records. Routine diagnostics
contain no source filenames, paths, title metadata, FFmpeg stderr or command lines.

Regression tests live in `backend/tests/test_transcoding_policy.py` and the shared
playback/transcoding suites. The mocked tests verify policy enforcement and
recovery decisions; they are not evidence that a real GPU is available. Actual
hardware availability must be checked with the installed service identity and
bundled binary on the target machine.
