"""
test_hashing.py - Partial content hash and byte-identical subgroup tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hashing import PARTIAL_HASH_CHUNK, byte_identical_subgroups, partial_hash
from scanner import FileInfo


def _make_fi(path: Path) -> FileInfo:
    return FileInfo(
        path=path,
        name=path.name,
        stem=path.stem,
        suffix=path.suffix.lower(),
        key="x",
        size=path.stat().st_size,
    )


class TestPartialHash:
    def test_identical_small_files_same_hash(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"hello world")
        b.write_bytes(b"hello world")
        assert partial_hash(a) == partial_hash(b)

    def test_different_small_files_different_hash(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"hello world")
        b.write_bytes(b"HELLO world")
        assert partial_hash(a) != partial_hash(b)

    def test_size_mixed_into_hash(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"x" * 100)
        b.write_bytes(b"x" * 200)
        assert partial_hash(a) != partial_hash(b), (
            "Different sizes must produce different hashes"
        )

    def test_large_file_head_tail_hashing(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        # 200KB - exceeds 128KB threshold so partial hashing kicks in
        size = 200 * 1024
        a.write_bytes(b"H" * PARTIAL_HASH_CHUNK + b"M" * (size - 2 * PARTIAL_HASH_CHUNK) + b"T" * PARTIAL_HASH_CHUNK)
        b.write_bytes(b"H" * PARTIAL_HASH_CHUNK + b"X" * (size - 2 * PARTIAL_HASH_CHUNK) + b"T" * PARTIAL_HASH_CHUNK)
        # Middle differs but head and tail are the same and sizes match - partial hash collides.
        # This is the intentional trade-off documented in hashing.py.
        assert partial_hash(a) == partial_hash(b)

    def test_missing_file_returns_none(self, tmp_path):
        assert partial_hash(tmp_path / "does_not_exist.bin") is None


class TestByteIdenticalSubgroups:
    def test_three_identical_files_one_subgroup(self, tmp_path):
        files = []
        for name in ("x-1.pdf", "x-2.pdf", "x-3.pdf"):
            p = tmp_path / name
            p.write_bytes(b"same content")
            files.append(_make_fi(p))
        groups = byte_identical_subgroups(files)
        assert len(groups) == 1
        assert {fi.name for fi in groups[0]} == {"x-1.pdf", "x-2.pdf", "x-3.pdf"}

    def test_no_match_returns_empty(self, tmp_path):
        files = []
        for i, name in enumerate(("x-1.pdf", "x-2.pdf")):
            p = tmp_path / name
            p.write_bytes(b"unique" + bytes([i]))
            files.append(_make_fi(p))
        assert byte_identical_subgroups(files) == []

    def test_size_prefilter_skips_different_sizes(self, tmp_path):
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        a.write_bytes(b"AAAA")
        b.write_bytes(b"AAAA" * 100)
        # Different sizes - not byte-identical, regardless of partial hash
        assert byte_identical_subgroups([_make_fi(a), _make_fi(b)]) == []

    def test_mixed_cluster_partial_match(self, tmp_path):
        """Two identical, one distinct - one subgroup of two."""
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        c = tmp_path / "c.pdf"
        a.write_bytes(b"shared content")
        b.write_bytes(b"shared content")
        c.write_bytes(b"different stuff!!")
        groups = byte_identical_subgroups([_make_fi(a), _make_fi(b), _make_fi(c)])
        assert len(groups) == 1
        assert {fi.name for fi in groups[0]} == {"a.pdf", "b.pdf"}
