# Jury - Design Document
Date: 2026-05-12

---

## Section 1 - File Layout

```
~/Downloads/jury/
├── RECON.md                  # reconnaissance (existing)
├── DESIGN.md                 # this document
├── install.sh                # splices digest call into weekly-clean.sh
├── digest.py                 # main entry point; orchestrates all modules
├── scanner.py                # walk dirs, find clusters and auto-delete candidates
├── classifier.py             # decide: auto-delete vs surface-only per file
├── digest_writer.py          # render sections into cleanup-digest.md
├── probes.py                 # TM, iCloud, EOFY, portfolio status checks
├── tokens.py                 # time-boxed move-to-Trash link generation/validation
└── tests/
    ├── fixtures/             # synthetic dir trees mirroring real clusters
    │   ├── Documents/
    │   └── Downloads/
    ├── test_scanner.py       # cluster detection against all 10 real clusters
    ├── test_classifier.py    # auto-delete safety; boundary conditions
    ├── test_digest_writer.py # markdown output shape; no em-dashes
    ├── test_probes.py        # mocked subprocess calls for TM/iCloud/portfolio
    └── test_tokens.py        # token generation and expiry
```

One-line purpose per file:

- `install.sh` - patches weekly-clean.sh to call digest.py via `|| echo "jury failed"`
- `digest.py` - CLI entry; calls scanner, classifier, probes, writer in sequence; exits 0 always
- `scanner.py` - walks Documents and Downloads; groups files into clusters by key; collects auto-delete candidates
- `classifier.py` - applies rules to each file: safe-delete list vs protected-surface list
- `digest_writer.py` - takes structured data from scanner and probes; writes ~/Documents/cleanup-digest.md
- `probes.py` - thin subprocess wrappers for `tmutil`, `brctl`, `lsof`, portfolio log, EOFY date check
- `tokens.py` - generates and validates timestamped one-week trash-link tokens embedded in the digest
- `tests/fixtures/` - synthetic file trees (zero-byte files) matching real cluster shapes from RECON
- `test_scanner.py` - one test per RECON cluster (A through J) plus edge cases
- `test_classifier.py` - auto-delete boundaries, lsof gate for lock files, protected extension list
- `test_digest_writer.py` - section rendering, link format, overwrite idempotency
- `test_probes.py` - subprocess patched; verifies parsing of brctl/tmutil output
- `test_tokens.py` - fresh token is valid; token older than 7 days is expired

---

## Section 2 - Architecture Decisions

### Language: Python for digest.py and modules; bash stays in weekly-clean.sh

Reasoning: the cluster-detection logic requires non-trivial string manipulation (UUID regex anchoring, cross-directory pairing, "old -" prefix matching). Python handles this cleanly; equivalent bash becomes unreadable and untestable. The file scanning uses `os.walk` / `pathlib`, which is more reliable than `find` for Unicode filenames. The digest is Markdown text - Python f-strings with explicit section functions are far easier to audit for em-dashes or formatting regressions than heredocs.

Bash stays for weekly-clean.sh itself - no reason to rewrite it. The splice is one line: `python3 ~/Downloads/jury/digest.py || echo "jury digest failed"`.

Python startup cost (~80ms) on a 3 AM launchd run is irrelevant. No daemon, no background process - single execution, exits cleanly.

### Cluster key algorithm

Base name = everything up to the first `-<8-4-4-4-12>` UUID segment, or up to ` (N)` suffix, or up to `-N` integer suffix (where N <= 99, to avoid matching date segments like `-2026`). The three suffix patterns are stripped independently and the same key can match across all three - so `file.xlsx`, `file-1.xlsx`, `file (2).xlsx`, and `file-<UUID>.xlsx` cluster together. UUID anchoring is strict: the regex must match the full 8-4-4-4-12 hex pattern, not just any hyphenated segment. This prevents collapsing unrelated files that happen to share a partial name prefix.

Cross-directory clusters use a separate pass: for each file matching `^old - (.+)` in either directory, look for a file matching captured group in the other directory. Fuzzy-free - exact base-name match only.

### No auto-delete of protected extensions

The auto-delete allowlist is explicit and short: `~$*` (with lsof check), `.DS_Store`, `*.crdownload`, `*.download`. Everything else is surface-only. This is a whitelist design - adding a new extension to the delete set requires a deliberate code change, not a missing exclusion.

### Move-to-Trash via osascript

`osascript -e 'tell application "Finder" to delete POSIX file "/path/to/file"'` moves to Trash without bypassing Trash (as opposed to `rm`). The digest embeds links as `[Move to Trash](trash://jury/<token>)` - these are not clickable in standard Markdown viewers. The realistic mechanism for this week's version: the digest lists each flagged file with an explicit macOS Terminal command the user can paste. Token expiry (7 days, matching the Sunday cadence) is implemented and checked, but the enforcement mechanism is a warning comment in the digest ("links generated on YYYY-MM-DD; paste commands valid until YYYY-MM-DD"). Full URL scheme handling is an open question deferred to Rob (see Section 7).

---

## Section 3 - Module Breakdown

### scanner.py

- `scan_dirs(docs_path, downloads_path) -> ScanResult`
- `cluster_key(filename) -> str` - strips UUID, ` (N)`, `-N` suffixes; lowercases for comparison but preserves display name
- `find_clusters(files: list[FileInfo]) -> list[Cluster]` - groups by key; returns only clusters with 2+ members
- `find_cross_dir_clusters(docs_files, downloads_files) -> list[Cluster]` - "old -" prefix pass
- `find_auto_delete_candidates(files) -> list[FileInfo]` - matches allowlist patterns; calls lsof gate for `~$*`

### classifier.py

- `PROTECTED_EXTENSIONS: frozenset` - `.docx .xlsx .pptx .pdf .docm .xlsm .pptm .rtf .pages .numbers .key .doc .xls .ppt .odt .txt .md .csv .pages .numbers .key`
- `AUTO_DELETE_PATTERNS: list[re.Pattern]` - `~$*`, `.DS_Store`, `*.crdownload`, `*.download`
- `classify(file_info) -> Classification` - returns `AUTO_DELETE | SURFACE_ONLY | IGNORE`
- `is_lock_file_safe(path) -> bool` - runs `lsof` on the parent path; returns True if no process has it open

### digest_writer.py

- `write_digest(scan_result, probe_result, output_path)` - orchestrates all sections; overwrites file
- `render_clusters(clusters) -> str` - Section 1 of digest
- `render_auto_deleted(counts) -> str` - Section 2; counts only, no names
- `render_icloud_status(icloud_probe) -> str` - Section 3
- `render_tm_status(tm_probe) -> str` - Section 4
- `render_eofy(eofy_probe) -> str` - Section 5; omitted if not May/June
- `render_portfolio(portfolio_probe) -> str` - Section 6; omitted if last run succeeded

### probes.py

- `probe_time_machine() -> TMStatus` - runs `tmutil latestbackup` and `tmutil status`; catches non-zero exit
- `probe_icloud() -> iCloudStatus` - runs `brctl status`; greps for `caught-up`, `needs-upload`, CKErrorDomain
- `probe_eofy(today: date) -> EOFYStatus | None` - returns None outside May-June
- `probe_portfolio(log_path: Path) -> PortfolioStatus | None` - reads last line of ~/portfolio/update.log; None if log absent or last run succeeded

### tokens.py

- `generate_token(file_path, generated_at) -> str` - base64url of `sha256(path + iso_date)[:8]`; not a security primitive, just uniqueness for display
- `token_valid(token, generated_at, today) -> bool` - True if age <= 7 days
- `trash_command(file_path) -> str` - returns the `osascript` one-liner the user can paste

---

## Section 4 - Test Plan

All tests use `pytest`. Fixtures are zero-byte files in `tests/fixtures/` created by a `conftest.py` `tmp_path`-scoped fixture that mirrors the real cluster shapes.

### test_scanner.py

- `test_cluster_A_une_timetable_uuid_variants` - five files from RECON Cluster A; assert one cluster with 5 members; key is `2025 y3 acme student timetables_canvas`
- `test_cluster_B_une_timetable_downloads_uuid_and_n` - 18 files including URL-encoded `+` variant; assert key strips `+` decoding and UUID; single cluster
- `test_cluster_C_y2_timetable_paren_n` - `(1)` `(2)` `(3)` variants; assert cluster key matches base; `(doe ...)` date segment not stripped
- `test_cluster_D_weekly_report_final_variants` - four files with "final" and "dot point version" suffixes; assert one cluster
- `test_cluster_E_dated_old_prefix_cross_dir` - four `old - XX...` files in Documents, four matching files in Downloads; assert cross-dir cluster detected
- `test_cluster_F_certificate_year_suffix` - two files, year-suffix `2026` stripped; single cluster
- `test_cluster_G_research_proposal_docx_pdf` - same base, two extensions; single cluster
- `test_cluster_H_application_typo` - typo variant vs full name; assert cluster (fuzzy-free: these share no clean key so they do NOT cluster - test asserts zero clusters, confirming the scanner does not hallucinate)
- `test_cluster_I_survey_drafts` - "early draft", "draft", final; assert cluster on base name
- `test_cluster_J_nextdns_paren_n` - mobileconfig with ` (1)` ` (2)` ` (3)`; assert cluster
- `test_uuid_not_over_anchored` - two files with different UUIDs but same base; assert they cluster (same key)
- `test_integer_suffix_vs_date_segment` - `file-2026.xlsx` should NOT strip `-2026`; `file-3.xlsx` should strip `-3`
- `test_single_file_no_cluster` - one file; assert no cluster returned
- `test_empty_dir` - empty dirs; assert ScanResult with zero clusters and zero candidates

### test_classifier.py

- `test_auto_delete_ds_store` - `.DS_Store` classified AUTO_DELETE
- `test_auto_delete_lock_file_when_safe` - `~$file.docx` with lsof patched to return no open handle; classified AUTO_DELETE
- `test_lock_file_skipped_when_parent_open` - lsof patched to return a hit; classified SURFACE_ONLY (not deleted)
- `test_protected_extension_docx` - `.docx` file classified SURFACE_ONLY regardless of name
- `test_protected_extension_pdf` - `.pdf` file classified SURFACE_ONLY
- `test_protected_extension_pages` - `.pages` file classified SURFACE_ONLY
- `test_crdownload_auto_delete` - partial download classified AUTO_DELETE
- `test_whitelist_is_explicit` - unknown extension `.xyz` that does not match auto-delete patterns classified IGNORE

### test_digest_writer.py

- `test_digest_overwrites_existing` - write twice; file contains only the second run's content
- `test_clusters_section_present` - output contains "Filename Clusters" heading
- `test_auto_deleted_count_no_names` - section shows integer count; no individual filenames
- `test_eofy_section_omitted_in_march` - probe returns None; section absent
- `test_eofy_section_present_in_may` - probe returns tally; section present
- `test_portfolio_section_omitted_on_success` - probe returns success; section absent
- `test_trash_command_in_cluster_entry` - each cluster member entry contains the osascript paste command
- `test_no_em_dashes_in_output` - assert `chr(0x2014) not in output` (U+2014 codepoint check, not visual scan)

### test_probes.py

- `test_tm_unavailable_on_non_zero_exit` - `tmutil` patched to exit 1; TMStatus.available is False
- `test_icloud_caught_up` - `brctl status` output containing `caught-up` and no `needs-upload`; status is OK
- `test_icloud_quota_exceeded` - output containing `CKErrorDomain:25`; status surfaces warning
- `test_portfolio_probe_absent_log` - log path does not exist; returns None
- `test_portfolio_probe_last_line_success` - log ends with "OK"; returns None (no alert)
- `test_portfolio_probe_last_line_failed` - log ends with "FAILED"; returns PortfolioStatus with detail

### test_tokens.py

- `test_fresh_token_valid` - token generated today; valid
- `test_token_expired_after_7_days` - token generated 8 days ago; invalid
- `test_trash_command_well_formed` - returned string contains `osascript` and the file path; no shell injection via spaces (path is quoted)

---

## Section 5 - Install Integration

`install.sh` takes the safer sibling-script approach: digest.py is invoked as a one-liner inserted into weekly-clean.sh, not as a full rewrite.

Splice location: after the `qlmanage -r cache` line, before the iOS backup `find` block.

The installer:
1. Confirms `~/Downloads/jury/digest.py` exists
2. Confirms Python 3 is at `~/miniconda3/bin/python3` (falls back to `/usr/bin/python3`)
3. Checks that the splice line is not already present (idempotent)
4. Uses `sed` to insert the call after the `qlmanage` line

Inserted line (inside the existing `{ ... } >> "$LOG"` block):

```
  ~/miniconda3/bin/python3 ~/Downloads/jury/digest.py 2>&1 || echo "jury digest failed"
```

If the Python invocation errors for any reason, `|| echo "..."` ensures weekly-clean.sh continues. The error message is captured by the existing `>> "$LOG" 2>&1` redirect on the outer block, so it lands in `~/Library/Logs/weekly-clean.log` automatically.

No changes to the launchd plist are needed - it already fires the existing script on Sunday at 03:00.

---

## Section 6 - Smoke Test Procedure

1. `cd ~/Downloads/jury`
2. `pip install pytest` (or `~/miniconda3/bin/pip install pytest`) if not present
3. `pytest tests/ -v` - all unit tests run against synthetic fixtures; should pass on a fresh clone with no real Documents/Downloads access
4. Manual smoke run against real dirs:
   ```
   JURY_DRY_RUN=1 ~/miniconda3/bin/python3 digest.py \
     --docs ~/Documents --downloads ~/Downloads \
     --output /tmp/digest-smoke.md
   ```
   `JURY_DRY_RUN=1` suppresses all actual deletions and writes the digest to `/tmp/` instead of `~/Documents/`.
5. Inspect `/tmp/digest-smoke.md`:
   - Confirm Cluster A (Acme Timetable) appears with 5 members
   - Confirm auto-deleted count matches RECON totals (17 Documents + 20 Downloads = 37)
   - Confirm no em-dashes (run `grep -P '\xe2\x80\x94' /tmp/digest-smoke.md` - should return nothing)
   - Confirm each cluster entry contains the osascript paste command
6. After smoke passes, run `install.sh` and verify the splice landed: `grep jury ~/bin/weekly-clean.sh`

---

## Section 7 - Open Questions for Rob

**Q1 - Move-to-Trash link mechanism (recommended default: paste commands only)**
The digest can embed osascript one-liners users paste into Terminal, or it can try to register a custom URL scheme (`trash://jury/...`) so links are clickable in a Markdown viewer. The URL scheme requires a small LaunchAgent and Automator or a compiled helper. Recommended default: paste commands only - simpler, no extra daemon, Safari/Markdown Preview is not involved. Upgrade to URL scheme is a one-week addition if desired.

**Q2 - Cluster similarity threshold for fuzzy matches like Cluster H (recommended default: exact-key-match only, no fuzzy)**
Cluster H (application-letter typo variant) shares no clean key with the corrected-name file. Fuzzy matching (Levenshtein distance) would catch it but also generates false positives on unrelated files with short names. Recommended: skip fuzzy, label Cluster H as out-of-scope for v1. Can be added as an opt-in flag later.

**Q3 - Auto-delete actually runs, or dry-run always (recommended default: auto-delete runs for the safe list)**
Lock files and .DS_Store are unambiguously safe. Recommended: auto-delete the allowlist (with lsof gate for lock files) and log names + count to the digest. If Rob prefers a fully dry-run mode where even these require confirmation, add `JURY_DRY_RUN=1` in the launchd environment - but the script defaults to live deletion of the safe list.

**Q4 - EOFY invoice tally source (recommended default: count .pdf files in ~/Documents matching "invoice" or "receipt" in name)**
RECON does not specify where invoices live. If they are in a dedicated folder (e.g. `~/Documents/Invoices/`), a path-scoped count is more accurate. Recommended: scan `~/Documents` recursively for filenames matching `invoice|receipt|tax` (case-insensitive) and surface the count with a note to Rob to confirm the folder.

**Q5 - Digest written to iCloud-synced ~/Documents (recommended default: accept the risk, note it in header)**
RECON Section 6 notes iCloud quota is currently exceeded. The digest itself is a small .md file and will queue for upload when quota clears. The local file is always written successfully regardless of iCloud state. Recommended: add a one-line note at the top of the digest: "Note - iCloud quota exceeded as of last check; this file is local only until quota resolves." No behavioral change needed.
