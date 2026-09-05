# Native development dependency audit — 2026-09-05

This audit covers the private unsigned Windows development installer. It does
not create a public release or certify a future commercial distribution.

The prior `artifacts/native-dev/phase3-service` EXE is 577,722,682 bytes,
version `0.1.0-dev.2`, SHA-256
`1a00221f71e237356767e5764d750fba0ac4771c7922d626ec4c04491f3a8b47`.
Its embedded inventory reports clean source commit
`df26a7b6ab5b2f024cb572198b116a85d785ea14`, predating the current development
changes. It is historical evidence, not the canonical newly accepted artifact.
The previous payload had 5,123 inventoried files and 661 component records.
No text reference to the build checkout was found inside that payload. This
phase removes unused frontend test fixtures, command-line bins, source maps and
type declarations from the installed runtime; corresponding-source archives
remain intact.

New builds require a clean committed checkout, rebuild the frontend by default,
verify downloaded hashes and Python wheel hashes, and generate relative-path
payload inventories. `installer-artifact.json` records filename, size, SHA-256,
development version, Windows file version, source commit/clean status, build
time and inventory hashes. The canonical destination is
`artifacts/development/BlueAshReel-Setup-Development-x64.exe`. Version
`0.1.0-development.3` is an ignored acceptance candidate; the intended final
version is `0.1.0-development.4`. They use the same reviewed dependency pins.

| Component | Exact version/source | Redistribution evidence |
| --- | --- | --- |
| CPython embedded x64 | 3.13.15, python.org archive + matching upstream SPDX SBOM | PSF license and upstream third-party notices, including SQLite/OpenSSL/runtime components |
| Python application dependencies | Exact versions and SHA-256 hashes in `packaging/windows/requirements.lock` | Original wheel metadata/notices copied individually; no target-side pip |
| Node.js x64 | 24.19.0, nodejs.org archive | Original Node LICENSE, including its bundled third-party notices; npm/corepack are excluded |
| Compiled frontend | `frontend/pnpm-lock.yaml` integrity pins | Per-package original notices and package integrity; runtime versus conservative browser build-input attribution is identified |
| Caddy | 2.11.4, official release archive | Apache-2.0; binary hash checked against official SBOM; notices for 147 compiled Go components plus pinned source |
| WinSW | 2.12.0, official NET461 executable | MIT, exact original notice, four merged component notices and source pins; Windows supplies .NET Framework 4.8 |
| FFmpeg / FFprobe | 8.1.2, commit `38b88335f99e76ed89ff3c93f877fdefce736c13` | GPL-3.0-or-later configuration, binary SHA-256, complete source/build controls and original notices |
| x264 | `31e19f92f00c7003fa115047ce50978bc98c3a0d` | GPL-2.0-or-later source and notice; combined FFmpeg binary distributed under GPL-3.0-or-later |
| NVIDIA codec headers | 13.1.15.0 | MIT header notices and exact source |
| AMD AMF headers | 1.5.2 | MIT notice and exact source |
| Intel libvpl dispatcher | 2.17.0 | MIT notice and exact source |
| Inno Setup engine | 7.1.0 | Original Inno Setup license retained; compiler itself is build-only |

Exact download URLs/checksums are in `components.lock.json` and
`additional-components.json`; there are no `latest` download pins. Runtime
executables, libraries, notices, source archives and installed application code
are independently inventoried with SHA-256. The FFmpeg source reproduction
instructions are in `packaging/windows/ffmpeg-redistribution.md`, also bundled
beside corresponding source. FFmpeg's GPL-enabled build is deliberately not
described as LGPL-only. [FFmpeg explains the GPL distinction](https://ffmpeg.org/legal.html).

One of the four previous missing npm notices is now supplied from the actual
`react-remove-scroll-bar` project: its author added an authentic MIT notice in
commit `7301c160fda44cb8cf2b9fdfde61efad35736196`. That exact notice is pinned and
retained together with the original 2.3.8 published tarball and MIT declaration.
The notice is not falsely described as having been present in the npm tarball.
[Original project notice](https://github.com/theKashey/react-remove-scroll-bar/blob/7301c160fda44cb8cf2b9fdfde61efad35736196/LICENSE).

Three gaps remain explicitly marked `upstream_full_license_text_missing`:

- `css-box-shadow@1.0.0-3`: the package and its author repository at published
  commit `6fc6c3716752879fa683f7925dbc51cf2c7930ee` contain an MIT declaration but
  no full LICENSE file.
- `unpic@4.2.2`: the published commit
  `eefd6174338cc1377f8f238b84af94f48acd6da4` and inspected current upstream tree
  contain an MIT declaration but no full LICENSE file.
- `@unpic/core@1.0.3`: the release tag resolves to
  `35df62b9aa22cc9e6ede939eb443023cd9179f60`. It declares MIT but has no core/root
  notice. Other framework packages' notices are not relabeled as this package's.

These are transitive frontend framework dependencies. Their original package
metadata, README and complete published tarballs are retained; no copyright
statement is invented. Their presence is an explicit attribution limitation for
this private development artifact. A public release should resolve these
upstream omissions or remove the relevant dependency paths after runtime tests.

The current [Inno license](https://jrsoftware.org/files/is/license.txt) permits
redistribution with original notices retained. Although the unregistered
compiler prints a non-commercial message, the publisher's
[commercial-license FAQ](https://jrsoftware.org/isorder.php) says purchase is
requested, not strictly required. No purchase or license entitlement is claimed.
The installer engine's notice is now required by the builder rather than
silently omitted if missing.
