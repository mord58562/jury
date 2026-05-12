"""
hashing.py - Partial content hashing to promote name-based clusters into
provable byte-identical subgroups.

A partial hash reads the first and last 64KB of a file and mixes the file
size into the digest. Two files share a partial hash only if they have the
same size AND their head and tail blocks match - a strong signal of being
byte-identical without paying the cost of hashing multi-megabyte files in
full.

Files smaller than 128KB are read in full so the head and tail don't overlap.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Optional

from scanner import FileInfo


PARTIAL_HASH_CHUNK = 64 * 1024


def partial_hash(path: Path) -> Optional[str]:
    """Return a 16-char hex digest of (first 64KB + last 64KB + size), or None on error.

    Size is mixed into the digest so different-sized files with matching
    head/tail blocks never collide.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            if size <= 2 * PARTIAL_HASH_CHUNK:
                h.update(f.read())
            else:
                h.update(f.read(PARTIAL_HASH_CHUNK))
                f.seek(-PARTIAL_HASH_CHUNK, 2)
                h.update(f.read(PARTIAL_HASH_CHUNK))
    except OSError:
        return None
    h.update(str(size).encode())
    return h.hexdigest()[:16]


def byte_identical_subgroups(members: list[FileInfo]) -> list[list[FileInfo]]:
    """Within a cluster, return groups of files that share a partial hash.

    Pre-filters by size so we never hash files that can't be byte-identical.
    Only groups of >=2 are returned; singletons are dropped.
    """
    by_size: dict[int, list[FileInfo]] = defaultdict(list)
    for fi in members:
        by_size[fi.size].append(fi)

    subgroups: list[list[FileInfo]] = []
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        by_hash: dict[str, list[FileInfo]] = defaultdict(list)
        for fi in same_size:
            digest = partial_hash(fi.path)
            if digest is None:
                continue
            by_hash[digest].append(fi)
        for group in by_hash.values():
            if len(group) >= 2:
                subgroups.append(group)
    return subgroups
