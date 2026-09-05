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
from dataclasses import dataclass


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


from core.binary_resolver import get_fpcalc_path, get_ffmpeg_path, get_ffprobe_path
from core.ffmpeg_runner import terminate_process_tree


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
        ffmpeg_bin = get_ffmpeg_path() or "ffmpeg"
        cmd = [
            ffmpeg_bin, "-v", "quiet", "-nostdin",
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


@dataclass(frozen=True)
class AudioStreamInfo:
    codec_name: str
    sample_rate: int
    channels: int
    channel_layout: str
    sample_fmt: str
    bit_depth: Optional[int]
    is_lossless: bool


LOSSLESS_CODECS = {
    "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_s16be", "pcm_s24be", "pcm_s32be",
    "pcm_s64le", "pcm_s64be", "pcm_u8", "pcm_f32le", "pcm_f64le", "flac", "alac",
    "wavpack", "ape", "tak", "shorten"
}


def get_audio_stream_info(filepath: str) -> Optional[AudioStreamInfo]:
    """
    Extracts essential audio stream parameters via ffprobe for strict information-preserving comparisons.
    Queries: codec_name, sample_rate, channels, channel_layout, sample_fmt, bits_per_sample, bits_per_raw_sample.
    """
    if not filepath or not os.path.isfile(filepath):
        return None

    ffprobe_bin = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe_bin, "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "a:0", filepath
    ]
    startupinfo = _get_low_priority_startupinfo() if sys.platform == "win32" else None

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            startupinfo=startupinfo,
            timeout=10.0
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]

        codec = str(s.get("codec_name", "")).lower()
        sr = int(s.get("sample_rate", 0) or 0)
        ch = int(s.get("channels", 0) or 0)
        layout = str(s.get("channel_layout", "")).lower().strip()
        if not layout:
            layout = "mono" if ch == 1 else ("stereo" if ch == 2 else str(ch))
        fmt = str(s.get("sample_fmt", "")).lower().strip()

        raw_bits = s.get("bits_per_raw_sample") or s.get("bits_per_sample")
        bd: Optional[int] = None
        if raw_bits and str(raw_bits).isdigit() and int(raw_bits) > 0:
            bd = int(raw_bits)
        else:
            fmt_base = fmt.rstrip("p")
            if fmt_base == "s16":
                bd = 16
            elif fmt_base == "s24":
                bd = 24
            elif fmt_base in ("s32", "flt"):
                bd = 32
            elif fmt_base in ("s64", "dbl"):
                bd = 64
            elif fmt_base == "u8":
                bd = 8
            else:
                bd = None

        is_lossless = codec in LOSSLESS_CODECS or codec.startswith("pcm_")

        return AudioStreamInfo(
            codec_name=codec,
            sample_rate=sr,
            channels=ch,
            channel_layout=layout,
            sample_fmt=fmt,
            bit_depth=bd,
            is_lossless=is_lossless
        )
    except Exception:
        return None


def get_canonical_pcm_format(info_a: AudioStreamInfo, info_b: AudioStreamInfo) -> Optional[str]:
    """
    Deterministically selects canonical PCM format ONLY if both audio streams are compatible
    without losing information (no downmixing, no sample-rate alteration, matching bit depth).
    Returns format string (e.g. 's16le', 's24le', 's32le', 's64le', 'f32le', 'f64le', 'u8') or None if incompatible.
    """
    # Reject mismatch in channel count or non-positive channels
    if info_a.channels <= 0 or info_b.channels <= 0 or info_a.channels != info_b.channels:
        return None

    # Reject channel layout mismatch
    if info_a.channel_layout and info_b.channel_layout and info_a.channel_layout != info_b.channel_layout:
        return None

    # Reject sample rate mismatch
    if info_a.sample_rate <= 0 or info_b.sample_rate <= 0 or info_a.sample_rate != info_b.sample_rate:
        return None

    # Reject mixed lossy vs lossless (e.g. MP3 vs FLAC belongs to acoustic comparator)
    if info_a.is_lossless != info_b.is_lossless:
        return None

    # Require verifiable bit depth on both sides
    if info_a.bit_depth is None or info_b.bit_depth is None:
        return None
    if info_a.bit_depth <= 0 or info_b.bit_depth <= 0:
        return None

    # Reject bit depth mismatch
    if info_a.bit_depth != info_b.bit_depth:
        return None

    # If lossy, codecs must be identical
    if not info_a.is_lossless:
        if info_a.codec_name != info_b.codec_name:
            return None

    fmt_a = (info_a.sample_fmt or "").lower().rstrip("p")
    fmt_b = (info_b.sample_fmt or "").lower().rstrip("p")

    # Define supported exact canonical mappings: (fmt_base, bit_depth) -> ffmpeg_format
    supported_pairs = {
        ("s16", 16): "s16le",
        ("s32", 24): "s24le",
        ("s24", 24): "s24le",
        ("s32", 32): "s32le",
        ("s64", 64): "s64le",
        ("flt", 32): "f32le",
        ("dbl", 64): "f64le",
        ("u8", 8): "u8",
    }

    pair_a = (fmt_a, info_a.bit_depth)
    pair_b = (fmt_b, info_b.bit_depth)

    # If formats differ (e.g. float vs integer) reject immediately
    if fmt_a != fmt_b:
        # Special safe exception: s32/24 and s24/24 are both 24-bit PCM integer representations
        if not ({fmt_a, fmt_b}.issubset({"s32", "s24"}) and info_a.bit_depth == 24):
            return None

    if pair_a not in supported_pairs:
        return None
    if pair_b not in supported_pairs:
        return None

    canonical_a = supported_pairs[pair_a]
    canonical_b = supported_pairs[pair_b]

    if canonical_a != canonical_b:
        return None

    return canonical_a


def verify_full_normalized_pcm_match(
    filepath_a: str,
    filepath_b: str,
    sample_rate: Optional[int] = None,
    chunk_size: int = 65536,
    **kwargs
) -> bool:
    """
    Strictly verifies full decoded PCM equivalence between two audio files.
    Preserves all channel and sample-rate information (no downmix, no forced resampling).
    Returns False immediately if streams are incompatible or differ at any sample.
    """
    if not filepath_a or not filepath_b:
        return False
    if not os.path.isfile(filepath_a) or not os.path.isfile(filepath_b):
        return False

    info_a = get_audio_stream_info(filepath_a)
    info_b = get_audio_stream_info(filepath_b)
    if not info_a or not info_b:
        return False

    canonical_fmt = get_canonical_pcm_format(info_a, info_b)
    if not canonical_fmt:
        # Incompatible streams: reject strict EXACT_AUDIO
        return False

    cmd_a = [
        "ffmpeg", "-v", "quiet", "-nostdin",
        "-i", filepath_a,
        "-f", canonical_fmt,
        "-"
    ]
    cmd_b = [
        "ffmpeg", "-v", "quiet", "-nostdin",
        "-i", filepath_b,
        "-f", canonical_fmt,
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
                terminate_process_tree(p)


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
