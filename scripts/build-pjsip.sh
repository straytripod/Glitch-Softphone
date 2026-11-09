#!/usr/bin/env bash
# Build PJSIP from source and install its pjsua2 Python bindings into the
# project venv. Works on ARM64 and x86_64. See docs/BUILD.md for prerequisites.
#
# Usage (from the project directory, with the source tarball already in ~/src):
#   scripts/build-pjsip.sh
set -euo pipefail

PJ_VERSION="${PJ_VERSION:-2.17}"
SRC_ROOT="${SRC_ROOT:-$HOME/src}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
PJ_DIR="$SRC_ROOT/pjproject-$PJ_VERSION"

if [[ ! -d "$PJ_DIR" ]]; then
    echo "Missing $PJ_DIR. Download and unpack:" >&2
    echo "  https://github.com/pjsip/pjproject/archive/refs/tags/$PJ_VERSION.tar.gz" >&2
    exit 1
fi
if [[ ! -x "$VENV_PY" ]]; then
    echo "Missing venv at $PROJECT_DIR/.venv (see docs/BUILD.md)" >&2
    exit 1
fi

cd "$PJ_DIR"

# Compile-time options. Audio only: no video support compiled in.
cat > pjlib/include/pj/config_site.h <<'EOF'
#define PJMEDIA_HAS_VIDEO 0
EOF

# -fPIC: the static PJSIP libraries get linked into a Python extension module
# (a shared library), which requires position-independent code.
echo "=== configure ==="
./configure \
    CFLAGS="-O2 -fPIC" CXXFLAGS="-O2 -fPIC" \
    --disable-video --disable-sdl --disable-ffmpeg --disable-v4l2 \
    --disable-openh264 --disable-vpx --disable-libyuv --disable-upnp

echo "=== make ==="
make dep
make -j"$(nproc)"

# SWIG generates the Python wrapper; system python3 (same version as the venv)
# builds it into a wheel, which we then install into the venv.
echo "=== python bindings ==="
cd pjsip-apps/src/swig/python
rm -rf dist build
make
make wheel
"$VENV_PY" -m pip install --force-reinstall dist/pjsua2-*.whl

echo "=== check ==="
"$VENV_PY" -c "import pjsua2 as pj; ep = pj.Endpoint(); ep.libCreate(); print('pjsua2 OK, PJSIP', ep.libVersion().full); ep.libDestroy()"
