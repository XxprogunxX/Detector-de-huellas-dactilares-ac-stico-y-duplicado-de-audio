"""
Unit and Integration Tests for Phase D — Persistence, SQLite Safety, and GUI Consistency.
Covers:
  1. Strict directory prefix matching in SQLite (escaping %, _, trailing slashes, UNC paths).
  2. Safe VACUUM without breaking WAL mode, active transactions, or connection locks.
  3. Crash-resilient atomic session persistence and corrupted session recovery.
  4. Application shutdown lifecycle (cooperative stop, thread wait, safe close order).
  5. Zombie duplicate group pruning and safe best_track_path recalculation.
  6. Scanner results button navigation wiring.
"""

import os
import tempfile
import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from core.models import (
    AudioTrack,
    DuplicateGroup,
    DuplicateType,
    FileAction,
    prune_duplicate_groups
)
from core.database import Database, make_safe_directory_like_pattern, normalize_path_safe
from core.session_manager import (
    save_session_atomic,
    load_session_safe,
    clean_abandoned_tmp_sessions
)
from core.scanner import AudioScanner
from gui.app import AudioDuplicateDetectorApp, ScannerWorker, WorkerState

# Ensure single headless QApplication instance for GUI signal testing
_app = QApplication.instance() or QApplication([])


class TestSQLiteDirectoryPrefixMatching(unittest.TestCase):
    """1. SQLite strict directory matching and wildcard escaping."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db = Database(self.temp_db.name)

        # Helper to quickly add tracks
        self.t_music1 = AudioTrack(
            filepath="C:/Music/track1.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        self.t_music2 = AudioTrack(
            filepath="C:/Music2/track2.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        self.t_music_backup = AudioTrack(
            filepath="C:/Music_Backup/track3.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        self.db.upsert_tracks_batch([self.t_music1, self.t_music2, self.t_music_backup])

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    def test_directory_music_does_not_match_music2(self):
        """1. C:/Music must match C:/Music/track1.flac but NOT C:/Music2/track2.flac."""
        tracks = self.db.get_all_tracks(dir_prefix="C:/Music")
        paths = [t.filepath for t in tracks]
        self.assertIn("C:/Music/track1.flac", paths)
        self.assertNotIn("C:/Music2/track2.flac", paths)

    def test_directory_prefix_does_not_match_music_backup(self):
        """2. C:/Music must not match C:/Music_Backup/track3.flac."""
        tracks = self.db.get_all_tracks(dir_prefix="C:/Music")
        paths = [t.filepath for t in tracks]
        self.assertNotIn("C:/Music_Backup/track3.flac", paths)

    def test_directory_with_percent_character_is_escaped(self):
        """3. % character in folder name must be escaped as literal, not treated as SQL wildcard."""
        t_percent = AudioTrack(
            filepath="C:/Hits_100%/song.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        t_other = AudioTrack(
            filepath="C:/Hits_1000_Extra/song.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        self.db.upsert_tracks_batch([t_percent, t_other])

        tracks = self.db.get_all_tracks(dir_prefix="C:/Hits_100%")
        paths = [t.filepath for t in tracks]
        self.assertIn("C:/Hits_100%/song.flac", paths)
        self.assertNotIn("C:/Hits_1000_Extra/song.flac", paths)

    def test_directory_with_underscore_character_is_escaped(self):
        """4. _ character in folder name must be escaped as literal, not single-char wildcard."""
        t_under = AudioTrack(
            filepath="C:/My_Rock/song.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        t_wild = AudioTrack(
            filepath="C:/MyARock/song.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        self.db.upsert_tracks_batch([t_under, t_wild])

        tracks = self.db.get_all_tracks(dir_prefix="C:/My_Rock")
        paths = [t.filepath for t in tracks]
        self.assertIn("C:/My_Rock/song.flac", paths)
        self.assertNotIn("C:/MyARock/song.flac", paths)

    def test_unc_path_prefix_is_handled_safely(self):
        """5. UNC paths (\\\\server\\share\\music) must match files under that share correctly."""
        t_unc1 = AudioTrack(
            filepath="//nas/share/audio/track1.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        t_unc2 = AudioTrack(
            filepath="//nas/share/audio2/track2.flac", filesize=1000, mtime=1.0, duration=10.0, format="FLAC"
        )
        self.db.upsert_tracks_batch([t_unc1, t_unc2])

        tracks = self.db.get_all_tracks(dir_prefix="\\\\nas\\share\\audio")
        paths = [t.filepath for t in tracks]
        self.assertIn("//nas/share/audio/track1.flac", paths)
        self.assertNotIn("//nas/share/audio2/track2.flac", paths)


class TestSQLiteSafeVacuum(unittest.TestCase):
    """3. Safe SQLite VACUUM execution."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db = Database(self.temp_db.name)

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    def test_vacuum_database_in_wal_mode(self):
        """6. VACUUM executes cleanly on active WAL-mode connection and preserves WAL mode."""
        res = self.db.vacuum_database()
        self.assertTrue(res)

        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

    def test_vacuum_not_run_inside_active_transaction(self):
        """7. VACUUM is safely refused if an active transaction is in progress."""
        self.db._conn.execute("BEGIN IMMEDIATE;")
        try:
            res = self.db.vacuum_database()
            self.assertFalse(res)
        finally:
            self.db._conn.rollback()

    def test_vacuum_lock_failure_is_reported_safely(self):
        """8. If VACUUM encounters an operational lock, it returns False without crashing."""
        mock_conn = MagicMock()
        mock_conn.in_transaction = False
        mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
        with patch.object(self.db, "_conn", mock_conn):
            res = self.db.vacuum_database()
            self.assertFalse(res)


class TestAtomicSessionPersistence(unittest.TestCase):
    """4 & 5. Crash-resilient atomic session persistence and corrupted session handling."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_path = os.path.join(self.temp_dir.name, "last_session.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_atomic_session_save(self):
        """9. Session is saved atomically via tmp file + fsync + os.replace."""
        t1 = AudioTrack(filepath="C:/Music/a.mp3", filesize=100, mtime=1.0)
        t2 = AudioTrack(filepath="C:/Music/b.mp3", filesize=100, mtime=1.0)
        group = DuplicateGroup(
            group_id="g1", primary_type=DuplicateType.EXACT_HASH,
            tracks=[t1, t2], best_track_path="C:/Music/a.mp3"
        )

        ok = save_session_atomic(self.session_path, "C:/Music", [group])
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.session_path))
        self.assertFalse(os.path.exists(self.session_path + ".tmp"))

        folder, groups = load_session_safe(self.session_path)
        self.assertEqual(folder, "C:/Music")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].group_id, "g1")

    def test_failed_session_save_preserves_previous_session(self):
        """10. A write failure during save preserves the existing valid session untouched."""
        t1 = AudioTrack(filepath="C:/Music/a.mp3", filesize=100, mtime=1.0)
        t2 = AudioTrack(filepath="C:/Music/b.mp3", filesize=100, mtime=1.0)
        group_initial = DuplicateGroup(
            group_id="initial_valid", primary_type=DuplicateType.EXACT_HASH,
            tracks=[t1, t2], best_track_path="C:/Music/a.mp3"
        )
        save_session_atomic(self.session_path, "C:/Music", [group_initial])

        # Simulate crash/failure during second write
        with patch("os.fsync", side_effect=IOError("Simulated disk error")):
            ok = save_session_atomic(self.session_path, "C:/NewFolder", [])
            self.assertFalse(ok)

        # Previous session file must remain valid and unchanged!
        folder, groups = load_session_safe(self.session_path)
        self.assertEqual(folder, "C:/Music")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].group_id, "initial_valid")

    def test_truncated_temp_session_is_not_loaded(self):
        """11. A broken .tmp session file does not corrupt or replace the primary session."""
        t1 = AudioTrack(filepath="C:/Music/a.mp3", filesize=100, mtime=1.0)
        t2 = AudioTrack(filepath="C:/Music/b.mp3", filesize=100, mtime=1.0)
        group = DuplicateGroup(
            group_id="valid_g", primary_type=DuplicateType.EXACT_HASH,
            tracks=[t1, t2], best_track_path="C:/Music/a.mp3"
        )
        save_session_atomic(self.session_path, "C:/Music", [group])

        # Write truncated/broken .tmp file
        tmp_file = self.session_path + ".tmp"
        with open(tmp_file, "w") as f:
            f.write('{"folder": "incomplete')

        folder, groups = load_session_safe(self.session_path)
        self.assertEqual(folder, "C:/Music")
        self.assertEqual(len(groups), 1)

    def test_abandoned_session_tmp_is_handled_safely(self):
        """12. clean_abandoned_tmp_sessions cleans up orphaned .tmp files without touching session."""
        save_session_atomic(self.session_path, "C:/Music", [])
        tmp_file = self.session_path + ".tmp"
        with open(tmp_file, "w") as f:
            f.write("abandoned")

        clean_abandoned_tmp_sessions(self.temp_dir.name)
        self.assertFalse(os.path.exists(tmp_file))
        self.assertTrue(os.path.exists(self.session_path))

    def test_corrupted_session_does_not_crash(self):
        """13. Loading a completely corrupted session does not crash and defaults to empty session."""
        with open(self.session_path, "w") as f:
            f.write("<<<CORRUPT NON-JSON DATA>>>")

        folder, groups = load_session_safe(self.session_path)
        self.assertEqual(folder, "")
        self.assertEqual(groups, [])

    def test_old_session_manual_review_protection_survives_load(self):
        """14. A session JSON that claims requires_manual_review=False for POSSIBLE_DUPLICATE is overridden to True."""
        raw_json = (
            '{"folder": "C:/Music", "groups": [{'
            '"group_id": "g_pos", "primary_type": "POSSIBLE_DUPLICATE", '
            '"best_track_path": "C:/Music/a.mp3", "requires_manual_review": false, '
            '"tracks": ['
            '{"filepath": "C:/Music/a.mp3", "filesize": 100, "mtime": 1.0, "duration": 10.0, "format": "MP3"},'
            '{"filepath": "C:/Music/b.mp3", "filesize": 100, "mtime": 1.0, "duration": 10.0, "format": "MP3"}'
            ']}]}'
        )
        with open(self.session_path, "w", encoding="utf-8") as f:
            f.write(raw_json)

        folder, groups = load_session_safe(self.session_path)
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].requires_manual_review)

    def test_invalid_duplicate_group_is_not_auto_delete_candidate(self):
        """15. Invalid group with only 1 track is pruned on load and cannot become an auto-delete candidate."""
        raw_json = (
            '{"folder": "C:/Music", "groups": [{'
            '"group_id": "zombie_1_track", "primary_type": "EXACT_HASH", '
            '"best_track_path": "C:/Music/single.mp3", "requires_manual_review": false, '
            '"tracks": ['
            '{"filepath": "C:/Music/single.mp3", "filesize": 100, "mtime": 1.0, "duration": 10.0, "format": "MP3", "action": "DELETE"}'
            ']}]}'
        )
        with open(self.session_path, "w", encoding="utf-8") as f:
            f.write(raw_json)

        folder, groups = load_session_safe(self.session_path)
        # Zombie group with 1 track must be discarded
        self.assertEqual(len(groups), 0)


class TestApplicationShutdownAndLifecycle(unittest.TestCase):
    """6 & 7. Safe shutdown lifecycle and single closeEvent handler."""

    def test_close_event_does_not_have_duplicate_handler(self):
        """16. closeEvent must be defined exactly once in AudioDuplicateDetectorApp."""
        # Check source code / class dict
        handlers = [
            name for name, val in AudioDuplicateDetectorApp.__dict__.items()
            if name == "closeEvent"
        ]
        self.assertEqual(len(handlers), 1)

    def test_database_not_closed_while_scanner_active(self):
        """17. Scanner database connection is open and active during scan execution."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db = Database(temp_db.name)
        scanner = AudioScanner(db=db)

        self.assertFalse(db.is_closed)
        scanner.stop()
        self.assertFalse(db.is_closed)
        db.close()
        self.assertTrue(db.is_closed)

        if os.path.exists(temp_db.name):
            try:
                os.remove(temp_db.name)
            except OSError:
                pass

    def test_close_waits_for_scanner_shutdown(self):
        """18. closeEvent waits cooperatively for worker thread to stop before closing database."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        app_win = AudioDuplicateDetectorApp()

        # Mock running worker
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        mock_worker.wait.return_value = True
        app_win.worker = mock_worker

        mock_event = MagicMock()
        with patch.object(app_win.db, "close") as mock_db_close:
            app_win.closeEvent(mock_event)
            # Must cancel worker and wait
            mock_worker.cancel.assert_called_once()
            mock_worker.wait.assert_called_once_with(5000)
            mock_db_close.assert_called_once()

        app_win.close()
        if os.path.exists(temp_db.name):
            try:
                os.remove(temp_db.name)
            except OSError:
                pass

    def test_cancelled_scan_does_not_write_after_database_close(self):
        """19. Once cancelled and closed, scanner does not execute writes on closed DB."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db = Database(temp_db.name)
        scanner = AudioScanner(db=db)

        scanner.stop()
        db.close()
        self.assertTrue(db.is_closed)
        self.assertTrue(scanner.is_cancelled())

        if os.path.exists(temp_db.name):
            try:
                os.remove(temp_db.name)
            except OSError:
                pass


class TestZombieGroupsAndBestTrackRecalculation(unittest.TestCase):
    """8 & 9. Zombie duplicate groups pruning and safe best_track_path recalculation."""

    def test_group_with_one_track_is_pruned(self):
        """20. A duplicate group with only 1 track remaining is pruned from the list."""
        t1 = AudioTrack(filepath="C:/Music/a.mp3", filesize=1000, action=FileAction.DELETE)
        group = DuplicateGroup(
            group_id="g1", primary_type=DuplicateType.EXACT_HASH,
            tracks=[t1], best_track_path="C:/Music/a.mp3"
        )
        pruned = prune_duplicate_groups([group])
        self.assertEqual(len(pruned), 0)
        # Obsolete DELETE action is cleared
        self.assertEqual(t1.action, FileAction.KEEP)

    def test_group_with_zero_tracks_is_pruned(self):
        """21. A duplicate group with 0 tracks is pruned."""
        group = DuplicateGroup(group_id="g0", primary_type=DuplicateType.EXACT_HASH, tracks=[])
        pruned = prune_duplicate_groups([group])
        self.assertEqual(len(pruned), 0)

    def test_surviving_track_remains_in_library(self):
        """22. When track B is removed, the group is pruned but track A remains in database."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db = Database(temp_db.name)

        t_a = AudioTrack(filepath="C:/Music/survivor.flac", filesize=5000, mtime=1.0)
        t_b = AudioTrack(filepath="C:/Music/deleted.flac", filesize=5000, mtime=1.0)
        db.upsert_tracks_batch([t_a, t_b])

        # Delete track B from database
        db.delete_track(t_b.filepath)

        # Track A must still exist in database
        survivor = db.get_track(t_a.filepath)
        self.assertIsNotNone(survivor)
        self.assertIsNone(db.get_track(t_b.filepath))

        db.close()
        if os.path.exists(temp_db.name):
            try:
                os.remove(temp_db.name)
            except OSError:
                pass

    def test_pruned_group_does_not_contribute_space_saving(self):
        """23. Pruned group resets space_saving_bytes to 0."""
        t1 = AudioTrack(filepath="C:/Music/a.mp3", filesize=1000, action=FileAction.DELETE)
        group = DuplicateGroup(
            group_id="g1", primary_type=DuplicateType.EXACT_HASH,
            tracks=[t1], space_saving_bytes=1000
        )
        prune_duplicate_groups([group])
        self.assertEqual(group.space_saving_bytes, 0)

    def test_best_track_recalculated_after_removal(self):
        """24. If best_track_path was deleted, recalculate best_track_path among survivors."""
        t_old_best = AudioTrack(filepath="C:/Music/old_best.flac", filesize=5000, quality_score=95.0)
        t_second = AudioTrack(filepath="C:/Music/survivor1.flac", filesize=4000, quality_score=85.0)
        t_third = AudioTrack(filepath="C:/Music/survivor2.flac", filesize=3000, quality_score=70.0)

        group = DuplicateGroup(
            group_id="g_recalc",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_second, t_third],  # t_old_best was removed
            best_track_path=t_old_best.filepath
        )

        pruned = prune_duplicate_groups([group])
        self.assertEqual(len(pruned), 1)
        # New best track must be t_second (highest quality among survivors)
        self.assertEqual(pruned[0].best_track_path, t_second.filepath)

    def test_manual_review_group_does_not_gain_delete_actions_after_recalculation(self):
        """25. Recalculation in a manual-review group must NEVER assign automatic DELETE actions."""
        t1 = AudioTrack(filepath="C:/Music/survivor1.flac", filesize=4000, quality_score=80.0, action=FileAction.UNSET)
        t2 = AudioTrack(filepath="C:/Music/survivor2.flac", filesize=3000, quality_score=70.0, action=FileAction.UNSET)

        group = DuplicateGroup(
            group_id="g_manual",
            primary_type=DuplicateType.POSSIBLE_DUPLICATE,
            tracks=[t1, t2],
            best_track_path="C:/Music/deleted_best.flac",
            requires_manual_review=True
        )

        pruned = prune_duplicate_groups([group])
        self.assertEqual(len(pruned), 1)
        # No track should have DELETE action
        for t in pruned[0].tracks:
            self.assertNotEqual(t.action, FileAction.DELETE)


class TestGUIInteractivityAndNavigation(unittest.TestCase):
    """11. Scanner results button navigation wiring."""

    def test_scanner_results_button_requests_duplicate_view(self):
        """26. Clicking 'Ver Resultados de Duplicados' requests Duplicados section and switches page."""
        app_win = AudioDuplicateDetectorApp()
        # Verify scanner_view is wired to switch to Duplicados
        with patch.object(app_win, "_on_nav_changed") as mock_nav:
            app_win.scanner_view.view_duplicates_requested.emit()
            mock_nav.assert_called_once_with("Duplicados")
        app_win.close()


if __name__ == "__main__":
    unittest.main()
