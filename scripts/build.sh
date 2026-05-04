#!/bin/bash
# Build a self-contained ChainProxy.app by stamping the source .py files into
# its Contents/Resources/. The skeleton (Info.plist, launcher, icns) lives in
# the repo at ChainProxy.app/; this script just keeps the .py copies in sync.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ChainProxy.app"
RES="$APP/Contents/Resources"

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

# Strip any stale quarantine flag (otherwise Gatekeeper nags on first launch).
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "✓ Built $APP"
