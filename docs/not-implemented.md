# Phase 2 limitations and deferred work

Implemented: household roles/assignments, local movie/TV browsing and indexed search,
authenticated direct playback, per-user resume/watch history, embedded text subtitles,
explicit audio selection, progressive HLS remux, software conversion, quality limits
and Owner active-stream controls.

Current limitations:

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
