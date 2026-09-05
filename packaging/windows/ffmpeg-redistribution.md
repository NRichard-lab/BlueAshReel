# Bundled FFmpeg corresponding source

The separate FFmpeg and FFprobe executables are FFmpeg 8.1.2, configured with
`--enable-gpl --enable-version3 --enable-libx264`. Their distribution license is
GPL-3.0-or-later. This is not an LGPL-only build. No `--enable-nonfree` component
is enabled. The media application invokes these executables as separate local
processes; it does not load FFmpeg libraries into its Python or Node process.

This directory accompanies the executables in the same installer and contains:

- Complete checksum-pinned source archives for FFmpeg, x264, Intel libvpl, NVIDIA
  codec headers and AMD AMF headers. Exact commits, URLs and SHA-256 values are
  recorded in `../packaging/components.lock.json` and `included-components.json`
  at the installed application root.
- `build-ffmpeg.sh`, `ffmpeg.Dockerfile`, `mingw-toolchain.cmake`, the actual
  `ffmpeg-config.mak`, build-tool versions, Windows DLL imports and binary hashes.
- Original GPL and dependency notices in the installed `licenses/ffmpeg` folder.
  The statically linked GCC/MinGW runtimes retain their notices and applicable
  runtime exceptions. The compiled binaries require only inbox Windows DLLs;
  optional hardware encoding requires the corresponding vendor driver.

No FFmpeg or x264 source patches are applied. The script appends the libstdc++
link requirement to generated libvpl pkg-config metadata. Generated build paths
such as `/build` and `/output` are container paths, not dependencies on a user's
Windows checkout. The historical FFmpeg extra-version label is a build label,
not the Agent's installer version.

To rebuild, create a source workspace containing these three build-control files
under `packaging/windows/` and the five `.tar.gz` archives under
`artifacts/native-dev/ffmpeg-sources/`. Run Docker BuildKit with
`docker build -f packaging/windows/ffmpeg.Dockerfile --target export --output type=local,dest=ffmpeg-output .`.
The build container uses the recorded Debian base digest and prints/verifies all
source hashes before compiling. It has no media or application database mounts.
Docker and compilation tools are needed only to rebuild these sources, never
to install or run the distributed Agent. Exact build-tool versions are recorded;
the Debian package repository can change, so byte-identical rebuilding is not
claimed. Always verify the resulting binary hashes and Windows import list.

Preserve the original notices and this complete corresponding source alongside
any redistribution of these executables, including redistribution through an
authenticated download. See [FFmpeg's licensing explanation](https://ffmpeg.org/legal.html)
and the included GPL text. Source availability is provided in this installer;
it is not deferred to a future written offer. This technical inventory does not
assert patent clearance or certify legal compliance for a future public release.
