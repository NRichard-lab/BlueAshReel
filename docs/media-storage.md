# Approved media storage

Blue Ash Reel can browse and scan only host folders that the local operator has explicitly bind-mounted into the backend and worker. Source mounts are read-only. The web application cannot access the Docker socket, inspect an arbitrary host path, create a host mount, or modify source media.

## Browser experience

During first run, Blue Ash Reel establishes a signed, 30-minute setup session with an HttpOnly, SameSite-strict cookie and CSRF protection. This limited session can browse approved folders but cannot access libraries, playback, or administration. Folder names are never exposed through the public setup-status endpoint. Step 3 lets the user choose an approved folder without typing a container path; finishing setup creates the Owner and optional first library together, switches to the normal Owner session, and invalidates every setup browsing capability. No scan starts automatically.

After setup, Owners and Administrators can use the same picker while creating a library or adding a folder to an existing library. The Owner-only **Settings → Media Storage** page reports each configured root's friendly name, availability, readability, read-only enforcement, associated libraries, and last validation time. Its Browse action is a read-only preview and never starts a scan.

The picker:

- returns directories only;
- uses signed selection identifiers rather than accepting a host path;
- naturally sorts the complete bounded listing before pagination;
- rejects traversal, absolute-path injection, links, junctions, and reparse points;
- resolves and revalidates canonical containment when a folder is listed, selected, stored, and scanned;
- reports missing and permission-denied storage without logging the selected path; and
- supports breadcrumbs, Back, keyboard navigation, and phone/tablet layouts.

Advanced manual entry is retained for troubleshooting. It accepts only an existing internal path beneath a configured root. A Windows drive or UNC path is rejected with guidance to configure that host folder through bootstrap first; Blue Ash Reel never guesses a host-to-container mapping.

## Configure roots on Windows

For a new interactive installation, run:

```powershell
.\scripts\bootstrap.ps1
```

When desktop interaction is available, bootstrap offers a native folder dialog. Typed entry and Skip remain available. To configure or replace roots on an existing installation, make the change explicit:

```powershell
.\scripts\bootstrap.ps1 -ConfigureMediaRoots -MediaPath "E:\Family Media" -MediaLabel "Family Media"
```

Pass arrays to approve multiple roots:

```powershell
.\scripts\bootstrap.ps1 -ConfigureMediaRoots `
  -MediaPath "E:\Movies","F:\Television" `
  -MediaLabel "Movies","TV Shows"
```

Bootstrap validates each directory, preserves stable internal root identifiers for paths it has seen before, writes only ignored local configuration, validates the rendered Compose model, and recreates the stack. It refuses broad drive roots, links/reparse points, state-directory overlap, duplicate/nested roots, and an existing Compose override it does not own.

## Configure roots on Linux

Use interactive typed selection on a new installation or repeat `--media-path` explicitly:

```sh
sh scripts/bootstrap.sh --configure-media-roots \
  --media-path /srv/movies \
  --media-path /mnt/television
```

Directories must exist and be readable/traversable by the unprivileged operator account. The same overlap, link, duplicate, and broad-root safeguards apply.

## Local configuration contract

The first approved host folder maps to the stable compatibility target `/media`. Additional roots receive stable targets below `/media-roots/<id>`. Backend and worker receive identical read-only mounts; frontend, proxy, and backup do not receive media mounts.

Bootstrap owns these ignored local artifacts:

- `.env`, including `MEDIA_PATH`, `MEDIA_ROOTS`, and the host-path-free `MEDIA_ROOT_DEFINITIONS` value;
- `.bluereel/media-roots.tsv`, which preserves the local host mapping and stable identifiers; and
- `compose.override.yml` when additional bind mounts are needed.

Do not commit or share those files. Adding or changing a host root requires a controlled container recreation because Docker establishes bind mounts when containers start. There is deliberately no **Add Host Drive** web action.

## API boundary

All paths are beneath `/api/v1`:

| Endpoint | Access | Purpose |
| --- | --- | --- |
| `POST /setup/session` | First run only | Establish the limited, expiring setup session |
| `GET /media-roots` | Setup session, Owner, or Administrator | Enumerate approved roots without host paths |
| `POST /media-folders/browse` | Setup session, Owner, or Administrator + CSRF | List one bounded page of child directories |
| `POST /media-folders/validate` | Setup session, Owner, or Administrator + CSRF | Validate advanced internal entry and return a signed selection |
| `GET /media-storage` | Owner | Report storage state and library use |

Library creation (including `initial_library` during setup) accepts signed `folder_ids`; adding an existing library path accepts one signed `folder_id`. The server immediately resolves these to a canonical internal path and never stores the token as a library path. Legacy manual path writes pass through the same read-only and containment validation.

## Disconnected storage

If a drive is removed, Blue Ash Reel reports its approved root unavailable. Reconnect it at the same host path and run the bootstrap/Compose recreation if Docker no longer has the mount. Do not replace a read-only bind with a writable one. A path returning at a different host location must be configured explicitly so the stable container target remains deliberate.

See [the validation report](media-storage-validation.md) for automated coverage and the local Docker/browser acceptance results.
