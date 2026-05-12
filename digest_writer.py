"""
digest_writer.py - Renders structured scan data into cleanup-digest.md.

Section layout:
  1. Filename Clusters
  2. Stale Downloads (with reveal/preview commands)
  3. Quarantine Status (entries, expiring-soon, restore commands)
  4. Monitor Action Tally (quarantined / skipped_* counts)
  5. iCloud Status
  6. Time Machine Status
  7. EOFY Tally (May/June only)
  8. Portfolio Status (only if last run failed)

A one-line iCloud quota note appears in the header when quota is exceeded.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from hashing import byte_identical_subgroups
from probes import EOFYStatus, PortfolioStatus, TMStatus, iCloudStatus
from quarantine import (
    APP_SUPPORT_ROOT,
    EXPIRING_SOON_DAYS,
    QUARANTINE_TTL_DAYS,
    QuarantineEntry,
    restore_command,
)
from scanner import Cluster, ScanResult
from stale import StaleFile


VALIDITY_DAYS = 7
TOP_N_CLUSTERS = 50
TOP_N_QUARANTINE = 30
LAST_RUN_FILE = APP_SUPPORT_ROOT / "monitor.last_run"


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB", "TB"]
    value = float(n)
    for unit in units:
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PB"


def _shell_quote(s: str) -> str:
    return s.replace("'", "'\\''")


def trash_command(file_path: str | Path) -> str:
    """osascript one-liner that moves the file to Trash."""
    escaped = _shell_quote(str(file_path))
    return f"osascript -e 'tell application \"Finder\" to delete POSIX file \"{escaped}\"'"


def bulk_trash_command(paths: list[Path | str]) -> str:
    """Single osascript that trashes multiple files in one call."""
    items = [f'POSIX file "{_shell_quote(str(p))}"' for p in paths]
    joined = ", ".join(items)
    return f"osascript -e 'tell application \"Finder\" to delete {{{joined}}}'"


def reveal_command(file_path: str | Path) -> str:
    """Open Finder and reveal the file."""
    return f'open -R "{_shell_quote(str(file_path))}"'


def preview_command(file_path: str | Path) -> str:
    """Quick Look preview the file (Esc to dismiss)."""
    return f'qlmanage -p "{_shell_quote(str(file_path))}" 2>/dev/null'


def _read_last_run() -> Optional[str]:
    try:
        return LAST_RUN_FILE.read_text().strip()
    except OSError:
        return None


def _header(generated_at: date, icloud_status: Optional[iCloudStatus] = None) -> str:
    valid_until = (generated_at + timedelta(days=VALIDITY_DAYS)).isoformat()
    last_run = _read_last_run()
    lines = [
        "# Jury - Cleanup Digest",
        f"Generated: {generated_at.isoformat()}",
        f"Paste commands valid until: {valid_until}",
        f"Monitor last ran: {last_run or 'never (not yet installed?)'}",
        "",
    ]
    if icloud_status and icloud_status.quota_exceeded:
        lines.append(
            "Note - iCloud quota exceeded as of last check; "
            "this file is local only until quota resolves."
        )
        lines.append("")
    return "\n".join(lines)


def render_clusters(clusters: list[Cluster], generated_at: date) -> str:
    """Render Section 1: Filename Clusters, sorted by reclaimable bytes desc."""
    lines = ["## Section 1 - Filename Clusters", ""]
    if not clusters:
        lines.append("No filename clusters detected.")
        lines.append("")
        return "\n".join(lines)

    ranked = sorted(
        clusters,
        key=lambda c: (c.reclaimable_bytes, len(c.members)),
        reverse=True,
    )
    shown = ranked[:TOP_N_CLUSTERS]
    hidden = ranked[TOP_N_CLUSTERS:]

    total_reclaimable = sum(c.reclaimable_bytes for c in ranked)
    lines.append(
        f"Detected {len(ranked)} cluster(s); "
        f"showing top {len(shown)} by reclaimable size. "
        f"Total reclaimable across all clusters: {_format_bytes(total_reclaimable)}."
    )
    lines.append("")

    for cluster in shown:
        label = "(cross-directory)" if cluster.cross_dir else ""
        lines.append(f"### Cluster: `{cluster.key}` {label}".rstrip())
        lines.append(
            f"Members: {len(cluster.members)} | "
            f"Reclaimable: {_format_bytes(cluster.reclaimable_bytes)} "
            f"(total {_format_bytes(cluster.total_bytes)})"
        )
        lines.append("")
        for fi in cluster.members:
            lines.append(f"- `{fi.name}` ({_format_bytes(fi.size)})")
            lines.append(f"  Path: `{fi.path}`")
            lines.append(f"  Trash: `{trash_command(fi.path)}`")
        lines.append("")

        subgroups = byte_identical_subgroups(cluster.members)
        if subgroups:
            lines.append("**Byte-identical groups (safe bulk-trash):**")
            for group in subgroups:
                keeper = max(group, key=lambda f: (len(f.name), f.name))
                trash_targets = [fi for fi in group if fi is not keeper]
                names = ", ".join(f"`{fi.name}`" for fi in group)
                lines.append(
                    f"- {len(group)} files identical "
                    f"({_format_bytes(group[0].size)} each): {names}"
                )
                lines.append(f"  Keeper: `{keeper.name}`")
                lines.append(
                    f"  Trash the rest: `{bulk_trash_command([fi.path for fi in trash_targets])}`"
                )
            lines.append("")

    if hidden:
        hidden_bytes = sum(c.reclaimable_bytes for c in hidden)
        lines.append(
            f"... plus {len(hidden)} more cluster(s) not shown, "
            f"reclaiming {_format_bytes(hidden_bytes)} total."
        )
        lines.append("")

    return "\n".join(lines)


def render_stale_downloads(stale: list[StaleFile], today: date) -> str:
    """Render Section 2: Stale Downloads with reveal/preview commands."""
    lines = ["## Section 2 - Stale Downloads", ""]
    if not stale:
        lines.append("No stale downloads detected.")
        lines.append("")
        return "\n".join(lines)

    total_bytes = sum(s.size for s in stale)
    never_opened = sum(1 for s in stale if s.last_used is None)
    lines.append(
        f"Found {len(stale)} stale file(s); "
        f"{_format_bytes(total_bytes)} total. "
        f"{never_opened} never opened (per Spotlight). "
        f"Use Reveal/Preview to check before trashing."
    )
    lines.append("")
    for s in stale:
        age_days = (today - s.mtime).days
        opened = s.last_used or "never (per Spotlight)"
        lines.append(f"- `{s.path.name}` ({_format_bytes(s.size)}, {age_days} days old)")
        lines.append(f"  Path: `{s.path}`")
        lines.append(f"  Last opened: {opened}")
        lines.append(f"  Reveal: `{reveal_command(s.path)}`")
        lines.append(f"  Preview: `{preview_command(s.path)}`")
        lines.append(f"  Trash: `{trash_command(s.path)}`")
    lines.append("")
    return "\n".join(lines)


def render_quarantine(entries: list[QuarantineEntry], today: date) -> str:
    """Render Section 3: Quarantine Status."""
    lines = ["## Section 3 - Quarantine Status", ""]
    if not entries:
        lines.append("Quarantine is empty.")
        lines.append("")
        return "\n".join(lines)

    total_count = len(entries)
    expiring = [e for e in entries if e.days_until_purge <= EXPIRING_SOON_DAYS]
    lines.append(
        f"Quarantined: {total_count} file(s). "
        f"{len(expiring)} expiring within {EXPIRING_SOON_DAYS} day(s) "
        f"(TTL {QUARANTINE_TTL_DAYS} days). "
        f"Reveal to inspect; Restore to put back."
    )
    lines.append("")

    if expiring:
        lines.append("**Expiring soon (final-trash imminent):**")
        for e in expiring:
            lines.append(
                f"- `{e.original_path.name}` "
                f"(quarantined {e.quarantined_at.isoformat()}, "
                f"{e.age_days} days ago, {e.days_until_purge} day(s) remaining)"
            )
            lines.append(f"  Original: `{e.original_path}`")
            lines.append(f"  Current:  `{e.current_path}`")
            lines.append(f"  Restore: `{restore_command(e)}`")
            lines.append(f"  Reveal: `{reveal_command(e.current_path)}`")
        lines.append("")

    other = [e for e in entries if e not in expiring]
    if other:
        shown = other[:TOP_N_QUARANTINE]
        hidden = other[TOP_N_QUARANTINE:]
        lines.append(f"**All quarantine entries (oldest {len(shown)} shown):**")
        for e in shown:
            lines.append(
                f"- `{e.original_path.name}` "
                f"({e.age_days} days quarantined, "
                f"{e.days_until_purge} day(s) remaining)"
            )
            lines.append(f"  Restore: `{restore_command(e)}`")
        if hidden:
            lines.append(f"... plus {len(hidden)} more entries.")
        lines.append("")

    return "\n".join(lines)


def render_action_tally(tally: dict, dry_run: bool = False) -> str:
    """Render Section 4: Monitor Action Tally."""
    lines = ["## Section 4 - Monitor Action Tally", ""]
    label_map = [
        ("quarantined", "Quarantined this run"),
        ("skipped_cooling", "Skipped (within 24h cooling window)"),
        ("skipped_open", "Skipped (file open per lsof)"),
        ("skipped_ceiling", "Skipped (daily action ceiling reached)"),
        ("missing", "Skipped (file no longer exists)"),
        ("failed", "Failed (filesystem error)"),
    ]
    if dry_run:
        lines.append(f"DRY RUN - would quarantine: {tally.get('quarantined', 0)} file(s)")
    else:
        for key, label in label_map:
            count = tally.get(key, 0)
            if count:
                lines.append(f"- {label}: {count}")
        if not any(tally.get(k, 0) for k, _ in label_map):
            lines.append("- No actions taken this run.")
    lines.append("")
    lines.append(
        "Patterns acted on: lock files (~$*), .DS_Store, *.crdownload, *.download. "
        "Files quarantined under ~/Library/Application Support/jury/quarantine/."
    )
    lines.append("")
    return "\n".join(lines)


def render_icloud_status(icloud: iCloudStatus) -> str:
    """Render Section 5: iCloud Status."""
    lines = ["## Section 5 - iCloud Status", ""]
    status = "caught-up" if icloud.caught_up else "not caught-up"
    lines.append(f"Sync state: {status}")
    if icloud.needs_upload:
        lines.append("Warning: files pending upload")
    if icloud.quota_exceeded:
        lines.append("Warning: iCloud quota exceeded (CKErrorDomain:25)")
    if icloud.detail:
        lines.append(f"Detail: {icloud.detail}")
    lines.append("")
    return "\n".join(lines)


def render_tm_status(tm: TMStatus) -> str:
    """Render Section 6: Time Machine Status."""
    lines = ["## Section 6 - Time Machine Status", ""]
    if not tm.available:
        lines.append("Time Machine: unavailable (drive may be offline)")
        if tm.detail:
            lines.append(f"Detail: {tm.detail}")
    else:
        lines.append(f"Last backup: {tm.latest_backup or 'unknown'}")
        lines.append(f"Running: {'yes' if tm.running else 'no'}")
    lines.append("")
    return "\n".join(lines)


def render_eofy(eofy: Optional[EOFYStatus]) -> str:
    """Render Section 7: EOFY Tally. Empty string outside May/June."""
    if eofy is None:
        return ""
    lines = [
        "## Section 7 - EOFY Invoice Tally",
        "",
        f"Invoices found: {eofy.invoice_count}",
        f"Receipts found: {eofy.receipt_count}",
        f"Tax documents found: {eofy.tax_count}",
        f"Note - confirm this matches your invoices folder ({eofy.docs_path})",
        "",
    ]
    return "\n".join(lines)


def render_portfolio(portfolio: Optional[PortfolioStatus]) -> str:
    """Render Section 8: Portfolio Status. Empty string on success."""
    if portfolio is None:
        return ""
    lines = [
        "## Section 8 - Portfolio Status",
        "",
        f"Last run status: {portfolio.last_status}",
        f"Detail: {portfolio.detail}",
        "",
    ]
    return "\n".join(lines)


def write_digest(
    scan_result: ScanResult,
    tm_status: TMStatus,
    icloud_status: iCloudStatus,
    eofy_status: Optional[EOFYStatus],
    portfolio_status: Optional[PortfolioStatus],
    output_path: Path,
    generated_at: Optional[date] = None,
    dry_run: bool = False,
    stale_downloads: Optional[list[StaleFile]] = None,
    quarantine_entries: Optional[list[QuarantineEntry]] = None,
    action_tally: Optional[dict] = None,
) -> str:
    """Orchestrate all sections and write the digest. Returns the content string."""
    today = generated_at or date.today()
    stale_list = stale_downloads if stale_downloads is not None else []
    quarantine_list = quarantine_entries if quarantine_entries is not None else []
    tally = action_tally if action_tally is not None else {}

    sections = [
        _header(today, icloud_status),
        render_clusters(scan_result.clusters, today),
        render_stale_downloads(stale_list, today),
        render_quarantine(quarantine_list, today),
        render_action_tally(tally, dry_run=dry_run),
        render_icloud_status(icloud_status),
        render_tm_status(tm_status),
        render_eofy(eofy_status),
        render_portfolio(portfolio_status),
    ]

    content = "\n".join(s for s in sections if s)

    if chr(0x2014) in content:
        raise ValueError("Em-dash (U+2014) detected in digest output - check render functions")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return content
