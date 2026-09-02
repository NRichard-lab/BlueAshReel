# Phase 2 limitations and deferred work

Implemented: household roles/assignments, local movie/TV browsing and indexed search,
authenticated direct playback, per-user resume/watch history, embedded text subtitles,
explicit audio selection, progressive HLS remux, software conversion, quality limits
and Owner active-stream controls.

Current limitations:

- The native Windows installer is unsigned and development-only. Windows 11 was
  exercised on this workstation; Windows 10, a second clean Windows machine,
  reboot/sign-out, and other GPU/driver combinations need separate validation.
- Native upgrade creates and validates a backup and retains prior program/config
  files. Automatic rollback is not implemented; recovery is explicitly manual.
- Native hardware acceleration covers verified H.264 encoding. Decode, AAC audio
  and text subtitle conversion remain on the CPU. No hardware result is inferred
  from a GPU name alone.
- Caddy's administration API is disabled. Native API, worker and web stop
  cooperatively; the stateless proxy is terminated, not gracefully drained.
- Network shares are an advanced case requiring service-account access and a
  reviewed network policy; the strict-local native profile does not enable them.
- Bundled dependency provenance, notice gaps and the WinSW/log4net development
  risk are recorded in [native packaging](native-windows.md#components-and-licenses).
- Image-based subtitle burn-in is unsupported; choose a text track or turn subtitles off.
- Single selected quality, not an automatic adaptive-bitrate ladder.
- HLS seeks create a precise local transcode from the requested position.
- Separate sidecar-subtitle discovery is not provided; embedded text styling is stripped.
- Chromium was tested locally; Safari/Firefox and production GPU/driver combinations need workstation validation.
- One API process and one scan worker. Completed output is reusable while referenced, then deleted.
- Forced browser termination may lose the final 15-second checkpoint.
- Docker Desktop/WSL2 container playback, recovery and no-egress were validated with synthetic media; see [the validation scope](container-validation.md). Long household files and other browser/GPU combinations still need operator testing.

The following remain deliberately deferred:
- remote/public access, managed TLS, relay services, and router automation;
- IMDb, TMDB, TVDB, or any other metadata-provider calls and matching;
- downloading remote posters, backgrounds, subtitles, or trailers;
- recommendations, discovery feeds, social features, or analytics;
- live television, tuners, electronic program guides, recording, and DVR;
- Google TV or other native television/mobile applications;
- Blue Ash portal integration or production deployment;
- cloud storage, cloud accounts, remote backup, and hosted crash reporting;
- password reset by email, invitations, federation, or external identity providers;
- multi-node operation, PostgreSQL, Redis, Kubernetes, or a distributed queue;
- unattended upgrades and destructive automated restore; and
- general unauthenticated server-filesystem browsing.

Interfaces and normalized identifiers make several later additions possible, but a placeholder schema or extension point is not a claim that the feature exists. New outbound functionality must follow the opt-in and audit requirements in [privacy and outbound connections](privacy-and-outbound.md).
