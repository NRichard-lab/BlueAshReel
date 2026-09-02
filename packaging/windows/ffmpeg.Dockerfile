FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS build

# Build-only tools, never installed on the BlueReel target. This independent
# build container has no media/state mounts and never touches the Docker app.
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates make cmake ninja-build pkg-config nasm xz-utils gcc libc6-dev \
    gcc-mingw-w64-x86-64-posix g++-mingw-w64-x86-64-posix \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY artifacts/native-dev/ffmpeg-sources/ /sources/
COPY packaging/windows/build-ffmpeg.sh /build/build-ffmpeg.sh
COPY packaging/windows/mingw-toolchain.cmake /build/mingw-toolchain.cmake
RUN sh /build/build-ffmpeg.sh

FROM scratch AS export
COPY --from=build /output/ /
