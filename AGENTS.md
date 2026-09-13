# Blue Ash Reel development

Before making architectural, deployment, Agent settings, library, scanner,
transport, or shared API changes, read docs/architecture.md and verify it
against the current runtime/repository state. This is the established cross-component guide.

Codex owns Agent and main Portal/Web work. Claude owns the TV app; do not modify
the TV repository. Preserve media, watch history, manual identification, pairing
and install identity. No automatic version bumps, releases or installer builds.
