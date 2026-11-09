#!/usr/bin/env bash
# Build the AppImage for this machine's architecture (x86_64 or aarch64)
# inside an Ubuntu 24.04 container. Needs podman.
#
# Usage (from anywhere):  scripts/build-appimage.sh
# Output:                 dist/Glitch_Softphone-<version>-<arch>.AppImage
# Downloads are cached in the podman volume "glitch-appimage-cache".
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="glitch-softphone-appimage-builder"

if ! command -v podman > /dev/null; then
    echo "podman is needed: sudo apt install podman  (Fedora: sudo dnf install podman)" >&2
    exit 1
fi

podman build -t "$IMAGE" "$PROJECT_DIR/packaging/appimage"
# :z lets the container read the project on SELinux systems (Fedora).
podman run --rm \
    -v "$PROJECT_DIR":/src:z \
    -v glitch-appimage-cache:/cache \
    "$IMAGE" bash /src/packaging/appimage/build-inside.sh
