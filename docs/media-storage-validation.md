# Approved media storage validation

Validated on 2026-09-01, starting from `ba0e1f6f00ef82d2feaa8927fa7f1b0e36022c0a`.
This was a local Docker Desktop validation, not a public deployment.

## Automated checks

| Check | Result |
| --- | --- |
| Full backend suite in Linux with real FFmpeg | 125 passed, no skips; 87% backend coverage |
| Operational suite in non-root Linux | 32 passed; 2 Windows-only PowerShell tests skipped |
| Backend and operational tests on Windows | 144 passed, 15 platform/tool skips; 84% backend coverage |
| Frontend unit tests | 28 passed across 7 files |
| Python Ruff and strict mypy | Passed on Windows and Linux; 36 application modules type-checked |
| Frontend TypeScript and Oxlint | Passed |
| Production Docker image builds | Passed |

Windows skips comprise one symlink-creation capability test, twelve native FFmpeg
integration cases, and two POSIX-only bootstrap tests. The backend
symlink and FFmpeg cases pass in Linux. Both POSIX bootstrap cases and backup
permission assertions also pass in the non-root Linux operational run. The
frontend build retains the existing
large-chunk advisory; it is not a build failure.

Regression coverage includes approved and multiple roots, directory-only natural
sorting and pagination, empty/missing/permission-denied storage, signed selection
and cursor tampering, traversal, absolute and similar-prefix paths, symlinks and
reparse points, read-only enforcement, log redaction, manual validation, and
friendly saved-library labels on a secondary root. Setup tests cover expiring
signed cookies, CSRF, role boundaries, atomic Owner/library creation, and the
invalidation of setup access after an Owner exists.

## Real same-origin browser checks

- The real installation's Step 3 displayed its approved **Media** root, reported
  read-only enforcement, and accepted the root through **Select Folder**. The
  selected-folder field was read-only and showed a friendly label. This root had
  no immediate child folders, so the empty-folder message was accurate.
- A disposable loopback-only stack used separate state directories and a separate
  secret, while sharing the same real media bind read-only. Its complete first-run
  flow created a synthetic Owner and first library from a browsed selection.
  Database, application data, temporary storage, FFprobe, and FFmpeg checks passed.
- An additional disposable root exercised nested navigation, multiple root
  choices, empty folders, and a 105-folder listing. Pagination returned all 105
  entries in natural order without showing the dummy media-file entry.
- Creating another library and adding a second folder to it through Browse both
  succeeded. Saved paths retained the secondary root's friendly name. Advanced
  manual entry rejected Windows host paths with bootstrap guidance and rejected
  parent traversal. Choosing a browsed folder cleared stale manual errors.
- The Owner's Media Storage page showed availability, readability, enforced
  read-only mounts, validation time, and correct library associations. Advanced
  exposed the internal identifier/path. Browse preview did not mutate a library.
- Enter, arrows, Home, End, Back, breadcrumbs, and Escape were exercised.
  Navigation focused the first readable child, or Select Folder for an empty
  folder. Loading another page focused its first new folder; Escape returned
  focus to Browse.
- Dialogs were visually inspected at 390x844, 768x1024, and 844x390. Headers,
  breadcrumbs, and action buttons remained visible while only the folder-list area
  scrolled. Deep-list keyboard focus did not scroll the outer dialog.

The real installation's unsaved test inputs were cleared. Its existing database
still contained no Owner, libraries, media items, or background jobs. No scan was
started on either installation.

## Docker and privacy boundary

The explicitly selected Windows host directory maps to `/media` in both backend
and worker. Its actual host path is kept only in ignored local configuration.
Both containers could read the root; fresh exclusive-create probes failed with
`EROFS`, and no probe file remained. Linux mount inspection independently reported
read-only enforcement.

All four services became healthy after the final controlled recreation. Only
`127.0.0.1:8080` is published. Frontend has no source mounts; proxy has only its
read-only Caddy configuration. No service receives the Docker socket. The secret
and all existing application/database/artwork/temporary/configuration mount
locations remained unchanged; the backup path also remained unchanged.

The private network remains internal. Proxy IPv4 and IPv6 OUTPUT/FORWARD policies
remain DROP, with upstream rules matching the recreated backend/frontend.
Supervisors and Caddy run as UID 1000 with zero capabilities and no-new-privileges.
Bounded public IPv4, IPv6, and DNS probes were blocked. Root, live, and ready HTTP
checks returned 200 after recreation.

## Artifacts and publication boundary

The disposable browser containers and networks, and dedicated backend test
containers/images, were removed. Local execution policy refused recursive removal
of the disposable browser fixture directory in the operating system's temporary
area. That directory is outside the repository, is not used by the real app, and
is not published. No real media or persistent application data was deleted.

The source audit found no real secrets, databases, media, logs, backups, transcodes,
dependency directories, build output, or generated runtime configuration among
the publication candidates. Safe examples, migrations, source fixtures, tests,
lockfiles, Docker configuration, bootstrap scripts, and documentation remain
included. Existing Git history is preserved.
