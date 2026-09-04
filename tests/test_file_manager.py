import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.models import DuplicateGroup, AudioTrack, DuplicateType, FileAction
from core.database import Database
from core.file_manager import (
    auto_apply_recommendations,
    FileOperationService,
    move_marked_duplicates,
    trash_marked_duplicates,
    delete_marked_duplicates_permanently
)


class TestFileManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="audioclean_fm_test_")
        self.db_path = os.path.join(self.test_dir, "test_music.db")
        self.db = Database(db_path=self.db_path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_dummy_file(self, filename: str, content: bytes = b"dummy audio data 12345") -> str:
        filepath = os.path.join(self.test_dir, filename)
        with open(filepath, "wb") as f:
            f.write(content)
        return filepath

    def test_low_confidence_review_never_auto_deletes(self):
        """Aislar grupos con requires_manual_review de auto_apply_recommendations."""
        t1 = AudioTrack(filepath="track1.mp3")
        t2 = AudioTrack(filepath="track2.mp3")
        t1.action = FileAction.UNSET
        t2.action = FileAction.UNSET
        
        group = DuplicateGroup(
            group_id="G1",
            primary_type=DuplicateType.LOW_CONFIDENCE_REVIEW,
            tracks=[t1, t2],
            best_track_path="",
            requires_manual_review=True
        )
        
        modified = auto_apply_recommendations([group])
        self.assertEqual(modified, 0, "No debe modificar grupos con requires_manual_review=True")
        for t in group.tracks:
            self.assertNotEqual(t.action, FileAction.DELETE)

    def test_k_auto_apply_recommendations_returns_integer(self):
        """TEST K: auto_apply_recommendations(...) debe retornar un entero correcto con grupos modificados."""
        t1 = AudioTrack(filepath="t1.mp3", filesize=1000)
        t2 = AudioTrack(filepath="t2.mp3", filesize=1000)
        g1 = DuplicateGroup(
            group_id="G1",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t1, t2],
            best_track_path="t1.mp3",
            requires_manual_review=False
        )

        t3 = AudioTrack(filepath="t3.mp3", filesize=1000)
        t4 = AudioTrack(filepath="t4.mp3", filesize=1000)
        g2 = DuplicateGroup(
            group_id="G2",
            primary_type=DuplicateType.POSSIBLE_DUPLICATE,
            tracks=[t3, t4],
            best_track_path="t3.mp3",
            requires_manual_review=True
        )

        modified = auto_apply_recommendations([g1, g2])
        self.assertIsInstance(modified, int)
        self.assertEqual(modified, 1, "Solo el grupo g1 (no protegido) debió ser modificado")
        self.assertEqual(t1.action, FileAction.KEEP)
        self.assertEqual(t2.action, FileAction.DELETE)
        # g2 debe seguir intacto
        self.assertEqual(t3.action, FileAction.UNSET)
        self.assertEqual(t4.action, FileAction.UNSET)

    def test_f_keep_delete_preserves_keep(self):
        """TEST F: Grupo con KEEP y DELETE: eliminar DELETE debe conservar la pista KEEP."""
        f_keep = self._create_dummy_file("song_best.mp3")
        f_del = self._create_dummy_file("song_copy.mp3")

        t_keep = AudioTrack(filepath=f_keep, filesize=1000, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=1000, action=FileAction.DELETE)

        group = DuplicateGroup(
            group_id="G_test",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_keep, t_del],
            best_track_path=f_keep
        )

        deleted, failed, logs = delete_marked_duplicates_permanently([group], db=self.db)
        self.assertEqual(deleted, 1)
        self.assertEqual(failed, 0)
        self.assertTrue(os.path.exists(f_keep), "La pista KEEP debe conservarse en disco")
        self.assertFalse(os.path.exists(f_del), "La pista DELETE debe haberse eliminado")
        self.assertEqual(len(group.tracks), 1)
        self.assertEqual(group.tracks[0].filepath, f_keep)

    def test_g_delete_all_tracks_blocked_by_safety_invariant(self):
        """TEST G: Intentar eliminar todas las pistas del grupo debe ser bloqueado."""
        f1 = self._create_dummy_file("song1.mp3")
        f2 = self._create_dummy_file("song2.mp3")

        t1 = AudioTrack(filepath=f1, filesize=1000, action=FileAction.DELETE)
        t2 = AudioTrack(filepath=f2, filesize=1000, action=FileAction.DELETE)

        group = DuplicateGroup(
            group_id="G_unsafe",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t1, t2],
            best_track_path=f1
        )

        deleted, failed, logs = delete_marked_duplicates_permanently([group], db=self.db)
        self.assertEqual(deleted, 0, "No debe eliminar ningún archivo si no queda copia para conservar")
        self.assertGreater(failed, 0)
        self.assertTrue(os.path.exists(f1), "El archivo 1 no debió eliminarse")
        self.assertTrue(os.path.exists(f2), "El archivo 2 no debió eliminarse")
        self.assertEqual(len(group.tracks), 2, "group.tracks debe mantenerse intacto")
        self.assertTrue(any("bloqueada" in l.lower() for l in logs))

    def test_h_keep_track_is_strictly_immune(self):
        """TEST H: Una pista marcada con KEEP nunca debe ser eliminada."""
        f_keep = self._create_dummy_file("immune.mp3")
        t_keep = AudioTrack(filepath=f_keep, filesize=1000, action=FileAction.KEEP)

        group = DuplicateGroup(
            group_id="G_immune",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_keep],
            best_track_path=f_keep
        )

        deleted, failed, logs = delete_marked_duplicates_permanently([group], db=self.db)
        self.assertEqual(deleted, 0)
        self.assertTrue(os.path.exists(f_keep))

    @patch("send2trash.send2trash")
    def test_i_send2trash_failure_leaves_model_and_db_untouched(self, mock_s2t):
        """TEST I: Si send2trash falla, la pista sigue en group.tracks, SQLite permanece y el error se reporta."""
        mock_s2t.side_effect = OSError("Simulated Permission Denied on Trash")

        f_keep = self._create_dummy_file("keep.mp3")
        f_trash = self._create_dummy_file("trash_target.mp3")

        t_keep = AudioTrack(filepath=f_keep, filesize=1000, action=FileAction.KEEP)
        t_trash = AudioTrack(filepath=f_trash, filesize=1000, action=FileAction.DELETE)

        self.db.upsert_track(t_keep)
        self.db.upsert_track(t_trash)

        group = DuplicateGroup(
            group_id="G_trash_fail",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_keep, t_trash],
            best_track_path=f_keep
        )

        success, failed, logs = trash_marked_duplicates([group], db=self.db)
        self.assertEqual(success, 0)
        self.assertEqual(failed, 1)
        self.assertTrue(os.path.exists(f_trash), "El archivo en disco debe continuar")
        self.assertEqual(len(group.tracks), 2, "La pista que falló no debe retirarse de group.tracks")
        self.assertIsNotNone(self.db.get_track(f_trash), "El registro SQLite debe permanecer intacto")
        self.assertTrue(any("Error" in l for l in logs))

    def test_j_backup_move_success_updates_filesystem_db_and_model(self):
        """TEST J: Backup/move exitoso actualiza filesystem, SQLite, group.tracks y ahorro de espacio."""
        backup_dir = os.path.join(self.test_dir, "backup_dest")

        f_keep = self._create_dummy_file("songA.mp3")
        f_del = self._create_dummy_file("songB.mp3")

        t_keep = AudioTrack(filepath=f_keep, filesize=5000, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=5000, action=FileAction.DELETE)

        self.db.upsert_track(t_keep)
        self.db.upsert_track(t_del)

        group = DuplicateGroup(
            group_id="G_backup_success",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_keep, t_del],
            best_track_path=f_keep
        )
        group.recalculate_space_saving()
        self.assertEqual(group.space_saving_bytes, 5000)

        success, failed, logs = move_marked_duplicates([group], backup_dir, db=self.db)
        self.assertEqual(success, 1)
        self.assertEqual(failed, 0)

        # Filesystem
        self.assertFalse(os.path.exists(f_del), "El archivo original debe haberse movido")
        moved_target = os.path.join(backup_dir, "songB.mp3")
        self.assertTrue(os.path.exists(moved_target), "El archivo debe encontrarse en la carpeta de backup")

        # SQLite
        self.assertIsNone(self.db.get_track(f_del), "El registro de la pista movida debe eliminarse de SQLite")
        self.assertIsNotNone(self.db.get_track(f_keep), "La pista conservada debe continuar en SQLite")

        # Model & recalculation
        self.assertEqual(len(group.tracks), 1, "group.tracks debe contener solo la pista conservada")
        self.assertEqual(group.tracks[0].filepath, f_keep)
        self.assertEqual(group.space_saving_bytes, 0, "El ahorro debe recalcularse a 0 al no haber más duplicados")


if __name__ == "__main__":
    unittest.main()
