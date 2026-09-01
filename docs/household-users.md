# Household users and library permissions

The Owner opens **Administration → Household users** to create local accounts,
assign a single role, and select accessible libraries. There are no emailed invites
or cloud accounts. Share the initial password privately; use a unique passphrase.

- **Viewer:** browse assigned enabled libraries and manage their own history/preferences.
- **Administrator:** additionally manage libraries, scans, and non-Owner household accounts.
- **Owner:** additionally manage protected settings, privacy, Owner accounts, and stream monitoring.

Every role needs viewing assignments. Upgrading from Phase 1 grants existing Owners
access to existing libraries. A newly created library is assigned to its creator.
Administration can manage libraries without granting itself viewing access.

Disabling an account or changing its password/role revokes its sessions. Explicit
**Revoke sessions** also signs that person out. Library removal takes effect on the
next catalog or media request. No operation may remove, disable, or demote the last
active Owner. Administrators cannot edit Owner accounts or their own access.

Deleting an account requires an explicit history choice. With deletion selected,
watch progress is removed. Otherwise it is anonymized, retained only locally, and
cannot be resumed by a future account with the same name. Profile history deletion
only affects the signed-in person.

Media folder paths are write-only inputs. Browser responses show stable folder
labels, readiness, and capacity—not full filesystem paths. View or change native
storage locations in the server configuration.

## Viewer catalog

Home contains bounded rails; Movies, TV, Search, and Continue Watching paginate.
Search uses SQLite FTS5 prefix tokens, supports title/year, and combines show titles
with season/episode identifiers such as `Local Series S01E02`. Titles and artwork
come only from scanned local files. Missing artwork uses a clearly labeled placeholder.
No guessed synopsis, rating, cast, or external art is added.

This document accompanies the Phase 2A layer. The following playback layers add the
player, progress checkpoints, local conversion, and operational validation.
