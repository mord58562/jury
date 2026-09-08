# Jury architecture

What each module does and how the pieces fit together.

## Two execution paths

Jury runs as two independent launchd agents plus an optional menubar app.

**1. The always-on monitor.** `com.mord58562.jury.monitor` has `WatchPaths`
set to `~/Downloads` and `~/Documents` and a `ThrottleInterval` of 30
seconds. launchd spawns `monitor.py` when either directory changes, at most
once per 30 seconds. Each spawn is a one-shot process: take a lock, scan,
quarantine what qualifies, age out expired quarantine, exit 0. Idle memory
use is zero because nothing stays resident. `RunAtLoad` and `KeepAlive` are
both false.

**2. The Sunday digest.** `com.mord58562.jury.digest` runs `digest.py` every
Sunday at 03:00 via `StartCalendarInterval`. It performs the same scan and
the same quarantine pipeline as the monitor, then runs the system probes and
writes a Markdown report to `~/Documents/cleanup-digest.md`. It always exits
0 so launchd never throttles it.

**3. The menubar app.** `com.mord58562.jury.menubar` runs `Jury.app` with
`RunAtLoad` true and `KeepAlive` on non-zero exit. It is a read-only viewer
over the state the other two paths write.

The two Python paths share every code path below the entry point, so a
weekday quarantine and a Sunday quarantine pass through identical gates.

## Modules

### `scanner.py`

Walks the top level of `~/Documents` and `~/Downloads` only. Subdirectories
are not descended into: nested project trees, app bundles and conda
environments are deliberately out of scope.

`cluster_key()` derives a grouping key from a filename by decoding `+` to a
space, stripping a trailing UUID segment (`-8-4-4-4-12` hex), stripping a
trailing ` (N)`, stripping a trailing `-N` where N is at most two digits,
then lowercasing. The extension is not part of the key, so `report.docx` and
`report.pdf` land in the same cluster. The two-digit cap on the integer
suffix stops date segments such as `-2026` being eaten.

`find_clusters()` returns key groups with two or more members. Files matching
an auto-delete pattern are excluded so they are never reported twice.
`find_cross_dir_clusters()` matches an `old - <name>` file in one directory
against a plain `<name>` file in the other, in both directions.

`Cluster.reclaimable_bytes` is the cluster total minus its largest member,
which is the space freed if every copy but the biggest is removed.

### `classifier.py`

Decides one of `AUTO_DELETE`, `SURFACE_ONLY` or `IGNORE` per file.

The auto-delete allowlist is four regexes and nothing else: `~$*` Office lock
files, `.DS_Store`, `*.crdownload`, `*.download`. Extending it requires a
code change.

`PROTECTED_EXTENSIONS` is a hard gate covering 18 document extensions. A file
with one of those extensions never receives `AUTO_DELETE`.

The order of the two checks matters and is the one carve-out in the design.
The lock-file pattern is tested before the extension gate, because a lock
file is transient scratch state rather than the document it shadows.
`~$Report.docx` is therefore eligible for quarantine even though `.docx` is
protected. Before it is marked `AUTO_DELETE` it must pass an `lsof` check on
itself; if `lsof` returns a hit, or is missing, or times out, the file is
downgraded to `SURFACE_ONLY`.

### `hashing.py`

Promotes name-based clusters into provable byte-identical subgroups.
`partial_hash()` reads the first 64KB and the last 64KB of a file and mixes
the file size into a SHA-256 digest, returning the first 16 hex characters.
Files at or under 128KB are read in full so head and tail cannot overlap.
Size is part of the digest so different-sized files never collide.

`byte_identical_subgroups()` buckets a cluster by size first and only hashes
within a size bucket, so files that cannot be identical are never read.

### `stale.py`

Finds top-level files in `~/Downloads` older than `STALE_THRESHOLD_DAYS`
(90) by mtime, excluding cluster members, auto-delete candidates and
dotfiles. For each hit it queries Spotlight with
`mdls -name kMDItemLastUsedDate`, so the digest can say whether the file has
ever been opened. The Spotlight lookup is injectable for testing.

### `probes.py`

Best-effort subprocess wrappers. Every probe returns an "unavailable" value
rather than raising, so a missing tool degrades the digest instead of
failing the run.

- `probe_time_machine()` runs `tmutil latestbackup` and `tmutil status`.
- `probe_icloud()` runs `brctl status` and looks for `caught-up`,
  `needs-upload` and `CKErrorDomain:25` (quota exceeded).
- `probe_eofy()` returns `None` outside May and June. In May and June it
  walks `~/Documents` recursively, skipping a list of noise directories and
  bundle suffixes, and tallies filenames containing `invoice`, `receipt` or
  `tax`. The May/June window is the Australian financial year end and is
  meaningless in other jurisdictions.
- `probe_portfolio()` reads the last line of `~/portfolio/update.log`. That
  path belongs to a project that no longer exists, so the probe returns
  `None` on every run. It should be removed.

### `quarantine.py`

The safety pipeline. State lives under
`~/Library/Application Support/jury/`:

| Path | Contents |
| --- | --- |
| `quarantine/` | dated day directories holding quarantined files |
| `undo.log` | append-only tab-delimited action log |
| `state.json` | today's date and today's action count |
| `monitor.lock` | flock target for monitor concurrency |
| `monitor.last_run` | ISO timestamp of the last monitor pass |

`process_auto_delete_candidate()` runs a candidate through four gates in
order and returns a single outcome string:

1. `missing` if the path is gone.
2. `skipped_cooling` if mtime or atime is within `COOLING_HOURS` (24). A
   stat failure counts as in-window.
3. `skipped_open` if `lsof` reports the file open. A timeout or a missing
   `lsof` binary also counts as open.
4. `skipped_ceiling` if `DAILY_ACTION_CEILING` (200) actions have already
   been recorded today.

Only then does it move the file. The protected-extension invariant is
enforced upstream by `classify()`, so it is not rechecked here.

`move_to_quarantine()` writes to
`quarantine/<YYYY-MM-DD>/<sha256(original path)[:8]>/<filename>`, using
`os.rename` and falling back to `shutil.move` across devices. Alongside it it
writes `<filename>.jury-restore.json` recording `original_path`,
`quarantined_at` and `original_mtime`. The eight-character path hash keeps
same-named files from different directories apart within one day.

Every action appends one line to `undo.log`:
`<iso timestamp>\t<action>\t<original path>\t-> <current path>`.

`age_out()` walks day directories, and for any dated at least
`QUARANTINE_TTL_DAYS` (30) days ago, final-trashes each file through
`osascript` telling Finder to delete the POSIX file, logs a `final_trash`
line, then removes the day directory. Backslashes and double quotes in the
path are escaped before they reach AppleScript. Using Finder rather than
`unlink` means the file lands in the Trash and stays recoverable until the
Trash is emptied.

`restore_command()` produces a `mv` one-liner from the quarantine path back
to the original path, which the digest embeds per entry.

Quarantine lifecycle end to end: candidate detected, gates passed, file moved
plus sidecar written, entry listed in every digest for 30 days, flagged as
expiring in the last 7, then final-trashed to Finder's Trash and the day
directory removed.

### `monitor.py`

The WatchPaths entry point. Takes a non-blocking `fcntl.flock` on
`monitor.lock` and exits silently if another pass holds it, so a burst of
filesystem events cannot produce racing passes. Then `run_once()` scans,
pushes every auto-delete candidate through `process_auto_delete_candidate()`,
calls `age_out()`, and returns an outcome tally. `main()` writes an ISO
timestamp to `monitor.last_run` and always returns 0. Exceptions are written
to `undo.log` as a `monitor_error` line rather than raised.

### `digest.py`

The Sunday entry point. Scans, runs the candidates through the same
quarantine pipeline, runs all four probes, computes stale downloads, lists
current quarantine, and calls `write_digest()`. Both failure paths print to
stderr and exit 0.

`JURY_DRY_RUN=1` suppresses quarantine moves (the tally counts what would
have moved) and redirects the digest to `/tmp/jury-digest.md`.

Note that `age_out()` is not called on the digest path. Final-trashing is the
monitor's job.

### `digest_writer.py`

Renders eight numbered sections into one Markdown file. Sections 7 and 8 emit
nothing when their probe returns `None`, so a typical digest is six sections
long.

1. Filename clusters, ranked by reclaimable bytes, top 50 shown, each member
   with a paste-ready trash command, plus byte-identical subgroups with a
   nominated keeper and a single bulk-trash command for the rest.
2. Stale downloads, with reveal, Quick Look preview and trash commands.
3. Quarantine status, expiring entries first, each with a restore command.
   Up to 30 non-expiring entries are listed.
4. Monitor action tally, one line per non-zero outcome.
5. iCloud status.
6. Time Machine status.
7. EOFY invoice tally, May and June only.
8. Portfolio status, only when the last logged run failed.

The header carries the generation date, a seven-day validity date for the
paste commands, the monitor's last run time read from `monitor.last_run`, and
an iCloud quota warning when applicable.

`write_digest()` raises if U+2014 appears anywhere in the rendered output,
then overwrites the output path.

### `swift-app/Jury.swift`

An AppKit menubar app, `LSUIElement`, accessory activation policy, no Dock
icon and no main menu. Built by `swift-app/build.sh` with `swiftc -O`,
stripped, ad-hoc codesigned.

The status item is a 22 by 16 template `NSImage` drawn as a single
`NSBezierPath`, icon only, no title or badge. Clicking it toggles a
borderless non-activating `StatusPanel` (not an `NSPopover`) hosting a
SwiftUI view, positioned under the status button. A global mouse-down monitor
installed 0.2 seconds after opening dismisses it on any outside click.

The panel shows: quarantined count, expiring-soon count, up to six expiring
entries with days remaining, the monitor's last run time, and a banner once
today's action count reaches 180. Every element is a click target. Clicks
open the quarantine folder, the undo log, `state.json`, the monitor log, or
the digest, or reveal one quarantined file in Finder.

The app reads `quarantine/` and its sidecars, `undo.log`, `state.json`,
`monitor.last_run`, and checks whether `~/Documents/cleanup-digest.md`
exists. It has no timer, no FSEvents watcher and no notification observer:
the snapshot is read once per panel open and the panel is destroyed on close.

It cannot restore, trash or delete anything, and it cannot run the Python
side. The only writes it performs are creating an empty file or directory so
that a click on a missing target still opens something.

## Safety gates, in one list

- Auto-delete is an explicit four-pattern allowlist, not a heuristic.
- 18 protected document extensions can never be auto-deleted, with the single
  documented carve-out for `~$` lock files.
- Lock files additionally require an `lsof` check that the file is not open.
- A 24-hour cooling window blocks anything touched recently.
- An `lsof` gate blocks anything a process currently holds open.
- A 200-action daily ceiling caps the blast radius of a bad run.
- Nothing is deleted at detection time. Files are moved to quarantine and
  kept for 30 days.
- A restore sidecar next to every quarantined file records where it came
  from.
- Final removal goes through Finder to the Trash, not `unlink`.
- Every action is appended to `undo.log`.
- The monitor holds a lock so passes cannot race.
- Both entry points exit 0 unconditionally so launchd never disables them.
