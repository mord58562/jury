#!/usr/bin/env bash
# build.sh - Compile Jury.swift into a tiny .app bundle.
#
# Output: ./Jury.app  (self-contained, ~200 KB binary + Info.plist)
# Default destination for `install`: $HOME/Applications/Jury.app
#
# Usage:
#   ./build.sh           # build to ./build/Jury.app
#   ./build.sh install   # build then copy to ~/Applications/Jury.app

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/Jury.swift"
PLIST="$SCRIPT_DIR/Info.plist"
BUILD_DIR="$SCRIPT_DIR/build"
APP_DIR="$BUILD_DIR/Jury.app"
BIN_DIR="$APP_DIR/Contents/MacOS"
BIN="$BIN_DIR/jury-menubar"
DEST="$HOME/Applications/Jury.app"

rm -rf "$BUILD_DIR"
mkdir -p "$BIN_DIR"

echo "Compiling Jury.swift ..."
swiftc -O -whole-module-optimization -gnone \
  -framework AppKit -framework Foundation \
  -o "$BIN" "$SRC"

# Strip non-essential symbols from the binary - shrinks file size and
# trims the launch footprint. -x removes all local (non-global) symbols.
strip -x "$BIN"

cp "$PLIST" "$APP_DIR/Contents/Info.plist"

# Ad-hoc code signature so launchd / Gatekeeper accept the local build.
# Must run after strip since strip invalidates any existing signature.
codesign --force --deep --sign - "$APP_DIR"

echo "Built: $APP_DIR"
echo "Binary size: $(stat -f%z "$BIN") bytes"

if [[ "${1:-}" == "install" ]]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$DEST"
  cp -R "$APP_DIR" "$DEST"
  echo "Installed: $DEST"

  # Render and install the menubar LaunchAgent, substituting __HOME__ -> $HOME
  # (mirrors the monitor plist placeholder pattern).
  MENUBAR_PLIST_SRC="$SCRIPT_DIR/com.mord58562.jury.menubar.plist"
  MENUBAR_PLIST_DST="$HOME/Library/LaunchAgents/com.mord58562.jury.menubar.plist"
  if [[ -f "$MENUBAR_PLIST_SRC" ]]; then
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    TMP_MENUBAR="$(mktemp)"
    sed -e "s|__HOME__|$HOME|g" "$MENUBAR_PLIST_SRC" > "$TMP_MENUBAR"
    if [[ -f "$MENUBAR_PLIST_DST" ]]; then
      launchctl unload "$MENUBAR_PLIST_DST" 2>/dev/null || true
    fi
    mv "$TMP_MENUBAR" "$MENUBAR_PLIST_DST"
    launchctl load "$MENUBAR_PLIST_DST"
    echo "Installed menubar LaunchAgent: $MENUBAR_PLIST_DST"
  fi
fi
