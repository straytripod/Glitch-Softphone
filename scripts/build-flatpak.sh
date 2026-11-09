#!/usr/bin/env bash
# Build the Flatpak for this machine's architecture (x86_64 or aarch64)
# from packaging/flatpak/. Needs flatpak-builder and the
# freedesktop 25.08 runtime and SDK (see docs/BUILD.md).
#
# Usage (from anywhere):  scripts/build-flatpak.sh
# Output:                 dist/Glitch_Softphone-<version>-<arch>.flatpak
# Downloads and build steps are cached in ~/.cache/glitch-softphone-flatpak,
# so a rebuild after changing only the app takes seconds.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="io.github.straytripod.GlitchSoftphone"
MANIFEST="$PROJECT_DIR/packaging/flatpak/$APP_ID.yml"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/glitch-softphone-flatpak"
ARCH="$(uname -m)"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$PROJECT_DIR/app/__init__.py")"
OUT="$PROJECT_DIR/dist/Glitch_Softphone-$VERSION-$ARCH.flatpak"

if ! command -v flatpak-builder > /dev/null; then
    echo "flatpak-builder is needed: sudo apt install flatpak-builder  (Fedora: sudo dnf install flatpak-builder)" >&2
    exit 1
fi

mkdir -p "$CACHE" "$PROJECT_DIR/dist"
# --user: use the runtime installed with "flatpak install --user"
flatpak-builder --user --force-clean --state-dir="$CACHE/state" \
    --repo="$CACHE/repo" "$CACHE/build" "$MANIFEST"
# One file people can install with:  flatpak install --user <file>
flatpak build-bundle "$CACHE/repo" "$OUT" "$APP_ID" \
    --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
