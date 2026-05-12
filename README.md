# Jury

Weekly file cleanup digest for macOS. Runs via launchd (Sunday 03:00),
scans ~/Documents and ~/Downloads, groups duplicate-variant files into clusters,
auto-deletes unambiguously safe files (.DS_Store, lock files, partial downloads),
and writes a Markdown digest at ~/Documents/cleanup-digest.md with paste-ready
osascript commands for manual Trash actions.

---

## Purpose

Over time, browser downloads accumulate UUID-suffixed, integer-suffixed, and
(n)-suffixed duplicates of the same file. Jury detects these clusters
and presents them for review each Sunday. Safe files (lock files, .DS_Store,
partial downloads) are deleted automatically after an lsof safety check.
Protected document types (.docx, .xlsx, .pdf, etc.) are never auto-deleted.

---

## Install

**Prerequisites:** Python 3 at ~/miniconda3/bin/python3 (already present).
No additional packages required - only the Python standard library.

1. Clone or copy the jury folder to ~/Downloads/jury/

2. Run the installer to register the Sunday digest LaunchAgent and the
   always-active filesystem monitor LaunchAgent:

```bash
bash ~/Downloads/jury/install.sh
```

   The installer is idempotent - re-running it does not double-load the
   plists.

3. Verify the LaunchAgents are loaded:

```bash
launchctl list | grep com.mord58562.jury
```

4. (Optional) Run a dry-run immediately against your real directories:

```bash
JURY_DRY_RUN=1 ~/miniconda3/bin/python3 \
  ~/Downloads/jury/digest.py \
  --docs ~/Documents \
  --downloads ~/Downloads \
  --output /tmp/digest-preview.md
```

   Then inspect /tmp/digest-preview.md before allowing production runs.

---

## Uninstall

1. Unload and remove the LaunchAgents:

```bash
launchctl unload ~/Library/LaunchAgents/com.mord58562.jury.digest.plist
launchctl unload ~/Library/LaunchAgents/com.mord58562.jury.monitor.plist
rm ~/Library/LaunchAgents/com.mord58562.jury.*.plist
```

2. Delete the project folder:

```bash
rm -rf ~/Downloads/jury
```

3. Delete any generated digests:

```bash
rm -f ~/Documents/cleanup-digest.md
```

---

## Dependencies

No external packages. Uses only Python 3 standard library modules:
argparse, base64, collections, dataclasses, datetime, enum, hashlib,
os, pathlib, re, subprocess, sys.

pytest (already installed at ~/miniconda3/bin/pytest) is required only
for running the test suite.

---

## Running tests

```bash
cd ~/Downloads/jury
~/miniconda3/bin/python3 -m pytest tests/ -v
```

All 64 tests should pass. No real ~/Documents or ~/Downloads files are
touched during the test run - all fixtures are zero-byte files in
pytest's tmp_path.

---

## Environment variables

- `JURY_DRY_RUN=1` - suppresses all actual deletions and writes
  the digest to /tmp/ instead of ~/Documents/. Use this for smoke tests
  or when experimenting against real directories.

---

## Protected extensions (never auto-deleted)

.docx .xlsx .pptx .pdf .docm .xlsm .pptm .rtf .pages .numbers .key
.doc .xls .ppt .odt .txt .md .csv

---

## Auto-delete allowlist (explicit, short)

- Lock files: ~$* (with lsof safety gate - only deleted if parent doc is closed)
- .DS_Store
- *.crdownload (Chrome partial downloads)
- *.download (Safari partial downloads)
