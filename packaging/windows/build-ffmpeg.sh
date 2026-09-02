#!/bin/sh
set -eu
export SOURCE_DATE_EPOCH=1786492800
export LC_ALL=C
export PKG_CONFIG_LIBDIR=/opt/bluereel/lib/pkgconfig
export PKG_CONFIG_PATH=/opt/bluereel/lib/pkgconfig
export CFLAGS='-O2 -static -static-libgcc'
export CXXFLAGS='-O2 -static -static-libgcc -static-libstdc++'

# These are the same cryptographic source pins as components.lock.json.
cd /sources
echo '2ae7e42343cfffb811d15cfe98b6d005f082595fcdf034d30a4ff90cfed9f9c6  ffmpeg.tar.gz' | sha256sum -c -
echo 'd053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9  x264.tar.gz' | sha256sum -c -
echo '1d2070546de622fd6074a99d4b283e727988b7c3624ef85f97b88962264314d2  nv-codec-headers.tar.gz' | sha256sum -c -
echo 'd9cde24be31e847e78d31e4d01cfc6a461eac35c77557578436a407f621de26b  amf.tar.gz' | sha256sum -c -
echo '0a73dcda7abd6cde00d11a58dceb25670a93df5e98c80ef9c96aa1fcdff8ce9a  libvpl.tar.gz' | sha256sum -c -
for name in ffmpeg x264 nv-codec-headers amf libvpl; do
    mkdir -p "/build/$name"
    tar -xzf "/sources/$name.tar.gz" --strip-components=1 -C "/build/$name"
done

cd /build/x264
./configure --host=x86_64-w64-mingw32 --cross-prefix=x86_64-w64-mingw32- \
    --prefix=/opt/bluereel --enable-static --enable-pic --disable-cli \
    --disable-opencl --disable-lavf --disable-swscale
make -j4
make install

cd /build/nv-codec-headers
make PREFIX=/opt/bluereel install
mkdir -p /opt/bluereel/include/AMF
cp -a /build/amf/amf/public/include/. /opt/bluereel/include/AMF/

cd /build/libvpl
cmake -S . -B build -GNinja -DCMAKE_TOOLCHAIN_FILE=/build/mingw-toolchain.cmake \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/bluereel \
    -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_DISPATCHER=ON -DBUILD_DEV=ON \
    -DBUILD_PREVIEW=OFF -DBUILD_TOOLS=OFF -DBUILD_TOOLS_ONEVPL_EXPERIMENTAL=OFF \
    -DINSTALL_EXAMPLE_CODE=OFF -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTS=OFF
cmake --build build --parallel 4
cmake --install build
printf '\nLibs.private: -lstdc++\n' >> /opt/bluereel/lib/pkgconfig/vpl.pc

cd /build/ffmpeg
./configure --target-os=mingw32 --arch=x86_64 --enable-cross-compile \
    --cross-prefix=x86_64-w64-mingw32- --cc=x86_64-w64-mingw32-gcc-posix \
    --cxx=x86_64-w64-mingw32-g++-posix --pkg-config=pkg-config \
    --pkg-config-flags=--static --prefix=/output --enable-static --disable-shared \
    --disable-autodetect --enable-gpl --enable-version3 --enable-libx264 \
    --enable-amf --enable-ffnvcodec --enable-nvenc --enable-nvdec --enable-cuvid \
    --enable-libvpl --enable-d3d11va --enable-dxva2 \
    --disable-network --disable-doc --disable-ffplay --disable-debug \
    --extra-cflags=-I/opt/bluereel/include \
    --extra-ldflags='-L/opt/bluereel/lib -static -static-libgcc -static-libstdc++' \
    --extra-libs='-lstdc++ -lpthread' --extra-version=BlueReel-Development
make -j4
make install
mkdir -p /output/licenses /output/source
cp COPYING.GPLv3 /output/licenses/FFmpeg-GPL-3.0.txt
cp /build/x264/COPYING /output/licenses/x264-GPL-2.0-or-later.txt
cp /build/libvpl/LICENSE /output/licenses/libvpl-MIT.txt
# This project carries its MIT notices in each header, not a LICENSE file.
mkdir -p /output/licenses/nv-codec-headers
cp /build/nv-codec-headers/include/ffnvcodec/*.h /output/licenses/nv-codec-headers/
cp /build/amf/LICENSE.txt /output/licenses/AMF-MIT.txt
cp /usr/share/doc/gcc-mingw-w64-x86-64-posix/copyright /output/licenses/GCC-runtime.txt
cp /usr/share/doc/mingw-w64-common/copyright /output/licenses/mingw-w64.txt
cp /sources/*.tar.gz /output/source/
cp /build/build-ffmpeg.sh /build/mingw-toolchain.cmake /output/source/
cp ffbuild/config.mak /output/source/ffmpeg-config.mak
dpkg-query -W > /output/source/build-tools.txt
x86_64-w64-mingw32-objdump -p /output/bin/ffmpeg.exe | sed -n '/DLL Name:/p' > /output/source/windows-imports.txt
sha256sum /output/bin/ffmpeg.exe /output/bin/ffprobe.exe > /output/source/binaries.sha256
