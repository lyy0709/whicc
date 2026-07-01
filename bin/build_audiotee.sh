#!/bin/bash
# Build audiotee (system audio capture, Core Audio process tap) into ./bin/audiotee.
# Only needed for --audio-source system; --audio-source mic uses sounddevice
# (pure Python, no binary). Requires macOS 14.2+ and Swift 5.9+.
#
# Why ./bin/audiotee (not /tmp/...): macOS periodically cleans /tmp, so a
# binary there would silently disappear. Project-local ./bin/ persists
# across reboots.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$HERE/.vendor/audiotee"
BIN_DIR="$HERE/bin"
# Pin to a verified commit (supply-chain hygiene: don't build a third-party repo's
# HEAD directly). To upgrade, set AUDIOTEE_REF and re-verify.
AUDIOTEE_REF="${AUDIOTEE_REF:-56ac954369a09318e46b88a6eec33c2d2b0d32a3}"

mkdir -p "$BIN_DIR"

if [ ! -d "$VENDOR/.git" ]; then
    echo "==> Cloning audiotee source"
    git clone https://github.com/makeusabrew/audiotee.git "$VENDOR"
fi
echo "==> Checking out pinned revision $AUDIOTEE_REF"
git -C "$VENDOR" rev-parse --quiet --verify "$AUDIOTEE_REF^{commit}" >/dev/null \
    || git -C "$VENDOR" fetch origin
git -C "$VENDOR" checkout --quiet "$AUDIOTEE_REF"

echo "==> swift build -c release"
cd "$VENDOR"
swift build -c release

BIN="$(swift build -c release --show-bin-path)/audiotee"
cp "$BIN" "$BIN_DIR/audiotee"
echo "==> Done: $BIN_DIR/audiotee"
echo
echo "On first use of --audio-source system, macOS may prompt for"
echo "'Screen & System Audio Recording' permission for your terminal app."
echo "If no prompt appears, grant it manually:"
echo "  System Settings > Privacy & Security > Screen & System Audio Recording"
echo "On macOS 15+, scroll to the 'System Audio Recording Only' sub-section"
echo "(NOT the top one), add your terminal, fully quit and restart."
