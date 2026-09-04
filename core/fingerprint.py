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


# ─────────────────────────────────────────────────────────────────────────────
#  Windows CPU-priority helpers
# ─────────────────────────────────────────────────────────────────────────────

# Windows Process Priority constants
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_IDLE_PRIORITY_CLASS          = 0x00000040

def _get_low_priority_startupinfo():
    """Returns a STARTUPINFO that hides the window (Windows only)."""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si

def _set_subprocess_low_priority(proc: subprocess.Popen):
    """
    Drops the subprocess to BELOW_NORMAL CPU priority on Windows so the
    scanner never starves the GUI or other foreground apps.
    No-op on non-Windows platforms.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(
            0x0200,  # PROCESS_SET_INFORMATION
            False,
            proc.pid
        )
        if handle:
            ctypes.windll.kernel32.SetPriorityClass(handle, _BELOW_NORMAL_PRIORITY_CLASS)
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass  # Non-fatal: if it fails, we just run at normal priority


def get_fpcalc_path() -> str:
    """Find local fpcalc binary or fallback to PATH, with support for PyInstaller bundles."""
    # 1. Check PyInstaller temp directory (_MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        meipass_bin = os.path.join(sys._MEIPASS, "bin", "fpcalc.exe" if sys.platform == "win32" else "fpcalc")
        if os.path.isfile(meipass_bin):
            return meipass_bin
        meipass_root_bin = os.path.join(sys._MEIPASS, "fpcalc.exe" if sys.platform == "win32" else "fpcalc")
        if os.path.isfile(meipass_root_bin):
            return meipass_root_bin

    # 2. Check directory where executable/script is located
    exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates = [
        os.path.join(exe_dir, "bin", "fpcalc.exe" if sys.platform == "win32" else "fpcalc"),
        os.path.join(exe_dir, "fpcalc.exe" if sys.platform == "win32" else "fpcalc"),
        os.path.join(os.path.dirname(exe_dir), "bin", "fpcalc.exe" if sys.platform == "win32" else "fpcalc"),
        os.path.join(os.getcwd(), "bin", "fpcalc.exe" if sys.platform == "win32" else "fpcalc")
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    # 3. Check system PATH
    import shutil
    sys_fpcalc = shutil.which("fpcalc")
    if sys_fpcalc:
        return sys_fpcalc
        
    return candidates[0]


# ─────────────────────────────────────────────────────────────────────────────
#  Hashing
# ─────────────────────────────────────────────────────────────────────────────

_PARTIAL_HASH_CHUNK = 65536  # 64 KB read from head and tail for fast pre-filter

def compute_partial_sha256(filepath: str, chunk_size: int = _PARTIAL_HASH_CHUNK) -> str:
    """
    Computes a fast partial SHA-256 using only the first + last 64 KB of the file
    plus the file size. Used as a quick pre-filter: if this doesn't match,
    the full SHA-256 will definitely not match either, saving CPU on large files.

    NOTE: Two files can have the same partial hash but different full hashes
    (false positive). Always confirm with compute_file_sha256 before treating
    as an exact duplicate.
    """
    try:
        filesize = os.path.getsize(filepath)
        hasher = hashlib.sha256()
        # Include file size in the hash to distinguish files of different lengths
        hasher.update(struct.pack("<Q", filesize))
        with open(filepath, "rb") as f:
            # Read first chunk
            head = f.read(chunk_size)
            hasher.update(head)
            # Read last chunk (if file is large enough to have a distinct tail)
            if filesize > chunk_size * 2:
                f.seek(-chunk_size, 2)
                tail = f.read(chunk_size)
                hasher.update(tail)
        return hasher.hexdigest()
    except Exception:
        return ""


def compute_file_sha256(filepath: str, block_size: int = 131072) -> str:
    """
    Computes full SHA-256 hash of the entire file.
    Uses 128 KB blocks (2x the previous 64 KB) for better I/O throughput.
    """
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            hasher.update(block)
    return hasher.hexdigest()


def compute_audio_pcm_hash(filepath: str, sample_rate: int = 11025, max_seconds: float = 30.0) -> str:
    """
    Decodes the first `max_seconds` (default 30s) of audio to raw mono PCM with ffmpeg and computes MD5.
    
    NOTA DE SEGURIDAD Y ARQUITECTURA:
    Este hash actúa como un PREFILTRO RÁPIDO de prefijo de audio normalizado y NO constituye
    por sí solo prueba de identidad completa del archivo. Dos audios distintos con una intro idéntica
    pueden colisionar aquí.
    La clasificación final como EXACT_AUDIO requiere duraciones compatibles (|dur_a - dur_b| <= 0.5s)
    y verificación en streaming del flujo PCM normalizado completo con verify_full_normalized_pcm_match.
    """
    try:
        cmd = [
            "ffmpeg", "-v", "quiet", "-nostdin",
            "-i", filepath,
            "-t", str(max_seconds),   # only first 30s as fast pre-filter
            "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
            "-"
        ]
        startupinfo = _get_low_priority_startupinfo() if sys.platform == "win32" else None
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo
        )
        # Drop ffmpeg to BELOW_NORMAL priority immediately after spawning
        _set_subprocess_low_priority(proc)
        try:
            stdout, _ = proc.communicate(timeout=30.0)
            if not stdout:
                return ""
            hasher = hashlib.md5()
            hasher.update(stdout)
            return hasher.hexdigest()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return ""
    except Exception:
        return ""


def verify_full_normalized_pcm_match(
    filepath_a: str,
    filepath_b: str,
    sample_rate: int = 11025,
    chunk_size: int = 65536
) -> bool:
    """
    Decodes both complete audio files to normalized raw mono PCM (s16le at sample_rate)
    using streaming FFmpeg processes and compares them block by block.

    Safety and performance guarantees:
    - Never loads full files into RAM: streams in chunks (default 64 KB).
    - Early exit: terminates immediately upon first mismatch or stream length difference.
    - Fails safe: if either file does not exist, decodes to 0 bytes, or encounters any error,
      returns False (never assumes equivalence).
    - Low priority CPU usage: drops subprocesses to BELOW_NORMAL priority.
    """
    if not filepath_a or not filepath_b:
        return False
    if not os.path.isfile(filepath_a) or not os.path.isfile(filepath_b):
        return False

    cmd_a = [
        "ffmpeg", "-v", "quiet", "-nostdin",
        "-i", filepath_a,
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
        "-"
    ]
    cmd_b = [
        "ffmpeg", "-v", "quiet", "-nostdin",
        "-i", filepath_b,
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
        "-"
    ]

    startupinfo = _get_low_priority_startupinfo() if sys.platform == "win32" else None
    proc_a = None
    proc_b = None

    try:
        proc_a = subprocess.Popen(
            cmd_a,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo
        )
        proc_b = subprocess.Popen(
            cmd_b,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo
        )

        _set_subprocess_low_priority(proc_a)
        _set_subprocess_low_priority(proc_b)

        total_read = 0
        while True:
            chunk_a = proc_a.stdout.read(chunk_size)
            chunk_b = proc_b.stdout.read(chunk_size)

            if chunk_a != chunk_b:
                return False

            if not chunk_a:  # Both reached EOF simultaneously
                break

            total_read += len(chunk_a)

        # Disallow empty decoded streams
        if total_read == 0:
            return False

        # Confirm both FFmpeg processes finish cleanly without errors
        try:
            ret_a = proc_a.wait(timeout=10.0)
            ret_b = proc_b.wait(timeout=10.0)
            return ret_a == 0 and ret_b == 0
        except subprocess.TimeoutExpired:
            return False

    except Exception:
        return False
    finally:
        for p in (proc_a, proc_b):
            if p is not None:
                try:
                    if p.poll() is None:
                        p.kill()
                    if p.stdout is not None:
                        p.stdout.close()
                except Exception:
                    pass


# ─────────────────────────────────────────────────────────────────────────────
#  Fingerprinting
# ─────────────────────────────────────────────────────────────────────────────

def extract_fingerprint(
    filepath: str,
    fpcalc_path: Optional[str] = None,
    max_length_seconds: int = 0
) -> Tuple[float, List[int]]:
    """
    Extracts acoustic fingerprint as a list of 32-bit unsigned integers
    and the accurate audio duration using fpcalc.

    Performance note: max_length_seconds=60 is the recommended default for scanning.
    Chromaprint only needs ~60s to produce a reliable fingerprint; using 120s doubles
    fpcalc CPU time with no meaningful accuracy improvement.

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
    startupinfo = _get_low_priority_startupinfo() if sys.platform == "win32" else None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
        )
        # Drop fpcalc to BELOW_NORMAL priority so it doesn't starve the GUI
        _set_subprocess_low_priority(proc)

        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=30.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError(f"fpcalc timeout expired after 30.0 seconds for file: {filepath}")

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"fpcalc failed to launch: {e}")

    if proc.returncode != 0:
        raise RuntimeError(f"fpcalc failed with code {proc.returncode}: {stderr.strip()}")

    try:
        data = json.loads(stdout)
        duration = float(data.get("duration", 0.0))
        raw_fp = data.get("fingerprint", [])
        return duration, raw_fp
    except Exception as e:
        raise ValueError(f"Failed to parse fpcalc JSON output: {e}\nOutput: {stdout}")


# ─────────────────────────────────────────────────────────────────────────────
#  Fingerprint compression
# ─────────────────────────────────────────────────────────────────────────────

def compress_fingerprint(raw_fp: List[int]) -> bytes:
    """Packs list of 32-bit uints into compressed binary blob for SQLite.
    Uses zlib level 1 (3x faster than level 6, ~5% larger — a fair trade for real-time indexing).
    """
    if not raw_fp:
        return b""
    packed = struct.pack(f"<{len(raw_fp)}I", *raw_fp)
    return zlib.compress(packed, level=1)  # Fast compress: 3x faster than level 6


def decompress_fingerprint(blob: bytes) -> List[int]:
    """Unpacks binary blob from SQLite into list of 32-bit uints."""
    if not blob:
        return []
    uncompressed = zlib.decompress(blob)
    count = len(uncompressed) // 4
    return list(struct.unpack(f"<{count}I", uncompressed))
