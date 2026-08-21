"""
Acoustic Fingerprinting module using local Chromaprint (fpcalc) and audio hashes.
"""

import os
import sys
import json
import zlib
import struct
import hashlib
import subprocess
from typing import List, Tuple, Optional


def get_fpcalc_path() -> str:
    """Find local fpcalc binary or fallback to PATH."""
    # Check bundled bin folder
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    bundled_bin = os.path.join(project_root, "bin", "fpcalc.exe" if sys.platform == "win32" else "fpcalc")
    if os.path.isfile(bundled_bin) and os.access(bundled_bin, os.X_OK):
        return bundled_bin
    
    # Check relative to working directory
    local_bin = os.path.join(os.getcwd(), "bin", "fpcalc.exe" if sys.platform == "win32" else "fpcalc")
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin

    # Check system PATH
    import shutil
    sys_fpcalc = shutil.which("fpcalc")
    if sys_fpcalc:
        return sys_fpcalc
        
    return bundled_bin


def compute_file_sha256(filepath: str, block_size: int = 65536) -> str:
    """Computes SHA-256 hash of the entire file on disk."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            hasher.update(block)
    return hasher.hexdigest()


def compute_audio_pcm_hash(filepath: str, sample_rate: int = 11025) -> str:
    """
    Decodes audio stream to raw mono PCM with ffmpeg and computes MD5 hash.
    Identical audio recordings with different ID3 tags or containers will match here.
    """
    try:
        cmd = [
            "ffmpeg", "-v", "quiet", "-nostdin",
            "-i", filepath,
            "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
            "-"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        hasher = hashlib.md5()
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
        proc.wait()
        return hasher.hexdigest()
    except Exception:
        return ""


def extract_fingerprint(
    filepath: str,
    fpcalc_path: Optional[str] = None,
    max_length_seconds: int = 0
) -> Tuple[float, List[int]]:
    """
    Extracts acoustic fingerprint as a list of 32-bit unsigned integers
    and the accurate audio duration using fpcalc.
    
    Args:
        filepath: Path to audio file.
        fpcalc_path: Optional path to fpcalc binary.
        max_length_seconds: 0 for full track, or positive int for first N seconds.
        
    Returns:
        (duration_in_seconds, list_of_raw_integers)
    """
    if not fpcalc_path:
        fpcalc_path = get_fpcalc_path()

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Audio file not found: {filepath}")

    cmd = [
        fpcalc_path,
        "-raw",
        "-json",
        "-length", str(max_length_seconds),
        filepath
    ]

    # Run fpcalc subprocess without displaying window on Windows
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        startupinfo=startupinfo,
        encoding="utf-8",
        errors="replace"
    )

    if proc.returncode != 0:
        raise RuntimeError(f"fpcalc failed with code {proc.returncode}: {proc.stderr.strip()}")

    try:
        data = json.loads(proc.stdout)
        duration = float(data.get("duration", 0.0))
        raw_fp = data.get("fingerprint", [])
        return duration, raw_fp
    except Exception as e:
        raise ValueError(f"Failed to parse fpcalc JSON output: {e}\nOutput: {proc.stdout}")


def compress_fingerprint(raw_fp: List[int]) -> bytes:
    """Packs list of 32-bit uints into compressed binary blob for SQLite."""
    if not raw_fp:
        return b""
    packed = struct.pack(f"<{len(raw_fp)}I", *raw_fp)
    return zlib.compress(packed, level=6)


def decompress_fingerprint(blob: bytes) -> List[int]:
    """Unpacks binary blob from SQLite into list of 32-bit uints."""
    if not blob:
        return []
    uncompressed = zlib.decompress(blob)
    count = len(uncompressed) // 4
    return list(struct.unpack(f"<{count}I", uncompressed))
