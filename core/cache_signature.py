"""
Cache validation helpers: fast nanosecond mtime and quick signature.
STRICT ARCHITECTURAL RULE (Phase E):
Quick signature is an auxiliary cache validation filter, NOT an authoritative
proof of cryptographic file identity for destructive operations (delete, trash).
Exact matches destined for auto-delete must re-validate the full SHA-256 hash.
"""

import os
import hashlib
from typing import Optional


def compute_quick_signature(filepath: str, block_size: int = 4096) -> str:
    """
    Computes a fast signature from head, middle, and tail blocks of a file.
    Deterministic across all file sizes, including small, zero-byte, or overlapping files.
    """
    if not filepath or not os.path.isfile(filepath):
        return ""

    try:
        size = os.path.getsize(filepath)
        if size == 0:
            return hashlib.blake2b(b"empty").hexdigest()

        hasher = hashlib.blake2b(digest_size=20)
        hasher.update(size.to_bytes(8, byteorder="big"))

        with open(filepath, "rb") as f:
            if size <= block_size * 3:
                # Small or overlapping file: read entire file once deterministically
                hasher.update(f.read())
            else:
                # 1. Head block (first 4KB)
                head = f.read(block_size)
                hasher.update(head)

                # 2. Middle block (4KB centered)
                mid_offset = (size // 2) - (block_size // 2)
                f.seek(mid_offset)
                middle = f.read(block_size)
                hasher.update(middle)

                # 3. Tail block (last 4KB)
                tail_offset = size - block_size
                f.seek(tail_offset)
                tail = f.read(block_size)
                hasher.update(tail)

        return hasher.hexdigest()
    except Exception:
        return ""


def is_cache_valid(
    filepath: str,
    cached_size: int,
    cached_mtime_ns: int,
    cached_signature: str
) -> bool:
    """
    Validates whether an AudioTrack database cache record is fresh.
    Requires st_size, st_mtime_ns, and quick_signature to match.
    """
    try:
        stat = os.stat(filepath)
        if stat.st_size != cached_size:
            return False

        current_mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        if current_mtime_ns != cached_mtime_ns:
            return False

        if cached_signature:
            curr_sig = compute_quick_signature(filepath)
            if curr_sig != cached_signature:
                return False

        return True
    except Exception:
        return False


def verify_authoritative_sha256_before_destructive_action(filepath: str, claimed_sha256: str) -> bool:
    """
    Guarantees that a cached SHA-256 is re-verified byte-by-byte before any destructive action.
    A quick signature must NEVER be used alone to authorize permanent file deletion.
    """
    if not filepath or not os.path.isfile(filepath) or not claimed_sha256:
        return False

    try:
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest() == claimed_sha256
    except Exception:
        return False
