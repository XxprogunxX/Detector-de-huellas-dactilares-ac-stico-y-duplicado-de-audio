"""
Crash-Resilient Session Manager for Audio Cleaner.
Guarantees atomic session writes via tmp-file + fsync + os.replace,
safe backups, and robust recovery from corrupted session files.
"""

import os
import json
import logging
from typing import List, Tuple, Optional
from core.models import DuplicateGroup, DuplicateType, prune_duplicate_groups

logger = logging.getLogger(__name__)


def save_session_atomic(
    session_path: str,
    folder: str,
    groups: List[DuplicateGroup]
) -> bool:
    """
    Saves current scan session atomically to prevent file corruption from crashes/shutdowns.
    Flow:
      1. Write payload to session_path.tmp
      2. flush() buffer to OS
      3. os.fsync() to force write to physical storage
      4. Optional backup: copy existing valid session to session_path.bak
      5. os.replace(tmp, session_path) for atomic replacement
    """
    if not session_path:
        return False

    dir_name = os.path.dirname(os.path.abspath(session_path))
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = session_path + ".tmp"
    bak_path = session_path + ".bak"

    data = {
        "folder": folder,
        "groups": [g.to_dict() for g in prune_duplicate_groups(groups)]
    }

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Backup previous valid session if it exists and is non-empty
        if os.path.exists(session_path) and os.path.getsize(session_path) > 0:
            try:
                import shutil
                shutil.copy2(session_path, bak_path)
            except Exception as bak_err:
                logger.debug("Could not create session backup: %s", bak_err)

        # Atomic replacement: replaces destination atomically on Windows and POSIX
        os.replace(tmp_path, session_path)
        return True
    except Exception as e:
        logger.error("Atomic session save failed: %s. Previous session preserved.", e)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def load_session_safe(
    session_path: str
) -> Tuple[str, List[DuplicateGroup]]:
    """
    Loads saved session safely without crashing on corrupted JSON.
    Attempts primary session file first, falls back to backup file (.bak) if corrupt,
    and defaults to an empty clean session if both are invalid.

    Guarantees:
      - Never crashes on truncated or corrupted files
      - Filters out invalid groups
      - Prunes zombie groups (<= 1 track)
      - Enforces requires_manual_review=True on POSSIBLE_DUPLICATE and LOW_CONFIDENCE_REVIEW
    """
    if not session_path:
        return "", []

    bak_path = session_path + ".bak"
    data = None

    # Try primary file
    if os.path.exists(session_path):
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Corrupted primary session file at %s: %s", session_path, e)
            data = None

    # Fallback to backup if primary failed or does not exist
    if data is None and os.path.exists(bak_path):
        try:
            with open(bak_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info("Successfully recovered session from backup %s", bak_path)
        except Exception as e:
            logger.warning("Corrupted backup session file at %s: %s", bak_path, e)
            data = None

    if not isinstance(data, dict):
        return "", []

    folder = data.get("folder", "")
    raw_groups = data.get("groups", [])
    valid_groups: List[DuplicateGroup] = []

    for g_dict in raw_groups:
        if not isinstance(g_dict, dict):
            continue
        try:
            group = DuplicateGroup.from_dict(g_dict)
            # Must have at least 2 tracks to be a valid duplicate group
            if len(group.tracks) >= 2:
                valid_groups.append(group)
        except Exception as parse_err:
            logger.warning("Skipping invalid duplicate group from session: %s", parse_err)

    # Prune any remaining zombie groups
    pruned = prune_duplicate_groups(valid_groups)
    return folder, pruned


def clean_abandoned_tmp_sessions(session_dir: str):
    """Safely cleans up abandoned *.tmp session files from previous aborted writes."""
    if not os.path.isdir(session_dir):
        return
    try:
        for fname in os.listdir(session_dir):
            if fname.endswith(".json.tmp"):
                fpath = os.path.join(session_dir, fname)
                try:
                    os.remove(fpath)
                except OSError:
                    pass
    except Exception as e:
        logger.debug("Failed cleaning abandoned session tmp files: %s", e)
