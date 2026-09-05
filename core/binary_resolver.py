"""
Centralized binary resolver for external executables: ffmpeg, ffprobe, and fpcalc.
Resolution order:
1. PyInstaller bundled temp directory (sys._MEIPASS)
2. Local application bin/ directory relative to executable or project root
3. System PATH via shutil.which
"""

import os
import sys
import shutil
from typing import Optional, Dict, Tuple


def _get_binary_name(base_name: str) -> str:
    """Returns the platform-specific binary name (e.g., adds .exe on Windows)."""
    if sys.platform == "win32" and not base_name.lower().endswith(".exe"):
        return f"{base_name}.exe"
    return base_name


def resolve_binary_path(binary_name: str) -> Optional[str]:
    """
    Resolves the absolute path to a binary executable using a strict, documented priority.
    Returns None if the binary cannot be located, allowing callers to degrade gracefully.
    """
    exec_name = _get_binary_name(binary_name)

    # 1. PyInstaller bundled directory (sys._MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        meipass = getattr(sys, "_MEIPASS")
        meipass_bin = os.path.join(meipass, "bin", exec_name)
        if os.path.isfile(meipass_bin) and os.access(meipass_bin, os.X_OK if sys.platform != "win32" else os.F_OK):
            return os.path.abspath(meipass_bin)
        meipass_root = os.path.join(meipass, exec_name)
        if os.path.isfile(meipass_root) and os.access(meipass_root, os.X_OK if sys.platform != "win32" else os.F_OK):
            return os.path.abspath(meipass_root)

    # 2. Local bin/ directory relative to running script / executable or working directory
    base_dirs = []
    if getattr(sys, "frozen", False):
        base_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base_dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base_dirs.append(os.getcwd())

    for bdir in base_dirs:
        candidate_bin = os.path.join(bdir, "bin", exec_name)
        if os.path.isfile(candidate_bin):
            return os.path.abspath(candidate_bin)
        candidate_root = os.path.join(bdir, exec_name)
        if os.path.isfile(candidate_root):
            return os.path.abspath(candidate_root)

    # 3. System PATH
    path_hit = shutil.which(binary_name) or shutil.which(exec_name)
    if path_hit and os.path.isfile(path_hit):
        return os.path.abspath(path_hit)

    return None


def get_ffmpeg_path() -> Optional[str]:
    """Returns absolute path to ffmpeg binary, or None if unavailable."""
    return resolve_binary_path("ffmpeg")


def get_ffprobe_path() -> Optional[str]:
    """Returns absolute path to ffprobe binary, or None if unavailable."""
    return resolve_binary_path("ffprobe")


def get_fpcalc_path() -> Optional[str]:
    """Returns absolute path to fpcalc binary, or None if unavailable."""
    return resolve_binary_path("fpcalc")


def check_binaries() -> Dict[str, Tuple[bool, Optional[str]]]:
    """
    Checks the availability of all required external binaries.
    Returns a dict: {'ffmpeg': (available, path), 'ffprobe': (...), 'fpcalc': (...)}
    """
    return {
        "ffmpeg": (get_ffmpeg_path() is not None, get_ffmpeg_path()),
        "ffprobe": (get_ffprobe_path() is not None, get_ffprobe_path()),
        "fpcalc": (get_fpcalc_path() is not None, get_fpcalc_path()),
    }
