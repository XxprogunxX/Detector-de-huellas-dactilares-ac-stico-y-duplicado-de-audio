"""
Safe File Management, Review and Organization Operations.
Centralized FileOperationService enforcing strict safety invariants across all deletion modes.
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


def auto_apply_recommendations(groups: List[DuplicateGroup]) -> int:
    """
    Sets recommended best track to KEEP and all others to DELETE across all groups.
    Ignores groups requiring manual review.
    
    Returns:
        int: Number of groups successfully modified.
    """
    modified = 0
    for group in groups:
        if getattr(group, "requires_manual_review", False):
            continue
        if not group.best_track_path:
            continue

        changed = False
        for t in group.tracks:
            target_action = FileAction.KEEP if t.filepath == group.best_track_path else FileAction.DELETE
            if t.action != target_action:
                t.action = target_action
                changed = True

        if changed:
            group.recalculate_space_saving()
            modified += 1

    return modified


class FileOperationService:
    """
    Single source of truth for all duplicate file modifications (Backup, Trash, Permanent Deletion).

    Mandatory Safety Invariants:
    1. Prohibido eliminar/mover la única copia restante del grupo: Al menos una pista debe quedar conservada.
    2. Inmunidad para pistas [CONSERVAR]: Jamás se permite el borrado de archivos con acción KEEP.
    3. Protección de revisión manual: Grupos con requires_manual_review no se procesan automáticamente
       sin confirmación explícita del usuario.
    4. Sincronización post-éxito: group.tracks y SQLite solo se actualizan SI la operación en disco tuvo éxito.
    5. Aislamiento ante fallos: Una pista que falle no se retira del modelo ni de SQLite, y el error se registra.
    """

    @classmethod
    def execute_operation(
        cls,
        groups: List[DuplicateGroup],
        mode: str,  # "backup", "trash", "permanent"
        destination_folder: Optional[str] = None,
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = True
    ) -> Tuple[int, int, List[str]]:
        success = 0
        failed = 0
        logs: List[str] = []

        mode = mode.lower()
        if mode == "backup":
            if not destination_folder:
                logs.append("Error: No se especificó carpeta destino para el backup.")
                return 0, 0, logs
            os.makedirs(destination_folder, exist_ok=True)

        for group in groups:
            # Regla 3: Aislamiento por revisión manual
            if getattr(group, "requires_manual_review", False) and not allow_manual_review_bypass:
                logs.append(f"⚠️ Grupo {group.group_id}: Omitido porque requiere revisión manual explícita.")
                continue

            # Regla 1: Protección de copia única (debe quedar al menos una pista no marcada para DELETE)
            retained_tracks = [t for t in group.tracks if t.action != FileAction.DELETE]
            if not retained_tracks and len(group.tracks) > 0:
                logs.append(
                    f"⚠️ Grupo {group.group_id}: Operación bloqueada porque ninguna copia está marcada para conservar "
                    f"(se perderían todas las copias)."
                )
                failed += len([t for t in group.tracks if t.action == FileAction.DELETE])
                continue

            # Procesar pistas marcadas DELETE
            for track in list(group.tracks):
                if track.action == FileAction.DELETE:
                    # Regla 2: Inmunidad de KEEP
                    if track.action == FileAction.KEEP:
                        logs.append(f"⚠️ Seguridad: Pista {track.filename} marcada como KEEP, acción cancelada.")
                        continue

                    if not os.path.exists(track.filepath):
                        logs.append(f"Archivo no encontrado en disco: {track.filepath}")
                        failed += 1
                        continue

                    action_ok = False
                    log_msg = ""
                    try:
                        if mode == "backup":
                            target_filename = track.filename
                            target_path = os.path.join(destination_folder, target_filename)

                            # Evitar colisión en carpeta destino
                            counter = 1
                            base_name, ext = os.path.splitext(target_filename)
                            while os.path.exists(target_path):
                                target_path = os.path.join(destination_folder, f"{base_name}_{counter}{ext}")
                                counter += 1

                            shutil.move(track.filepath, target_path)
                            log_msg = f"Movido a backup: {track.filename} -> {os.path.basename(target_path)}"
                            action_ok = True

                        elif mode == "trash":
                            try:
                                import send2trash
                                send2trash.send2trash(track.filepath)
                                log_msg = f"Movido a Papelera: {track.filename}"
                                action_ok = True
                            except ImportError:
                                logs.append("Error crítico: El módulo 'send2trash' no está disponible en el entorno.")
                                failed += 1
                                continue

                        elif mode == "permanent":
                            os.remove(track.filepath)
                            log_msg = f"Eliminado permanentemente: {track.filename}"
                            action_ok = True

                        else:
                            logs.append(f"Modo de operación desconocido: {mode}")
                            failed += 1
                            continue

                    except Exception as e:
                        logs.append(f"Error procesando {track.filename}: {e}")
                        failed += 1
                        action_ok = False

                    # Sincronización solo tras éxito en sistema de archivos
                    if action_ok:
                        if db is not None:
                            try:
                                db.delete_track(track.filepath)
                            except Exception as db_err:
                                logs.append(f"Aviso SQLite: no se pudo eliminar registro de {track.filename}: {db_err}")

                        # Actualizar modelo en memoria
                        try:
                            group.tracks.remove(track)
                        except ValueError:
                            pass

                        logs.append(log_msg)
                        success += 1

            group.recalculate_space_saving()

        return success, failed, logs

    @classmethod
    def backup(
        cls,
        groups: List[DuplicateGroup],
        destination_folder: str,
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = True
    ) -> Tuple[int, int, List[str]]:
        return cls.execute_operation(
            groups, "backup", destination_folder=destination_folder, db=db,
            allow_manual_review_bypass=allow_manual_review_bypass
        )

    @classmethod
    def trash(
        cls,
        groups: List[DuplicateGroup],
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = True
    ) -> Tuple[int, int, List[str]]:
        return cls.execute_operation(
            groups, "trash", db=db,
            allow_manual_review_bypass=allow_manual_review_bypass
        )

    @classmethod
    def delete_permanently(
        cls,
        groups: List[DuplicateGroup],
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = True
    ) -> Tuple[int, int, List[str]]:
        return cls.execute_operation(
            groups, "permanent", db=db,
            allow_manual_review_bypass=allow_manual_review_bypass
        )


def move_marked_duplicates(
    groups: List[DuplicateGroup],
    destination_folder: str,
    db: Optional[Database] = None
) -> Tuple[int, int, List[str]]:
    """
    Moves all tracks marked DELETE to destination_folder.
    Preserves safety: Will NOT move tracks marked KEEP or delete the only remaining track.
    """
    return FileOperationService.backup(groups, destination_folder=destination_folder, db=db)


def trash_marked_duplicates(
    groups: List[DuplicateGroup],
    db: Optional[Database] = None
) -> Tuple[int, int, List[str]]:
    """
    Safely sends all tracks marked DELETE to the system Trash / Recycle Bin via Send2Trash.
    Shares the exact same safety invariants as backup and permanent delete.
    """
    return FileOperationService.trash(groups, db=db)


def delete_marked_duplicates_permanently(
    groups: List[DuplicateGroup],
    db: Optional[Database] = None
) -> Tuple[int, int, List[str]]:
    """
    Safely deletes all tracks marked DELETE permanently after user confirmation.
    Safety Guard: Never deletes a track marked KEEP or every track in a group.
    """
    return FileOperationService.delete_permanently(groups, db=db)
