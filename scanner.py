"""
scanner.py - Directory walker and cluster detector for Jury.

Walks the top level of each target directory only. Nested project trees,
app bundles, conda envs, etc. are intentionally not descended into - the
goal is duplicate browser/save dumps at the top level of Documents and
Downloads, not project internals.

Cluster key algorithm:
  - Strip a trailing UUID segment (-8-4-4-4-12 hex) from the stem
  - Strip a trailing (N) paren suffix from the stem
  - Strip a trailing -N integer suffix (N <= 99) to avoid eating date segments
  - Lowercase the result for comparison; preserve original for display
  - URL-decode '+' characters to spaces before keying

Files matching auto-delete patterns (~$*, .DS_Store, *.crdownload, *.download)
are kept out of cluster output - they are surfaced via the auto-delete count
section instead and never double-reported.

Cross-directory clusters use an "old - " prefix pass: exact base-name match
(fuzzy-free) between prefixed file in one dir and unprefixed file in the other.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


_UUID_RE = re.compile(
    r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_PAREN_N_RE = re.compile(r"\s*\(\d+\)$")

_INT_SUFFIX_RE = re.compile(r"-(\d{1,2})$")

_OLD_PREFIX_RE = re.compile(r"^old\s*-\s*(.+)$", re.IGNORECASE)


@dataclass
class FileInfo:
    path: Path
    name: str
    stem: str
    suffix: str
    key: str
    size: int = 0


@dataclass
class Cluster:
    key: str
    members: list[FileInfo] = field(default_factory=list)
    cross_dir: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(m.size for m in self.members)

    @property
    def reclaimable_bytes(self) -> int:
        if not self.members:
            return 0
        return self.total_bytes - max(m.size for m in self.members)


@dataclass
class ScanResult:
    clusters: list[Cluster]
    auto_delete_candidates: list[FileInfo]
    all_files: list[FileInfo]
    downloads_files: list[FileInfo] = field(default_factory=list)


def cluster_key(filename: str) -> str:
    """Derive the cluster key from a filename.

    Strips UUID, (N), and -N suffixes from the stem; lowercases; URL-decodes
    '+' to spaces. The suffix (extension) is not part of the key so that
    .docx and .pdf with the same base cluster together.
    """
    path = Path(filename)
    stem = path.stem

    stem = stem.replace("+", " ")
    stem = _UUID_RE.sub("", stem)
    stem = _PAREN_N_RE.sub("", stem)

    m = _INT_SUFFIX_RE.search(stem)
    if m:
        stem = stem[: m.start()]

    return stem.strip().lower()


def _collect_files(directory: Path) -> list[FileInfo]:
    """Return FileInfo for every regular file directly inside `directory`.

    Top-level only - subdirectories are not descended into. This is the
    deliberate scope: nested project trees, app bundles, conda envs, and
    git repos are out of bounds for cluster detection.
    """
    result: list[FileInfo] = []
    if not directory.exists():
        return result
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return result
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        name = entry.name
        p = Path(entry.path)
        result.append(FileInfo(
            path=p,
            name=name,
            stem=p.stem,
            suffix=p.suffix.lower(),
            key=cluster_key(name),
            size=stat.st_size,
        ))
    return result


def find_clusters(files: list[FileInfo]) -> list[Cluster]:
    """Group files by cluster key; return only groups with 2+ members.

    Files matching any auto-delete pattern are excluded from clustering -
    they are surfaced via the auto-delete count section instead.
    """
    from collections import defaultdict
    from classifier import _matches_auto_delete

    groups: dict[str, list[FileInfo]] = defaultdict(list)
    for fi in files:
        if not fi.key:
            continue
        if _matches_auto_delete(fi.name):
            continue
        groups[fi.key].append(fi)
    return [
        Cluster(key=key, members=members)
        for key, members in groups.items()
        if len(members) >= 2
    ]


def find_cross_dir_clusters(
    docs_files: list[FileInfo],
    downloads_files: list[FileInfo],
) -> list[Cluster]:
    """Detect cross-directory 'old -' prefix clusters."""
    clusters: list[Cluster] = []

    def _try_match(source: list[FileInfo], target: list[FileInfo]) -> None:
        target_index: dict[str, list[FileInfo]] = {}
        for fi in target:
            key = fi.stem.lower().replace("+", " ")
            target_index.setdefault(key, []).append(fi)

        for fi in source:
            m = _OLD_PREFIX_RE.match(fi.stem)
            if not m:
                continue
            captured = m.group(1).strip().lower().replace("+", " ")
            if captured in target_index:
                members = [fi] + target_index[captured]
                clusters.append(Cluster(
                    key=f"cross:{captured}",
                    members=members,
                    cross_dir=True,
                ))

    _try_match(docs_files, downloads_files)
    _try_match(downloads_files, docs_files)

    return clusters


def find_auto_delete_candidates(files: list[FileInfo]) -> list[FileInfo]:
    """Return files matching auto-delete patterns (without lsof check here)."""
    from classifier import _matches_auto_delete
    return [fi for fi in files if _matches_auto_delete(fi.name)]


def scan_dirs(
    docs_path: Path,
    downloads_path: Path,
) -> ScanResult:
    """Walk top level of docs and downloads; return clusters and candidates."""
    docs_files = _collect_files(docs_path)
    downloads_files = _collect_files(downloads_path)
    all_files = docs_files + downloads_files

    same_dir_clusters = find_clusters(all_files)
    cross_clusters = find_cross_dir_clusters(docs_files, downloads_files)

    cross_keys = {c.key for c in cross_clusters}
    deduped_same = [c for c in same_dir_clusters if c.key not in cross_keys]

    clusters = deduped_same + cross_clusters
    candidates = find_auto_delete_candidates(all_files)

    return ScanResult(
        clusters=clusters,
        auto_delete_candidates=candidates,
        all_files=all_files,
        downloads_files=downloads_files,
    )
