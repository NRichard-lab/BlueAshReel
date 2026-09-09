# Agent metadata and artwork

Blue Ash Reel owns local opaque media IDs, file analysis and viewing history.
Optional providers supply descriptive metadata. The first provider is TMDB.
Only the Agent contacts the provider; the Portal and browser read normalized
local results through the existing encrypted relay. No central catalog table
or raw-provider response storage is introduced.

## Configuration

Get an **API Read Access Token** from your TMDB account's API settings. This is
the Bearer token for the v3 API, not a browser-side key. Store it on the Agent,
never in the Portal, source repository, browser settings, command arguments or
chat. The existing native configuration loader intentionally ignores inherited
user/machine environment settings and reads the installed private `.env`.

The recommended native setup is a protected text file containing only the token:

1. In the installed Agent data directory's `configuration` folder, create
   `tmdb-access-token.txt` using a local editor. Keep that folder's existing
   per-user permissions. Paste the token there and save it without quotes.
2. Add `TMDB_TOKEN_FILE=C:/absolute/AgentData/configuration/tmdb-access-token.txt`
   to that same folder's `.env`. This setting contains a filename, not the token.
3. Restart the Agent, then run the private `status` command below. It prints
   only whether the provider is configured and local metadata state counts.
4. Run `enrich` to queue existing libraries, or scan a library normally.

`TMDB_ACCESS_TOKEN` in the Agent's private `.env` or standalone worker
environment is also supported. It takes precedence over the token file. The
secret is excluded from generic configuration serialization and representation;
native child processes receive it only through their private environment.
Missing, unreadable or malformed tokens leave metadata unavailable and local
indexing/playback operational. Removing the token stops new provider requests
after Agent restart; existing locally cached metadata remains available.

| Setting | Default | Behavior |
| --- | --- | --- |
| `TMDB_TIMEOUT_SECONDS` | 10 | Per-request timeout, 1–30 seconds |
| `TMDB_RETRIES` | 2 | At most 3 retries, bounded exponential backoff |
| `METADATA_LANGUAGE` | en-US | Metadata language |
| `METADATA_REGION` | US | Certification region; no invented NR fallback |
| `METADATA_REFRESH_DAYS` | 30 | Completed records become eligible on enrichment/scan |

Supplying the token enables only native HTTPS access to `api.themoviedb.org`
and `image.tmdb.org`. It does not enable the broad legacy outbound switch.
Only public DNS answers and port 443 are accepted. The provider validates TLS,
rejects redirects, reuses bounded connections and sends Bearer credentials only
to the API origin. Image requests never carry authorization. Docker's existing
network isolation is preserved and may still block optional enrichment.

## Model and provider interface

`app/metadata/types.py` defines provider-neutral `Candidate`, `MetadataDetails`,
`Credit` and `ArtworkSource`. `provider.py` defines `MetadataProvider` and safe
`ProviderError` categories. It supports movie/series search and details,
season/episode details, credits, images, external IDs and image download.
`tmdb.py` implements that contract using reusable stdlib HTTPS connections.
It uses supported `append_to_response` values to combine detail subresources.
Provider payloads are validated and reduced to bounded normalized fields.

Migration `2d0100000001` adds `metadata_records`, with a unique owning media or
season ID, and three provider/cache attribution columns on `local_artwork`.
It preserves the media, file, library, hierarchy, watch, pairing and alias tables.
The backup validator recognizes both old revisions and the new head.

Normalized metadata includes title/original title, year/release or air date,
runtime, overview/tagline, language, regional certification, genres, studios,
networks, creators, countries, series status/counts, community rating/count,
bounded credits/recommendation IDs, provider/external IDs and lifecycle fields.
JSON fields contain only defined small arrays/maps, not raw TMDB responses.
The old unused `external_metadata_identifiers` stub is preserved for migration
compatibility; its provider-wide integer uniqueness is unsuitable for TMDB's
separate movie/TV namespaces. The new provider lookup index includes media kind.

`provider_data_updated_at` records local retrieval time, not a claimed provider
modification timestamp. TMDB/IMDb/TVDB IDs are retained when supplied; none
replace internal navigation IDs. Original filename-derived media records remain
intact and provide truthful fallback data.

## Matching and lifecycle

The deterministic matcher normalizes Unicode, punctuation and case, considers
title/original/alternate title, checks year, and penalizes incompatible local
runtime. It does not select by search-result order or popularity.

- Automatic: score at least 0.90 and at least 0.08 ahead of the next candidate.
- Review: score at least 0.70 without both automatic conditions.
- Unmatched: lower confidence or no plausible candidates.
- A unique exact title without a year can score 0.90; a conflicting year by
  more than one year caps the score below automatic/review confidence.

Movie details receive an additional runtime/title/year check before accepting
a new automatic match. TV searches match the local series once, then reuse
its identity and the season lookup for local SxxEyy episodes. Episodes are
retrieved from the provider's series/season/episode hierarchy, never searched
as unrelated movies. Only locally indexed seasons/episodes are enriched.

States are `unavailable`, `unmatched`, `needs_review`, `matched`, `fetching`,
`complete`, and `error`. Match confidence/method, manual confirmation and
timestamps are local. Routine scans never silently replace a manual provider
identity. Clearing a match disables automatic rematching until explicit refresh
or assignment. Reassigning/clearing a series invalidates its old season/episode
metadata to prevent a mixed hierarchy.

Scanning commits successful indexing before queueing a separate metadata job.
Provider failures cannot fail the indexing job. Completed data refreshes after
30 days when enrichment runs; unmatched/review/error attempts have a six-hour
routine-scan cooldown. Provider-wide authentication, throttling and transient
outages stop the batch. Jobs retry after at least 15 minutes (or the provider's
longer Retry-After period), at most three attempts;
successful records are preserved between retries. A failed-only retry command
retries error/unavailable/matched/interrupted records without refreshing healthy
completed metadata. No provider calls happen on viewer page loads.

## Artwork

Canonical selection favors English then language-neutral images, sensible aspect
ratios and resolution, with deterministic tie-breaking. The provider downloads
one poster/backdrop for movies/series, one season poster, one episode still and
at most ten people profiles. Configuration-supported target widths are 500,
1280, 780 and 185 pixels respectively; no bulk gallery download occurs.

Validated JPEG/PNG/WebP bytes, at most 8 MiB, are written atomically under
`ARTWORK_DIR/metadata/<hash-prefix>/<sha256>.<extension>`. The existing artwork
table stores attribution/provider reference, content hash/type and relative
cache path. Shared references reuse downloaded bytes; corrupt cached bytes
are repaired. Old index references are removed on successful refresh; physical
orphan-cache garbage collection is deferred. Full scans reconcile only local
sidecars, so they do not delete provider artwork records.

Provider artwork keeps an enabled owning-library path association to preserve
existing authorization joins. Its actual bytes are in the Agent artwork cache,
not in the media source folder. Removing that path invalidates associated art;
later enrichment can rebuild it. Cache readers reject links, traversal and files
outside the configured cache layout. All reads require current local library
authorization and the existing opaque `artwork.bytes` relay alias. Browser
assembly validates bounded 128 KiB chunks and an 8 MiB total before creating
an in-memory blob URL. Neither cache filenames nor TMDB paths cross the relay.

## Viewer mapping

`metadata/view.py` projects local records for `catalog.detail` and enriches the
existing `catalog.seasons` result. Movie/show cards use normalized titles/years;
Home layout and watch-history behavior are unchanged. Episodes use their own
title, air date, overview, rating and crew; genres/studios/certification and
principal cast can inherit series context. Series directors/writers are never
misattributed to episodes. People profile IDs are separately aliased as artwork.

Related titles are the intersection of provider recommendation IDs and current
authorized, enabled, locally playable catalog entries of the same kind. Empty
intersections remain empty. Technical video/audio/subtitle/duration data remains
FFprobe-derived. Ratings identify TMDB community votes, never IMDb or critics.
Missing metadata produces empty fields rather than sample content.

The Portal's Settings > About contains an approved bundled TMDB logo and required
non-endorsement notice. See the Portal report for UI files and browser validation.
Official references: [authentication](https://developer.themoviedb.org/docs/authentication-application),
[appends](https://developer.themoviedb.org/docs/append-to-response),
[image configuration](https://developer.themoviedb.org/reference/configuration-details),
and [attribution](https://developer.themoviedb.org/docs/faq).

## Private maintenance commands

With the Agent's backend on Python's module path, run:

```text
python -m app.metadata.cli --data-dir C:/absolute/AgentData status
python -m app.metadata.cli --data-dir C:/absolute/AgentData enrich
python -m app.metadata.cli --data-dir C:/absolute/AgentData refresh --library-id LOCAL_LIBRARY_ID
python -m app.metadata.cli --data-dir C:/absolute/AgentData retry
python -m app.metadata.cli --data-dir C:/absolute/AgentData search --media-id LOCAL_MEDIA_ID
python -m app.metadata.cli --data-dir C:/absolute/AgentData assign --media-id LOCAL_MEDIA_ID --provider tmdb --provider-id PROVIDER_ID
python -m app.metadata.cli --data-dir C:/absolute/AgentData clear --media-id LOCAL_MEDIA_ID
```

Commands without `--media-id` queue work for the normal worker. Single-media
commands execute locally and print only a normalized result/status. Standalone
development/container invocations may omit `--data-dir` and use private env
configuration. Candidate search/assignment supports movies and series; episode
identity follows its series. A full Fix Match/settings interface is deferred.
