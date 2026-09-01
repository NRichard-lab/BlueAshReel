# What is not implemented yet

Phase one is an administration, catalog, scanner, privacy, and operations foundation. The following are deliberately deferred:

- browser media playback and streaming delivery;
- transcoding orchestration, adaptive bitrate packaging, and hardware acceleration;
- remote/public access, managed TLS, relay services, and router automation;
- IMDb, TMDB, TVDB, or any other metadata-provider calls and matching;
- downloading remote posters, backgrounds, subtitles, or trailers;
- recommendations, discovery feeds, social features, or analytics;
- live television, tuners, electronic program guides, recording, and DVR;
- Google TV or other native television/mobile applications;
- Blue Ash portal integration or production deployment;
- cloud storage, cloud accounts, remote backup, and hosted crash reporting;
- Administrator/Viewer user-management UI beyond the authorization/data foundation;
- password reset by email, invitations, federation, or external identity providers;
- multi-node operation, PostgreSQL, Redis, Kubernetes, or a distributed queue;
- unattended upgrades and destructive automated restore; and
- general unauthenticated server-filesystem browsing.

Interfaces and normalized identifiers make several later additions possible, but a placeholder schema or extension point is not a claim that the feature exists. New outbound functionality must follow the opt-in and audit requirements in [privacy and outbound connections](privacy-and-outbound.md).
