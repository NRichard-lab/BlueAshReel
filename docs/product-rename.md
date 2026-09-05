# Blue Ash Reel identity and upgrade compatibility

The customer-facing product is **Blue Ash Reel**, with domain `blueashreel.com`,
package-friendly name `BlueAshReel`, local server name **Blue Ash Reel Server** and
remote identity **Blue Ash Reel Agent**. `config/product.json` is authoritative for
runtime/browser titles and descriptions. The native builder reads the same file and
passes display/package/domain values to Inno Setup; service descriptions and installer
manifests read that identity too.

The GitHub repository was renamed to `NRichard-lab/BlueAshReel`. GitHub redirects the
old `NRichard-lab/BlueReel` URL, and existing Git history was preserved. The on-disk
checkout remains in its established BlueReel directory. The separate private portal
repository is `NRichard-lab/BlueAshReelPortal`.

Stable Windows service names (`BlueReel*` and `BlueReelDevelopment*`), installer AppIds,
registry keys, installation/data directories, backup formats, SQLite schema, API prefix,
cookie identifiers and environment variables retain their existing values. Historical
validation reports and artifact names remain historical evidence, not current branding.
No data was renamed or migrated for the product rename. New development installer output
uses `BlueAshReel-Setup-Development-x64.exe`; it remains unsigned and non-public.

Existing native installations use the same AppId and previous installation directory on
upgrade. New stable installations retain the migration-safe legacy default directory.
Shortcuts and service display descriptions use the current product identity. An installed
development instance continues to use its existing data and service identities.

The remote connector uses separate identity/control storage and does not add tables to
the local media SQLite database. Remote disablement/unpairing preserves all local users,
library records, files and watch history. See [Remote Access](remote-access.md).
