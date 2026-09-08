# Jury

Quarantine-based file cleanup for macOS. Jury watches `~/Documents` and
`~/Downloads`, moves obviously disposable files into a dated quarantine
folder where they stay recoverable for 30 days, groups duplicate-variant
files into clusters, and writes a weekly Markdown digest with paste-ready
commands for the decisions it will not make for you.

## Nothing is deleted

Jury does not delete files. When a file qualifies for automatic action it is
**moved**, not removed, to:

```
~/Library/Application Support/jury/quarantine/<YYYY-MM-DD>/<hash8>/<filename>
```

`<hash8>` is the first eight characters of a SHA-256 of the original path, so
same-named files from different folders never collide. Next to each
quarantined file Jury writes a `<filename>.jury-restore.json` sidecar
recording the original path, the quarantine date and the original mtime.

Entries stay in quarantine for 30 days. Each weekly digest lists them with a
one-line restore command, and flags anything within 7 days of expiry. After
30 days the file is final-trashed through Finder, which puts it in the Trash
rather than unlinking it. It is still recoverable until the Trash is emptied.

## What qualifies for quarantine

Four patterns, and nothing else. Extending the list requires a code change.

- `~$*` Office lock files
- `.DS_Store`
- `*.crdownload` (Chrome partial downloads)
- `*.download` (Safari partial downloads)

## Protected extensions, and the one carve-out

These 18 extensions are a hard gate. A file with one of them is never
actioned automatically:

`.docx .xlsx .pptx .pdf .docm .xlsm .pptm .rtf .pages .numbers .key .doc
.xls .ppt .odt .txt .md .csv`

**The carve-out:** the `~$*` lock-file pattern is checked *before* the
extension gate. `~$Report.docx` is a lock file, so it is eligible for
quarantine even though `.docx` is protected. This is deliberate: a lock file
is transient scratch state, not the document it shadows. Before Jury touches
one it runs `lsof` on it, and skips it if any process has it open, if `lsof`
times out, or if `lsof` is not installed. `Report.docx` itself is never
touched.

## Safety gates

Every candidate passes all of these before it moves:

- **24-hour cooling window.** Anything whose mtime or atime is within the
  last 24 hours is skipped. A failed `stat` counts as too recent.
- **`lsof` gate.** Anything a process currently holds open is skipped. A
  timeout or a missing `lsof` binary also counts as open.
- **Daily action ceiling.** 200 actions per day, tracked in `state.json`.
  Once reached, everything else is skipped until tomorrow.
- **Action log.** Every move and every final-trash appends a tab-delimited
  line to `~/Library/Application Support/jury/undo.log`.

## The two schedules

**Always-on monitor.** A launchd agent with `WatchPaths` on `~/Downloads` and
`~/Documents`, throttled to once per 30 seconds. Each filesystem change wakes
a one-shot `monitor.py` process that scans, quarantines what qualifies, ages
out expired quarantine, and exits. Nothing stays resident, so idle memory use
is zero.

**Sunday digest.** A second agent runs `digest.py` at 03:00 every Sunday. It
performs the same scan through the same gates, then writes the digest.

## The digest

Written to `~/Documents/cleanup-digest.md`. **It is overwritten in place on
every run, without prompting.** Do not edit it or keep notes in it.

Eight sections, of which two are conditional:

1. **Filename clusters.** Files whose names differ only by a UUID segment, a
   `(N)` suffix or a `-N` suffix, grouped and ranked by reclaimable bytes
   (cluster total minus the largest member). Top 50 shown. Each member gets a
   paste-ready trash command. Where members are provably byte-identical, the
   section nominates a keeper and gives one bulk-trash command for the rest.
2. **Stale downloads.** See below.
3. **Quarantine status.** Current entries with days remaining, expiring
   entries first, each with a `mv` restore command.
4. **Monitor action tally.** Counts of quarantined, skipped-cooling,
   skipped-open, skipped-ceiling, missing and failed.
5. **iCloud status.** Parsed from `brctl status`.
6. **Time Machine status.** Parsed from `tmutil latestbackup` and
   `tmutil status`.
7. **EOFY invoice tally.** May and June only. See known limitations.
8. **Portfolio status.** Only when a log check fails. See known limitations.

Nothing in the digest acts on its own. Sections 1 and 2 are lists of commands
for you to run or ignore.

### Stale-download detection

Top-level files in `~/Downloads` untouched for 90 days or more, excluding
cluster members, quarantine candidates and dotfiles. For each one Jury asks
Spotlight for `kMDItemLastUsedDate`, so the digest can tell you the file has
never been opened, which is usually a sharper question than how old it is.
Each entry gets reveal, Quick Look preview and trash commands.

### Byte-identical detection

Within a name cluster, Jury groups files by size first, then hashes only
within a size group. The hash covers the first 64KB, the last 64KB and the
file size. Files at or under 128KB are read in full. Two files share a hash
only if size, head and tail all match, which is a strong identity signal
without reading multi-gigabyte files end to end.

## The menubar app

`swift-app/` builds `Jury.app`, a menubar-only AppKit app (no Dock icon, no
main menu). Clicking the menubar icon opens a panel showing the current
quarantine count, how many entries are expiring soon, the expiring entries
themselves, when the monitor last ran, and a warning banner once today's
action count reaches 180.

Every element in the panel is a click target: the cards open the quarantine
folder, the rows reveal a file in Finder, the health row opens the monitor
log, and the header buttons open the digest, open `undo.log`, or quit.

The app is a read-only viewer. It cannot restore, trash or delete anything,
and it cannot run the Python side. It reads its snapshot once each time you
open the panel and does not refresh while open. Because there is no main
menu, Cmd-Q does nothing; quit with the power button in the panel.

If the Python side has never run, the app still launches and shows zeroes and
a last run of "never". Clicking a card in that state creates the empty folder
or file it was going to open.

## Dependencies

| Requirement | Why | Install |
| --- | --- | --- |
| macOS 13 or later | `LSMinimumSystemVersion` of the menubar app | Ships with the OS |
| Python 3 | Everything except the menubar app | `brew install python` |
| Xcode Command Line Tools | `swiftc`, `strip`, `codesign` for the menubar app | `xcode-select --install` |
| pytest | Test suite only | `python3 -m pip install pytest` |

No third-party Python packages. Standard library only: `argparse`,
`collections`, `dataclasses`, `datetime`, `enum`, `fcntl`, `hashlib`, `json`,
`os`, `pathlib`, `re`, `shutil`, `subprocess`, `sys`, `typing`.

`lsof`, `mdls`, `osascript`, `tmutil` and `brctl` are all part of macOS.

## Install

Clone the repo wherever you like. `install.sh` resolves paths relative to
itself, so the location does not matter.

```bash
git clone https://github.com/mord58562/jury.git
cd jury
```

**1. Preview before installing anything.** This writes to `/tmp` and moves no
files:

```bash
JURY_DRY_RUN=1 python3 digest.py \
  --docs ~/Documents \
  --downloads ~/Downloads \
  --output /tmp/digest-preview.md
open /tmp/digest-preview.md
```

**2. Install the two Python agents.** This registers the Sunday digest and
the always-on monitor. It is idempotent.

```bash
bash install.sh
```

`install.sh` prefers `~/miniconda3/bin/python3` if it exists and falls back to
whatever `python3` resolves to. The chosen interpreter is baked into both
plists as an absolute path.

**3. Build and install the menubar app.** This is a separate script and is
not run by `install.sh`. It compiles `Jury.swift`, copies the bundle to
`~/Applications/Jury.app`, and installs a third launchd agent
(`com.mord58562.jury.menubar`, `RunAtLoad` with `KeepAlive` on non-zero
exit).

```bash
bash swift-app/build.sh install
open ~/Applications/Jury.app
```

**4. Verify all three agents are loaded:**

```bash
launchctl list | grep com.mord58562.jury
```

You should see `com.mord58562.jury.digest`, `com.mord58562.jury.monitor` and
`com.mord58562.jury.menubar`.

## Permissions

Jury touches paths macOS guards, so expect consent prompts:

- **Files and Folders.** The monitor's `WatchPaths` cover `~/Documents` and
  `~/Downloads`, and both are TCC-protected. The menubar app also checks
  `~/Documents` for the digest.
- **Automation.** Final-trashing and the digest's trash commands drive Finder
  through `osascript`, which triggers an Apple Events consent prompt for the
  process making the call.
- **`lsof`.** Listing open files on paths in protected folders can prompt as
  well.

A launchd agent running in the background can have these prompts suppressed
rather than shown, in which case the access silently fails and Jury degrades
quietly: files stay put and the tally shows skips. If the monitor appears to
do nothing, open **System Settings > Privacy & Security** and check Files and
Folders, Full Disk Access, and Automation for the Python interpreter baked
into the plists and for `Jury.app`. Running the dry-run command above once in
Terminal will surface the prompts interactively.

## Uninstall

**Check quarantine first.** Removing the application-support directory
destroys anything still quarantined, including files you would have restored.

```bash
open ~/Library/Application\ Support/jury/quarantine
```

Restore or trash what is in there, then:

```bash
launchctl unload ~/Library/LaunchAgents/com.mord58562.jury.digest.plist
launchctl unload ~/Library/LaunchAgents/com.mord58562.jury.monitor.plist
launchctl unload ~/Library/LaunchAgents/com.mord58562.jury.menubar.plist
rm -f ~/Library/LaunchAgents/com.mord58562.jury.*.plist

rm -rf ~/Applications/Jury.app
rm -rf ~/Library/Application\ Support/jury
rm -f ~/Library/Logs/jury-monitor.*.log
rm -f ~/Library/Logs/jury-digest.*.log
rm -f ~/Library/Logs/jury-menubar.*.log
rm -f ~/Documents/cleanup-digest.md
```

Then delete the repo clone.

## Environment variables

- `JURY_DRY_RUN=1` - no files are moved, and the digest is written to
  `/tmp/jury-digest.md` instead of `~/Documents/cleanup-digest.md`. The
  action tally reports what would have moved.

## Running tests

```bash
python3 -m pytest -q
```

98 tests. The suite uses only zero-byte fixtures under pytest's `tmp_path`;
no real file in `~/Documents` or `~/Downloads` is read or touched.

Two tests currently fail: `test_monitor_run.py::TestRunOnce::
test_quarantines_safe_candidates_only` and `test_quarantine.py::
TestListQuarantine::test_returns_entries_with_sidecar_data`. Both hardcode
dates in May 2026 while comparing against real filesystem timestamps and the
real current date, so they pass only when run in that window. The failures
are a test-fixture problem, not a defect in the modules under test.

## Known limitations

- **The EOFY probe is Australian.** It fires only in May and June, which is
  the end of the Australian financial year. In any other jurisdiction section
  7 appears at the wrong time of year and means nothing.
- **The portfolio probe is dead.** `probes.py` reads
  `~/portfolio/update.log`, which belongs to a project that no longer exists.
  The probe returns `None` on every run and section 8 never renders. It
  should be removed.
- **The digest overwrites without asking.** See above.
- **The monitor never sees deep files.** Only the top level of the two
  directories is scanned, by design.

## What's new in 1.0.0

- Quarantine replaces deletion. Everything actioned automatically is moved to
  a dated folder with a restore sidecar and kept for 30 days.
- The always-on `WatchPaths` monitor. Cleanup no longer waits for Sunday, and
  the weekday and Sunday paths share identical safety gates.
- The menubar app: quarantine counts, expiring entries, monitor health and
  one-click access to the quarantine folder, the logs and the digest.
- Byte-identical detection inside name clusters, with a nominated keeper and
  a single bulk-trash command per group.
- Stale-download detection at 90 days, with Spotlight last-opened data.
- A 200-action daily ceiling, a 24-hour cooling window and an `lsof` gate on
  every candidate.
- An append-only `undo.log` covering every quarantine and final-trash.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) - what each module does and
  how the two execution paths fit together.
- [`docs/menubar-design.md`](docs/menubar-design.md) - design notes for the
  AppKit menubar app.

## License

MIT. See [`LICENSE`](LICENSE).
