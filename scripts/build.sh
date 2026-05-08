#!/bin/bash
# Build a self-contained ChainProxy.app by stamping the source .py files into
# its Contents/Resources/. The skeleton (Info.plist, launcher, icns) lives in
# the repo at ChainProxy.app/; this script just keeps the .py copies in sync.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ChainProxy.app"
RES="$APP/Contents/Resources"
GEODATA_SNAPSHOT="$ROOT/scripts/geodata"

if [ ! -d "$APP" ]; then
  echo "✗ $APP not found" >&2
  exit 1
fi

cp "$ROOT/chainproxy_qt.py"   "$RES/chainproxy_qt.py"
cp "$ROOT/chainproxy_core.py" "$RES/chainproxy_core.py"
# core/ is the platform-dispatching backend package — chainproxy_core.py is
# only a `from core import *` shim, so the package itself must ship in the
# bundle. Without this the launcher dies with ModuleNotFoundError: 'core'.
rm -rf "$RES/core"
cp -R "$ROOT/core" "$RES/core"
find "$RES/core" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
chmod +x "$APP/Contents/MacOS/ChainProxy"

# Bundle a GeoIP/GeoSite snapshot so cold-start mihomo doesn't deadlock
# downloading from GitHub on offline / GFW boxes. Files come from
# scripts/geodata/ — fetch on demand from the latest Loyalsoldier release.
mkdir -p "$GEODATA_SNAPSHOT"
fetch_geodata() {
  local name="$1" url="$2"
  local dest="$GEODATA_SNAPSHOT/$name"
  if [ -s "$dest" ] && [ "$(stat -f %z "$dest" 2>/dev/null || stat -c %s "$dest")" -gt 1024 ]; then
    return 0
  fi
  echo "  fetching $name..."
  if curl -fsSL --retry 2 --max-time 60 -o "$dest.tmp" "$url"; then
    mv "$dest.tmp" "$dest"
  else
    echo "  ✗ $name download failed; cold-start mihomo on GFW boxes will fail" >&2
    rm -f "$dest.tmp"
  fi
}
fetch_geodata "Country.mmdb" \
  "https://github.com/Loyalsoldier/geoip/releases/latest/download/Country.mmdb"
fetch_geodata "geoip.dat" \
  "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
fetch_geodata "geosite.dat" \
  "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"

mkdir -p "$RES/geodata"
for f in Country.mmdb geoip.dat geosite.dat; do
  if [ -s "$GEODATA_SNAPSHOT/$f" ]; then
    cp "$GEODATA_SNAPSHOT/$f" "$RES/geodata/$f"
  fi
done

# Strip any stale quarantine flag (otherwise Gatekeeper nags on first launch).
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "✓ Built $APP"
