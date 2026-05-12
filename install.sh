#!/usr/bin/env bash
# install.sh - Install Jury's Sunday digest + always-active monitor.
#
# Idempotent: re-running does not double-load plists and leaves existing
# state files alone.
#
# Components installed:
#   1. ~/Library/LaunchAgents/com.mord58562.jury.digest.plist - runs
#      digest.py every Sunday at 03:00.
#   2. ~/Library/LaunchAgents/com.mord58562.jury.monitor.plist - WatchPaths
#      on ~/Downloads and ~/Documents. Each filesystem change spawns
#      monitor.py (throttled to once per 30 seconds).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIGEST_PY="$SCRIPT_DIR/digest.py"
MONITOR_PY="$SCRIPT_DIR/monitor.py"
PLIST_SRC="$SCRIPT_DIR/com.mord58562.jury.monitor.plist"

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DST="$LAUNCH_AGENTS_DIR/com.mord58562.jury.monitor.plist"
SUNDAY_PLIST_DST="$LAUNCH_AGENTS_DIR/com.mord58562.jury.digest.plist"
LOG_DIR="$HOME/Library/Logs"
STDOUT_LOG="$LOG_DIR/jury-monitor.stdout.log"
STDERR_LOG="$LOG_DIR/jury-monitor.stderr.log"
DIGEST_STDOUT_LOG="$LOG_DIR/jury-digest.stdout.log"
DIGEST_STDERR_LOG="$LOG_DIR/jury-digest.stderr.log"

# 1. Sanity checks
for f in "$DIGEST_PY" "$MONITOR_PY" "$PLIST_SRC"; do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: $f not found. Run install.sh from the jury directory." >&2
    exit 1
  fi
done

if [[ -x "$HOME/miniconda3/bin/python3" ]]; then
  PYTHON="$HOME/miniconda3/bin/python3"
elif command -v python3 &>/dev/null; then
  PYTHON="$(command -v python3)"
else
  echo "ERROR: python3 not found. Install miniconda3 or system Python 3." >&2
  exit 1
fi
echo "Using Python: $PYTHON"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR" \
  "$HOME/Library/Application Support/jury"

# 2. Install / refresh the Sunday digest LaunchAgent (runs at 03:00 Sunday).
TMP_SUNDAY="$(mktemp)"
cat > "$TMP_SUNDAY" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.mord58562.jury.digest</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$DIGEST_PY</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>0</integer>
        <key>Hour</key><integer>3</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key><string>$DIGEST_STDOUT_LOG</string>
    <key>StandardErrorPath</key><string>$DIGEST_STDERR_LOG</string>
</dict>
</plist>
EOF
if [[ -f "$SUNDAY_PLIST_DST" ]] && cmp -s "$TMP_SUNDAY" "$SUNDAY_PLIST_DST"; then
  echo "Sunday digest plist already up-to-date at $SUNDAY_PLIST_DST."
  rm -f "$TMP_SUNDAY"
else
  if [[ -f "$SUNDAY_PLIST_DST" ]]; then
    launchctl unload "$SUNDAY_PLIST_DST" 2>/dev/null || true
  fi
  mv "$TMP_SUNDAY" "$SUNDAY_PLIST_DST"
  launchctl load "$SUNDAY_PLIST_DST"
  echo "Installed and loaded Sunday digest plist at $SUNDAY_PLIST_DST."
fi

# 3. Render the monitor plist with absolute paths
TMP_PLIST="$(mktemp)"
sed \
  -e "s|__PYTHON__|$PYTHON|g" \
  -e "s|__MONITOR__|$MONITOR_PY|g" \
  -e "s|__DOWNLOADS__|$HOME/Downloads|g" \
  -e "s|__DOCUMENTS__|$HOME/Documents|g" \
  -e "s|__LOG_OUT__|$STDOUT_LOG|g" \
  -e "s|__LOG_ERR__|$STDERR_LOG|g" \
  "$PLIST_SRC" > "$TMP_PLIST"

# 4. Install / refresh the monitor LaunchAgent (only reload if changed)
if [[ -f "$PLIST_DST" ]] && cmp -s "$TMP_PLIST" "$PLIST_DST"; then
  echo "Monitor plist already up-to-date at $PLIST_DST."
  rm -f "$TMP_PLIST"
else
  if [[ -f "$PLIST_DST" ]]; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
  fi
  mv "$TMP_PLIST" "$PLIST_DST"
  launchctl load "$PLIST_DST"
  echo "Installed and loaded monitor plist at $PLIST_DST."
fi

# 5. Verify the monitor is registered
if launchctl list | grep -q "com.mord58562.jury.monitor"; then
  echo "Monitor registered with launchd."
else
  echo "WARNING: monitor not visible in launchctl list. Try: launchctl load $PLIST_DST"
fi

echo
echo "Install complete."
echo "  Sunday digest:  $SUNDAY_PLIST_DST"
echo "  Monitor plist:  $PLIST_DST"
echo "  Logs:           $STDOUT_LOG  /  $STDERR_LOG"
echo "  State / quarantine: $HOME/Library/Application Support/jury/"
