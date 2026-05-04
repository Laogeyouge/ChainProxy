#!/bin/bash
# Bundle ChainProxy.app into a drag-to-/Applications style DMG.
# Output: dist/ChainProxy-<version>.dmg
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ChainProxy.app"
DIST="$ROOT/dist"
VERSION="${1:-1.0.0}"
DMG="$DIST/ChainProxy-$VERSION.dmg"

bash "$ROOT/scripts/build.sh"

mkdir -p "$DIST"
rm -f "$DMG"

# Stage: copy of the app + an Applications symlink (gives the classic
# drag-to-install layout). Use a fresh tmpdir so leftovers don't sneak in.
STAGE=$(mktemp -d -t chainproxy-dmg)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# UDZO = compressed read-only — what mainstream apps ship.
hdiutil create \
  -volname "ChainProxy" \
  -srcfolder "$STAGE" \
  -ov -format UDZO \
  "$DMG" >/dev/null

rm -rf "$STAGE"

echo "✓ DMG written: $DMG"
ls -lh "$DMG"
