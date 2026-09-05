"""
Safe File Management, Review and Organization Operations.
Centralized FileOperationService enforcing strict safety invariants across all deletion modes.
"""

import os
import sys
import shutil
import sqlite3
import uuid
import subprocess
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable, Dict, Any
from core.models import DuplicateGroup, AudioTrack, FileAction
from core.database import Database


class JournalError(Exception):
    """Raised when journal persistence operations fail."""
    pass


class OperationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


def is_volume_accessible(filepath: str) -> bool:
    """
    Checks if the filesystem volume, drive or root containing filepath is mounted and accessible.
    Prevents treating disconnected NAS, external USB drives or network shares as 'deleted files'.
    """
    if not filepath:
        return False
    try:
        abs_path = os.path.abspath(filepath)
        drive, _ = os.path.splitdrive(abs_path)
        if drive:
            drive_root = drive + os.path.sep
            return os.path.exists(drive_root)
        else:
            parent = os.path.dirname(abs_path)
            if os.path.exists(parent):
                return True
            return os.path.exists(os.path.sep)
    except Exception:
        return False


@dataclass
class OperationResult:
    """
    Structured outcome of a file management operation.
    Supports tuple unpacking (success, failed, logs) for 100% backwards compatibility.
    """
    success: int
    failed: int
    logs: List[str]
    blocked: int = 0
    status: OperationStatus = OperationStatus.SUCCESS
    partial_failures: int = 0
    reason: str = ""

    def __iter__(self):
        return iter((self.success, self.failed, self.logs))

    def __getitem__(self, index):
        return (self.success, self.failed, self.logs)[index]


class OperationJournal:
    """
    Durable, independent SQLite journal for filesystem operations.
    Stored separately from the main library database (operation_journal.db)
    to guarantee crash recovery and reconciliation even if library.db is locked or corrupted.
    States: PENDING -> FS_DONE -> COMPLETED (or FAILED / ABORTED).
    """
    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = db_path
        else:
            base_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AudioDuplicateDetector")
            try:
                os.makedirs(base_dir, exist_ok=True)
            except Exception:
                pass
            self.db_path = os.path.join(base_dir, "operation_journal.db")
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=FULL;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    def _init_db(self):
        conn = None
        try:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS operation_journal (
                        op_id TEXT PRIMARY KEY,
                        filepath TEXT NOT NULL,
                        action TEXT NOT NULL,
                        target_path TEXT,
                        state TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_op_journal_state ON operation_journal(state);")
                conn.commit()
        except Exception as e:
            raise JournalError(f"Fallo crítico al inicializar la base de datos del journal ({self.db_path}): {e}") from e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def record_pending(self, op_id: str, filepath: str, action: str, target_path: Optional[str] = None):
        conn = None
        try:
            now = datetime.now().isoformat()
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "INSERT INTO operation_journal (op_id, filepath, action, target_path, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (op_id, filepath, action, target_path or "", "PENDING", now, now)
                )
                conn.commit()
        except Exception as e:
            raise JournalError(f"Fallo al escribir estado PENDING en el journal para {filepath}: {e}") from e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def update_state(self, op_id: str, state: str):
        conn = None
        try:
            now = datetime.now().isoformat()
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "UPDATE operation_journal SET state = ?, updated_at = ? WHERE op_id = ?",
                    (state, now, op_id)
                )
                conn.commit()
        except Exception as e:
            raise JournalError(f"Fallo al actualizar estado a {state} en el journal (op_id={op_id}): {e}") from e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def get_incomplete_operations(self) -> List[Dict[str, Any]]:
        conn = None
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM operation_journal WHERE state IN ('PENDING', 'FS_DONE') ORDER BY created_at ASC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            raise JournalError(f"Fallo al consultar operaciones incompletas del journal: {e}") from e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def reconcile(self, db: Optional[Database] = None) -> List[str]:
        logs = []
        pending = self.get_incomplete_operations()
        for op in pending:
            op_id = op["op_id"]
            filepath = op["filepath"]
            action = op.get("action", "")
            target_path = op.get("target_path", "")
            state = op.get("state", "")

            # Safety check: Storage volume / drive accessibility
            if not is_volume_accessible(filepath):
                logs.append(
                    f"⚠️ Reconciliación omitida: El volumen o unidad para '{filepath}' no está accesible o está desconectado. "
                    f"Operación {op_id} conservada como {state} para prevenir purga errónea de la biblioteca."
                )
                continue

            if action == "backup":
                if target_path and not is_volume_accessible(target_path):
                    logs.append(
                        f"⚠️ Reconciliación omitida: El volumen destino de backup para '{target_path}' no está accesible. "
                        f"Operación {op_id} conservada como {state}."
                    )
                    continue

                if not os.path.exists(filepath):
                    if target_path and os.path.exists(target_path):
                        if db is not None:
                            try:
                                db.delete_track(filepath)
                            except Exception as e:
                                logs.append(f"Reconciliación pendiente: error al sincronizar SQLite para backup de {os.path.basename(filepath)}: {e}")
                                continue
                        self.update_state(op_id, "COMPLETED")
                        logs.append(f"Reconciliación exitosa: backup de {os.path.basename(filepath)} completado y registrado.")
                    else:
                        logs.append(f"⚠️ Reconciliación advertencia: archivo origen {filepath} ausente y destino {target_path} no encontrado.")
                        self.update_state(op_id, "FAILED")
                else:
                    self.update_state(op_id, "ABORTED")
                    logs.append(f"Reconciliación: backup pendiente para {os.path.basename(filepath)} descartado (archivo intacto en origen).")
            else:
                # trash or permanent
                if not os.path.exists(filepath):
                    if db is not None:
                        try:
                            db.delete_track(filepath)
                        except Exception as e:
                            logs.append(f"Reconciliación pendiente: error al intentar sincronizar SQLite para {os.path.basename(filepath)}: {e}")
                            continue
                    self.update_state(op_id, "COMPLETED")
                    logs.append(f"Reconciliación exitosa: archivo {os.path.basename(filepath)} confirmado ausente en disco (estado previo: {state}). Registro purgado.")
                else:
                    self.update_state(op_id, "ABORTED")
                    logs.append(f"Reconciliación: operación para {os.path.basename(filepath)} descartada (archivo aún presente en disco, estado previo: {state}). Marcado como ABORTED.")
        return logs


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
    3. Protección de revisión manual Fail-Closed: Grupos con requires_manual_review jamás se procesan
       a menos que allow_manual_review_bypass sea True explícitamente.
    4. Sincronización durable y Journaling: Operaciones en disco se registran en operation_journal.db independiente.
    5. No reportar éxito falso: Si la operación en disco triunfa pero la base de datos falla, se reporta como PARTIAL_FAILURE.
    """

    @classmethod
    def reconcile_pending_operations(
        cls,
        db: Optional[Database] = None,
        journal_path: Optional[str] = None
    ) -> List[str]:
        """
        Reconciles operations that completed on filesystem but failed to sync to SQLite.
        Should be called on application startup.
        """
        journal = OperationJournal(db_path=journal_path)
        return journal.reconcile(db=db)

    @classmethod
    def execute_operation(
        cls,
        groups: List[DuplicateGroup],
        mode: str,  # "backup", "trash", "permanent"
        destination_folder: Optional[str] = None,
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = False,
        pre_operation_hook: Optional[Callable[[str], None]] = None,
        journal_path: Optional[str] = None
    ) -> OperationResult:
        success = 0
        failed = 0
        blocked = 0
        partial_failures = 0
        logs: List[str] = []

        try:
            journal = OperationJournal(db_path=journal_path)
        except Exception as init_err:
            logs.append(f"Error crítico de seguridad: no se pudo inicializar Operation Journal: {init_err}")
            total_delete_requests = sum(
                len([t for t in g.tracks if t.action == FileAction.DELETE])
                for g in groups
            )
            return OperationResult(
                success=0,
                failed=total_delete_requests,
                blocked=total_delete_requests,
                logs=logs,
                status=OperationStatus.BLOCKED,
                reason=f"JOURNAL_INIT_FAILED: {init_err}"
            )

        mode = mode.lower()
        if mode == "backup":
            if not destination_folder:
                logs.append("Error: No se especificó carpeta destino para el backup.")
                return OperationResult(
                    success=0, failed=0, logs=logs, status=OperationStatus.FAILED, reason="DESTINATION_MISSING"
                )
            os.makedirs(destination_folder, exist_ok=True)

        for group in groups:
            # Regla 3: Aislamiento Fail-Closed por revisión manual
            if getattr(group, "requires_manual_review", False) and not allow_manual_review_bypass:
                blocked += 1
                logs.append(
                    f"⚠️ Grupo {group.group_id}: OPERACIÓN BLOQUEADA por política de seguridad "
                    f"(requires_manual_review=True). No se modificó ningún archivo. "
                    f"Requiere autorización explícita del usuario."
                )
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
                    if not os.path.exists(track.filepath):
                        logs.append(f"Archivo no encontrado en disco: {track.filepath}")
                        failed += 1
                        continue

                    # Hook desacoplado (ej. para detener reproducción antes de borrar en Windows)
                    if pre_operation_hook is not None:
                        try:
                            pre_operation_hook(track.filepath)
                        except Exception as hook_err:
                            logs.append(f"Aviso hook previo a operación ({track.filename}): {hook_err}")

                    resolved_target_path: Optional[str] = None
                    if mode == "backup":
                        target_filename = track.filename
                        candidate_path = os.path.join(destination_folder, target_filename)
                        counter = 1
                        base_name, ext = os.path.splitext(target_filename)
                        while os.path.exists(candidate_path):
                            candidate_path = os.path.join(destination_folder, f"{base_name}_{counter}{ext}")
                            counter += 1
                        resolved_target_path = candidate_path

                    op_id = str(uuid.uuid4())
                    # Fail-closed journal: debe registrar PENDING de forma duradera antes de tocar filesystem
                    try:
                        journal.record_pending(op_id, track.filepath, mode, target_path=resolved_target_path)
                    except Exception as j_err:
                        logs.append(
                            f"Error crítico de seguridad: Fallo al persistir PENDING en journal para {track.filename}: {j_err}. "
                            f"Operación abortada para este archivo (filesystem intacto)."
                        )
                        failed += 1
                        blocked += 1
                        continue

                    action_ok = False
                    log_msg = ""
                    try:
                        if mode == "backup":
                            shutil.move(track.filepath, resolved_target_path)
                            log_msg = f"Movido a backup: {track.filename} -> {os.path.basename(resolved_target_path)}"
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
                                try:
                                    journal.update_state(op_id, "ABORTED")
                                except Exception:
                                    pass
                                continue

                        elif mode == "permanent":
                            os.remove(track.filepath)
                            log_msg = f"Eliminado permanentemente: {track.filename}"
                            action_ok = True

                        else:
                            logs.append(f"Modo de operación desconocido: {mode}")
                            failed += 1
                            try:
                                journal.update_state(op_id, "ABORTED")
                            except Exception:
                                pass
                            continue

                    except Exception as e:
                        logs.append(f"Error procesando {track.filename}: {e}")
                        failed += 1
                        action_ok = False
                        try:
                            journal.update_state(op_id, "FAILED")
                        except Exception:
                            pass

                    # Sincronización solo tras éxito en sistema de archivos
                    if action_ok:
                        fs_done_ok = True
                        try:
                            journal.update_state(op_id, "FS_DONE")
                        except Exception as j_err:
                            fs_done_ok = False
                            partial_failures += 1
                            logs.append(
                                f"⚠️ Error de journal tras modificar filesystem para {track.filename}: {j_err}. "
                                f"Estado PENDING conservado para reconciliación posterior en el arranque."
                            )

                        db_ok = True
                        if db is not None:
                            try:
                                db.delete_track(track.filepath)
                            except Exception as db_err:
                                db_ok = False
                                partial_failures += 1
                                logs.append(
                                    f"⚠️ Aviso SQLite: archivo eliminado en disco pero no se pudo purgar de SQLite ({db_err}). "
                                    f"Estado persistido en Operation Journal para reconciliación automática."
                                )

                        if fs_done_ok and db_ok:
                            try:
                                journal.update_state(op_id, "COMPLETED")
                            except Exception as comp_err:
                                partial_failures += 1
                                logs.append(
                                    f"⚠️ Error al marcar COMPLETED en journal para {track.filename}: {comp_err}. "
                                    f"La próxima reconciliación cerrará el registro idempotentemente."
                                )

                        # Actualizar modelo en memoria
                        try:
                            group.tracks.remove(track)
                        except ValueError:
                            pass

                        # Si la pista eliminada era la principal, actualizar best_track_path
                        if group.best_track_path == track.filepath:
                            group.best_track_path = group.tracks[0].filepath if group.tracks else ""

                        if db_ok and fs_done_ok:
                            logs.append(log_msg)
                            success += 1

            group.recalculate_space_saving()

        # Determinar estado global con precedencia estricta
        if partial_failures > 0:
            status = OperationStatus.PARTIAL_FAILURE
            reason = "PARTIAL_FAILURE"
        elif success > 0 and failed > 0:
            status = OperationStatus.PARTIAL_FAILURE
            reason = "MIXED_SUCCESS_AND_FAILURE"
        elif success > 0 and blocked > 0:
            status = OperationStatus.PARTIAL_SUCCESS
            reason = "PARTIAL_SUCCESS"
        elif failed > 0:
            status = OperationStatus.FAILED
            reason = "FS_OPERATION_FAILED"
        elif blocked > 0:
            status = OperationStatus.BLOCKED
            reason = "MANUAL_REVIEW_REQUIRED"
        else:
            status = OperationStatus.SUCCESS
            reason = "OK"

        return OperationResult(
            success=success,
            failed=failed,
            logs=logs,
            blocked=blocked,
            status=status,
            partial_failures=partial_failures,
            reason=reason
        )

    @classmethod
    def backup(
        cls,
        groups: List[DuplicateGroup],
        destination_folder: str,
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = False,
        pre_operation_hook: Optional[Callable[[str], None]] = None,
        journal_path: Optional[str] = None
    ) -> OperationResult:
        return cls.execute_operation(
            groups, "backup", destination_folder=destination_folder, db=db,
            allow_manual_review_bypass=allow_manual_review_bypass,
            pre_operation_hook=pre_operation_hook,
            journal_path=journal_path
        )

    @classmethod
    def trash(
        cls,
        groups: List[DuplicateGroup],
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = False,
        pre_operation_hook: Optional[Callable[[str], None]] = None,
        journal_path: Optional[str] = None
    ) -> OperationResult:
        return cls.execute_operation(
            groups, "trash", db=db,
            allow_manual_review_bypass=allow_manual_review_bypass,
            pre_operation_hook=pre_operation_hook,
            journal_path=journal_path
        )

    @classmethod
    def delete_permanently(
        cls,
        groups: List[DuplicateGroup],
        db: Optional[Database] = None,
        allow_manual_review_bypass: bool = False,
        pre_operation_hook: Optional[Callable[[str], None]] = None,
        journal_path: Optional[str] = None
    ) -> OperationResult:
        return cls.execute_operation(
            groups, "permanent", db=db,
            allow_manual_review_bypass=allow_manual_review_bypass,
            pre_operation_hook=pre_operation_hook,
            journal_path=journal_path
        )


def move_marked_duplicates(
    groups: List[DuplicateGroup],
    destination_folder: str,
    db: Optional[Database] = None,
    allow_manual_review_bypass: bool = False,
    pre_operation_hook: Optional[Callable[[str], None]] = None,
    journal_path: Optional[str] = None
) -> OperationResult:
    """
    Moves all tracks marked DELETE to destination_folder.
    Preserves safety: Will NOT move tracks marked KEEP or delete the only remaining track.
    """
    return FileOperationService.backup(
        groups, destination_folder=destination_folder, db=db,
        allow_manual_review_bypass=allow_manual_review_bypass,
        pre_operation_hook=pre_operation_hook,
        journal_path=journal_path
    )


def trash_marked_duplicates(
    groups: List[DuplicateGroup],
    db: Optional[Database] = None,
    allow_manual_review_bypass: bool = False,
    pre_operation_hook: Optional[Callable[[str], None]] = None,
    journal_path: Optional[str] = None
) -> OperationResult:
    """
    Safely sends all tracks marked DELETE to the system Trash / Recycle Bin via Send2Trash.
    Shares the exact same safety invariants as backup and permanent delete.
    """
    return FileOperationService.trash(
        groups, db=db,
        allow_manual_review_bypass=allow_manual_review_bypass,
        pre_operation_hook=pre_operation_hook,
        journal_path=journal_path
    )


def delete_marked_duplicates_permanently(
    groups: List[DuplicateGroup],
    db: Optional[Database] = None,
    allow_manual_review_bypass: bool = False,
    pre_operation_hook: Optional[Callable[[str], None]] = None,
    journal_path: Optional[str] = None
) -> OperationResult:
    """
    Safely deletes all tracks marked DELETE permanently after user confirmation.
    Safety Guard: Never deletes a track marked KEEP or every track in a group.
    """
    return FileOperationService.delete_permanently(
        groups, db=db,
        allow_manual_review_bypass=allow_manual_review_bypass,
        pre_operation_hook=pre_operation_hook,
        journal_path=journal_path
    )
