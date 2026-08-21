"""
Safe File Management, Review and Organization Operations.
"""

import os
import sys
import shutil
import subprocess
from typing import List, Tuple, Optional
from core.models import DuplicateGroup, AudioTrack, FileAction
from core.database import Database


def open_file_in_explorer(filepath: str):
    """Opens Windows Explorer with the target file highlighted."""
    if not os.path.exists(filepath):
        return
    if sys.platform == "win32":
        path = os.path.normpath(filepath)
        subprocess.run(f'explorer /select,"{path}"')
    else:
        # Cross-platform fallback
        subprocess.run(["xdg-open", os.path.dirname(filepath)])


def set_track_action_in_group(group: DuplicateGroup, filepath: str, action: FileAction):
    """
    Updates the action (KEEP / DELETE / UNSET) for a track in a group
    and refreshes the space savings.
    """
    for t in group.tracks:
        if t.filepath == filepath:
            t.action = action
            break
    group.recalculate_space_saving()


def auto_apply_recommendations(groups: List[DuplicateGroup]):
    """Sets recommended best track to KEEP and all others to DELETE across all groups."""
    for group in groups:
        for t in group.tracks:
            if t.filepath == group.best_track_path:
                t.action = FileAction.KEEP
            else:
                t.action = FileAction.DELETE
        group.recalculate_space_saving()


def move_marked_duplicates(
    groups: List[DuplicateGroup],
    destination_folder: str,
    db: Optional[Database] = None
) -> Tuple[int, int, List[str]]:
    """
    Moves all tracks marked DELETE to destination_folder.
    Preserves safety: Will NOT move tracks marked KEEP.
    
    Returns:
        (successful_moves_count, failed_count, log_messages)
    """
    os.makedirs(destination_folder, exist_ok=True)
    success = 0
    failed = 0
    logs = []

    for group in groups:
        # Safety check: Do not move if all tracks in group are marked DELETE
        keep_tracks = [t for t in group.tracks if t.action == FileAction.KEEP]
        if not keep_tracks and len(group.tracks) > 1:
            logs.append(f"⚠️ Grupo {group.group_id}: Omitido porque ningún archivo está marcado para conservar.")
            continue

        for track in list(group.tracks):
            if track.action == FileAction.DELETE:
                if not os.path.exists(track.filepath):
                    logs.append(f"Archivo no encontrado: {track.filepath}")
                    failed += 1
                    continue

                target_filename = track.filename
                target_path = os.path.join(destination_folder, target_filename)

                # Avoid collision in destination folder
                counter = 1
                base_name, ext = os.path.splitext(target_filename)
                while os.path.exists(target_path):
                    target_path = os.path.join(destination_folder, f"{base_name}_{counter}{ext}")
                    counter += 1

                try:
                    shutil.move(track.filepath, target_path)
                    if db:
                        db.delete_track(track.filepath)
                    logs.append(f"Movido: {track.filename} -> {os.path.basename(target_path)}")
                    group.tracks.remove(track)
                    success += 1
                except Exception as e:
                    logs.append(f"Error al mover {track.filename}: {e}")
                    failed += 1

        group.recalculate_space_saving()

    return success, failed, logs


def delete_marked_duplicates_permanently(
    groups: List[DuplicateGroup],
    db: Optional[Database] = None
) -> Tuple[int, int, List[str]]:
    """
    Safely deletes all tracks marked DELETE permanently after user confirmation.
    Safety Guard: Never deletes a track marked KEEP or every track in a group.
    
    Returns:
        (deleted_count, failed_count, log_messages)
    """
    deleted = 0
    failed = 0
    logs = []

    for group in groups:
        # Strict safety check: Ensure at least one track is retained
        remaining_retained = [t for t in group.tracks if t.action != FileAction.DELETE]
        if not remaining_retained and len(group.tracks) > 0:
            logs.append(f"⚠️ Grupo {group.group_id}: Bloqueada eliminación porque no queda ninguna copia para conservar.")
            continue

        for track in list(group.tracks):
            if track.action == FileAction.DELETE:
                if not os.path.exists(track.filepath):
                    failed += 1
                    continue

                try:
                    os.remove(track.filepath)
                    if db:
                        db.delete_track(track.filepath)
                    logs.append(f"Eliminado: {track.filepath}")
                    group.tracks.remove(track)
                    deleted += 1
                except Exception as e:
                    logs.append(f"Error eliminando {track.filename}: {e}")
                    failed += 1

        group.recalculate_space_saving()

    return deleted, failed, logs
