#!/usr/bin/env bash
# Build the AppImage. Runs inside the container from Containerfile, started
# by scripts/build-appimage.sh with the project at /src and a download cache
# at /cache. Output: /src/dist/Glitch_Softphone-<version>-<arch>.AppImage
#
# Layout of the AppDir (the folder that becomes the AppImage):
#   AppRun                    starts the app (packaging/appimage/AppRun)
#   usr/python/               a relocatable Python with PySide6, pjsua2 and our app
#   usr/lib/                  libopus, OpenSSL (found by the PJSIP module via RPATH)
#   *.desktop, *.png          menu entry and icon
set -euo pipefail

ARCH="$(uname -m)"                      # x86_64 or aarch64: built natively, no cross-compiling
DESKTOP_ID="io.github.straytripod.GlitchSoftphone"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' /src/app/__init__.py)"

# Pinned downloads, checked against these SHA-256 sums so a changed or
# tampered file stops the build. To update: new version + new sum together.
PY_RELEASE="20261013"
PY_VERSION="3.14.8"
declare -A PY_SHA=(
    [x86_64]=322271a761140ffb3f522431048f701831e412c8f64770103d2b881ee2bbaed0
    [aarch64]=6c3cfb175acca2748d5a9e42208c523bfbc1099ddb928615b5f328ba7c106e83
)
PJ_VERSION="2.17"
PJ_SHA=065fe06c06788d97c35f563796d59f00ce52fe9558a52d7b490a042a966facce
TOOL_VERSION="1.9.1"
declare -A TOOL_SHA=(
    [x86_64]=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
    [aarch64]=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158
)
RUNTIME_RELEASE="20251108"
declare -A RUNTIME_SHA=(
    [x86_64]=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
    [aarch64]=00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444
)

fetch() {   # fetch <url> <file in /cache> <sha256>
    local url="$1" file="/cache/$2" sum="$3"
    if [[ ! -f "$file" ]]; then
        echo "--- downloading $url"
        curl -fL --retry 3 -o "$file.part" "$url"
        mv "$file.part" "$file"
    fi
    echo "$sum  $file" | sha256sum -c --quiet - || { rm -f "$file"; echo "Checksum mismatch: $file" >&2; exit 1; }
}

fetch "https://github.com/astral-sh/python-build-standalone/releases/download/$PY_RELEASE/cpython-$PY_VERSION+$PY_RELEASE-$ARCH-unknown-linux-gnu-install_only.tar.gz" \
      "python-$PY_VERSION-$ARCH.tar.gz" "${PY_SHA[$ARCH]}"
fetch "https://github.com/pjsip/pjproject/archive/refs/tags/$PJ_VERSION.tar.gz" \
      "pjproject-$PJ_VERSION.tar.gz" "$PJ_SHA"
fetch "https://github.com/AppImage/appimagetool/releases/download/$TOOL_VERSION/appimagetool-$ARCH.AppImage" \
      "appimagetool-$TOOL_VERSION-$ARCH.AppImage" "${TOOL_SHA[$ARCH]}"
fetch "https://github.com/AppImage/type2-runtime/releases/download/$RUNTIME_RELEASE/runtime-$ARCH" \
      "runtime-$RUNTIME_RELEASE-$ARCH" "${RUNTIME_SHA[$ARCH]}"

WORK=/tmp/build
APPDIR="$WORK/AppDir"
PY="$APPDIR/usr/python/bin/python3"
rm -rf "$WORK"
mkdir -p "$APPDIR/usr/lib"

echo "=== Python $PY_VERSION ==="
tar -xzf "/cache/python-$PY_VERSION-$ARCH.tar.gz" -C "$APPDIR/usr"   # unpacks to usr/python
# Not needed at run time; saves ~60 MB.
rm -rf "$APPDIR"/usr/python/lib/python3.*/{test,idlelib,tkinter,turtledemo,ensurepip} \
       "$APPDIR"/usr/python/lib/{libtcl*,libtk*,tcl*,tk*,itcl*,thread*} \
       "$APPDIR"/usr/python/include
# Keep the headers for the PJSIP build in a separate copy.
mkdir -p "$WORK/python-build"
tar -xzf "/cache/python-$PY_VERSION-$ARCH.tar.gz" -C "$WORK/python-build"

echo "=== PySide6 ==="
"$PY" -m pip install --no-cache-dir --no-warn-script-location -r /src/requirements.txt

echo "=== PJSIP $PJ_VERSION ==="
tar -xzf "/cache/pjproject-$PJ_VERSION.tar.gz" -C "$WORK"
cd "$WORK/pjproject-$PJ_VERSION"
echo '#define PJMEDIA_HAS_VIDEO 0' > pjlib/include/pj/config_site.h
# Same options as scripts/build-pjsip.sh (audio only).
./configure CFLAGS="-O2 -fPIC" CXXFLAGS="-O2 -fPIC" \
    --disable-video --disable-sdl --disable-ffmpeg --disable-v4l2 \
    --disable-openh264 --disable-vpx --disable-libyuv --disable-upnp > /dev/null
make dep > /dev/null
make -j"$(nproc)" > /dev/null
# The bindings build with whatever "python3" is first on PATH: a throwaway
# venv from the full Python copy, with setuptools, so the AppDir stays clean.
"$WORK/python-build/python/bin/python3" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --no-cache-dir -q setuptools wheel
cd pjsip-apps/src/swig/python
# python-build-standalone was compiled with clang, and setuptools reuses its
# compiler settings; use the container's GCC (it built PJSIP itself) instead.
export CC=gcc CXX=g++ LDSHARED="gcc -shared" LDCXXSHARED="g++ -shared"
PATH="$WORK/venv/bin:$PATH" make > /dev/null
PATH="$WORK/venv/bin:$PATH" make wheel > /dev/null
unset CC CXX LDSHARED LDCXXSHARED
"$PY" -m pip install --no-cache-dir --no-warn-script-location dist/pjsua2-*.whl

echo "=== bundled libraries ==="
# The PJSIP module needs libopus and OpenSSL, which not every system has in
# the same version. ALSA (libasound) and the C/C++ runtime always come from
# the host: ALSA's plugins (PipeWire's included) live there.
for lib in libopus.so.0 libssl.so.3 libcrypto.so.3; do
    cp -L "/usr/lib/$ARCH-linux-gnu/$lib" "$APPDIR/usr/lib/"
done
PJ_MODULE="$(ls "$APPDIR"/usr/python/lib/python3.*/site-packages/_pjsua2*.so)"
# RPATH instead of LD_LIBRARY_PATH: only this module looks there, so the
# programs we start (pw-dump, gdbus, ffmpeg) keep using the host's libraries.
patchelf --set-rpath '$ORIGIN/../../../../lib' "$PJ_MODULE"

echo "=== app ==="
SITE="$(dirname "$PJ_MODULE")"
cp -r /src/app "$SITE/app"
find "$SITE/app" -name __pycache__ -prune -exec rm -rf {} +
"$PY" -m compileall -q "$SITE/app"

cp /src/packaging/appimage/AppRun "$APPDIR/AppRun"
cp "/src/packaging/$DESKTOP_ID.desktop" "$APPDIR/"
cp "/src/packaging/$DESKTOP_ID.png" "$APPDIR/"
mkdir -p "$APPDIR/usr/share/metainfo" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "/src/packaging/$DESKTOP_ID.metainfo.xml" "$APPDIR/usr/share/metainfo/$DESKTOP_ID.appdata.xml"
cp "/src/packaging/$DESKTOP_ID.desktop" "$APPDIR/usr/share/applications/"
cp "/src/packaging/$DESKTOP_ID.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"

echo "=== smoke test ==="
QT_QPA_PLATFORM=offscreen "$PY" -I -c "
import pjsua2 as pj
ep = pj.Endpoint(); ep.libCreate(); print('pjsua2', ep.libVersion().full); ep.libDestroy()
from PySide6.QtWidgets import QApplication; a = QApplication([])
import app.ui.main_window, app.sip.engine
from app.sip.certs import system_ca_file
print('Qt + app modules import OK; CA bundle:', system_ca_file() or '(none)')
"

echo "=== AppImage ==="
mkdir -p /src/dist
OUT="/src/dist/Glitch_Softphone-$VERSION-$ARCH.AppImage"
TOOL="/cache/appimagetool-$TOOL_VERSION-$ARCH.AppImage"
chmod +x "$TOOL"
# No FUSE in the container: let appimagetool unpack itself and run.
# Build under a new name, then rename: replacing works even while the old
# AppImage is running (writing over it directly fails with "text file busy").
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$TOOL" --no-appstream \
    --runtime-file "/cache/runtime-$RUNTIME_RELEASE-$ARCH" "$APPDIR" "$OUT.new"
mv -f "$OUT.new" "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
