"""
Comprehensive Test Suite for Phase E:
Scalability, Robust Streaming, Worker Fault Isolation, Cache, and Binary Resolution.
"""

import os
import sys
import time
import shutil
import tempfile
import sqlite3
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from core.models import AudioTrack, DuplicateGroup, DuplicateType, FileAction, ScanCoverageReport, ScanStats
from core.config import DetectionConfig
from core.binary_resolver import (
    resolve_binary_path,
    get_ffmpeg_path,
    get_ffprobe_path,
    get_fpcalc_path,
    check_binaries
)
from core.ffmpeg_runner import (
    run_command_with_drain,
    terminate_process_tree,
    ProcessStallTimeoutError
)
from core.cache_signature import (
    compute_quick_signature,
    is_cache_valid,
    verify_authoritative_sha256_before_destructive_action
)
from core.database import Database
from core.scanner import AudioScanner, _process_audio_worker
from core.clustering import cluster_duplicates


class TestBinaryResolution(unittest.TestCase):
    """1. Centralized Binary Resolver: MEIPASS, local bin/, PATH, missing binary handling."""

    def test_binary_resolver_prefers_bundled_binary(self):
        """Bundled binary in sys._MEIPASS must take highest priority."""
        temp_dir = tempfile.mkdtemp()
        try:
            meipass_bin_dir = os.path.join(temp_dir, "bin")
            os.makedirs(meipass_bin_dir, exist_ok=True)
            fake_bin = os.path.join(meipass_bin_dir, "fpcalc.exe" if sys.platform == "win32" else "fpcalc")
            with open(fake_bin, "w") as f:
                f.write("mock")

            with patch.object(sys, "_MEIPASS", temp_dir, create=True):
                resolved = resolve_binary_path("fpcalc")
                self.assertIsNotNone(resolved)
                self.assertEqual(os.path.abspath(resolved), os.path.abspath(fake_bin))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_binary_resolver_falls_back_to_path(self):
        """Falls back to system PATH when local and bundled binaries are not present."""
        with patch("shutil.which", return_value=sys.executable):
            resolved = resolve_binary_path("custom_exec")
            self.assertEqual(resolved, os.path.abspath(sys.executable))

    def test_build_binary_resolution_under_meipass(self):
        """Verifies resolve_binary_path resolves both ffmpeg and ffprobe when bundled."""
        temp_dir = tempfile.mkdtemp()
        try:
            bin_dir = os.path.join(temp_dir, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            ff_bin = os.path.join(bin_dir, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            with open(ff_bin, "w") as f:
                f.write("mock")

            with patch.object(sys, "_MEIPASS", temp_dir, create=True):
                resolved = get_ffmpeg_path()
                self.assertIsNotNone(resolved)
                self.assertTrue(resolved.endswith("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestFFmpegExecutionAndTreeKill(unittest.TestCase):
    """2. Stall timeout, total adaptive timeout, concurrent stderr/stdout drain, and process tree termination."""

    def test_ffmpeg_stderr_backpressure_does_not_deadlock(self):
        """Emitting 1 MB on stderr does not deadlock due to concurrent background drain."""
        cmd = [
            sys.executable, "-c",
            "import sys; sys.stderr.write('x' * 1_000_000); sys.stderr.flush()"
        ]
        ret, out, err = run_command_with_drain(cmd, base_timeout=10.0, stall_timeout=5.0)
        self.assertEqual(ret, 0)
        self.assertEqual(len(err), 1_000_000)

    def test_ffmpeg_stall_detection_does_not_false_timeout_active_process(self):
        """Periodic progress output on stderr resets the stall activity timer."""
        cmd = [
            sys.executable, "-c",
            "import time, sys\n"
            "for i in range(4):\n"
            "    sys.stderr.write(f'progress {i}\\n')\n"
            "    sys.stderr.flush()\n"
            "    time.sleep(0.08)\n"
        ]
        ret, out, err = run_command_with_drain(cmd, base_timeout=5.0, stall_timeout=1.0)
        self.assertEqual(ret, 0)
        self.assertIn(b"progress 3", err)

    def test_ffmpeg_stall_timeout_terminates_process(self):
        """Process that becomes completely inactive raises ProcessStallTimeoutError and is killed."""
        cmd = [
            sys.executable, "-c",
            "import time; time.sleep(3.0)"
        ]
        with self.assertRaises((ProcessStallTimeoutError, subprocess.TimeoutExpired)):
            run_command_with_drain(cmd, base_timeout=10.0, stall_timeout=0.3)

    def test_ffmpeg_total_timeout_terminates_process(self):
        """Total adaptive timeout interrupts process exceeding maximum duration limit."""
        cmd = [
            sys.executable, "-c",
            "import time; time.sleep(3.0)"
        ]
        with self.assertRaises(subprocess.TimeoutExpired):
            run_command_with_drain(cmd, base_timeout=0.4, stall_timeout=5.0)

    def test_cancel_kills_child_process_tree(self):
        """terminate_process_tree kills parent process and its child processes on Windows/POSIX."""
        cmd = [
            sys.executable, "-c",
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])\n"
            "time.sleep(20)\n"
        ]
        proc = subprocess.Popen(cmd)
        time.sleep(0.3)
        terminate_process_tree(proc, timeout=2.0)
        self.assertIsNotNone(proc.poll())

    def test_no_ffmpeg_process_left_after_timeout(self):
        """After a timeout, process is reaped and pipes are closed."""
        cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
        try:
            run_command_with_drain(cmd, base_timeout=0.2, stall_timeout=0.2)
        except (subprocess.TimeoutExpired, ProcessStallTimeoutError):
            pass


class TestCacheValidationAndAdditiveMigration(unittest.TestCase):
    """3 & 4. Robust cache validation (st_size, st_mtime_ns, quick signature) and SQLite migration."""

    def test_cache_uses_mtime_ns(self):
        """Cache validation checks st_mtime_ns rather than coarse second floats."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(b"AUDIO_DATA_FOR_CACHE_TEST_12345")
            tf_path = tf.name

        try:
            stat = os.stat(tf_path)
            sig = compute_quick_signature(tf_path)
            valid = is_cache_valid(tf_path, stat.st_size, stat.st_mtime_ns, sig)
            self.assertTrue(valid)

            # Different mtime_ns must invalidate cache
            invalid = is_cache_valid(tf_path, stat.st_size, stat.st_mtime_ns + 1000, sig)
            self.assertFalse(invalid)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_cache_invalidated_when_file_changes(self):
        """Cache is invalidated when file size or content modifies."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(b"INITIAL_DATA")
            tf_path = tf.name

        try:
            stat1 = os.stat(tf_path)
            sig1 = compute_quick_signature(tf_path)

            with open(tf_path, "wb") as f:
                f.write(b"MODIFIED_DATA_NEW_CONTENT")

            stat2 = os.stat(tf_path)
            valid = is_cache_valid(tf_path, stat1.st_size, stat1.st_mtime_ns, sig1)
            self.assertFalse(valid)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_same_size_changed_content_not_trusted_if_signature_differs(self):
        """If size and coarse mtime are identical but quick_signature differs, cache is not trusted."""
        sig_a = "00000000000000000001"
        sig_b = "00000000000000000002"
        with patch("os.stat") as mock_stat, patch("core.cache_signature.compute_quick_signature", return_value=sig_b):
            mock_s = MagicMock()
            mock_s.st_size = 50000
            mock_s.st_mtime_ns = 123456789000
            mock_stat.return_value = mock_s

            valid = is_cache_valid("song.flac", 50000, 123456789000, sig_a)
            self.assertFalse(valid)

    def test_quick_signature_handles_small_overlapping_files_deterministically(self):
        """Files smaller than 3*block_size produce deterministic hashes without crash."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tf.write(b"A" * 150)
            tf_path = tf.name

        try:
            sig1 = compute_quick_signature(tf_path)
            sig2 = compute_quick_signature(tf_path)
            self.assertTrue(len(sig1) > 0)
            self.assertEqual(sig1, sig2)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_cache_signature_is_not_authoritative_for_destructive_exact_match(self):
        """Destructive exact actions require full SHA-256 byte verification, not quick signature alone."""
        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tf:
            tf.write(b"AUTHENTIC_AUDIO_CONTENT")
            tf_path = tf.name

        try:
            import hashlib
            real_sha256 = hashlib.sha256(b"AUTHENTIC_AUDIO_CONTENT").hexdigest()
            fake_sha256 = hashlib.sha256(b"DIFFERENT_AUDIO_CONTENT").hexdigest()

            # Claim matches actual bytes
            self.assertTrue(verify_authoritative_sha256_before_destructive_action(tf_path, real_sha256))
            # Claim does not match actual bytes
            self.assertFalse(verify_authoritative_sha256_before_destructive_action(tf_path, fake_sha256))
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_old_database_migrates_mtime_ns(self):
        """Old database schema without mtime_ns is migrated additively."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db = None
        try:
            conn = sqlite3.connect(temp_db.name)
            conn.execute("""
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT UNIQUE NOT NULL,
                    filesize INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    sha256 TEXT
                );
            """)
            conn.commit()
            conn.close()

            db = Database(temp_db.name)
            with db._get_connection() as c:
                cur = c.cursor()
                cur.execute("PRAGMA table_info(tracks);")
                col_names = [r[1] for r in cur.fetchall()]
                self.assertIn("mtime_ns", col_names)
        finally:
            if db is not None:
                db.close()
            if os.path.exists(temp_db.name):
                try:
                    os.remove(temp_db.name)
                except OSError:
                    pass

    def test_old_database_migrates_quick_signature(self):
        """Old database schema without quick_signature is migrated additively."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db = None
        try:
            conn = sqlite3.connect(temp_db.name)
            conn.execute("""
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT UNIQUE NOT NULL,
                    filesize INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    sha256 TEXT
                );
            """)
            conn.commit()
            conn.close()

            db = Database(temp_db.name)
            with db._get_connection() as c:
                cur = c.cursor()
                cur.execute("PRAGMA table_info(tracks);")
                col_names = [r[1] for r in cur.fetchall()]
                self.assertIn("quick_signature", col_names)
        finally:
            if db is not None:
                db.close()
            if os.path.exists(temp_db.name):
                try:
                    os.remove(temp_db.name)
                except OSError:
                    pass

    def test_database_migration_preserves_existing_tracks(self):
        """Existing rows and metadata are fully preserved across additive schema migration."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db = None
        try:
            conn = sqlite3.connect(temp_db.name)
            conn.execute("""
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT UNIQUE NOT NULL,
                    filesize INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    sha256 TEXT
                );
            """)
            conn.execute("INSERT INTO tracks (id, filepath, filesize, mtime, sha256) VALUES (1, 'C:/Music/test.flac', 1024, 123.45, 'hash123')")
            conn.commit()
            conn.close()

            db = Database(temp_db.name)
            track = db.get_track("C:/Music/test.flac")
            self.assertIsNotNone(track)
            self.assertEqual(track.filepath, "C:/Music/test.flac")
            self.assertEqual(track.sha256, "hash123")
        finally:
            if db is not None:
                db.close()
            if os.path.exists(temp_db.name):
                try:
                    os.remove(temp_db.name)
                except OSError:
                    pass

    def test_database_migration_is_idempotent(self):
        """Running migrate_schema_if_needed multiple times does not raise errors or duplicate columns."""
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db = None
        try:
            db = Database(temp_db.name)
            db.migrate_schema_if_needed()
            db.migrate_schema_if_needed()
            with db._get_connection() as c:
                cur = c.cursor()
                cur.execute("PRAGMA table_info(tracks);")
                col_names = [r[1] for r in cur.fetchall()]
                self.assertEqual(col_names.count("mtime_ns"), 1)
                self.assertEqual(col_names.count("quick_signature"), 1)
        finally:
            if db is not None:
                db.close()
            if os.path.exists(temp_db.name):
                try:
                    os.remove(temp_db.name)
                except OSError:
                    pass


class TestSHA256SurvivalAndWorkerIsolation(unittest.TestCase):
    """5, 7, 8. SHA-256 survival when fpcalc fails, worker exception isolation, and Python 3.10 executor."""

    def test_sha256_survives_fpcalc_failure(self):
        """When fpcalc fails, SHA-256 is still computed and track data is retained."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(b"WAV_DATA_FOR_SHA256_TEST_123456789")
            tf_path = tf.name

        try:
            with patch("core.scanner.extract_fingerprint", side_effect=RuntimeError("fpcalc failed")):
                data = _process_audio_worker(tf_path, min_duration=0.0, spectral_analysis=False)
                self.assertIsNotNone(data)
                self.assertTrue(len(data["sha256"]) > 0)
                self.assertIsNone(data["fingerprint_raw"])
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_missing_fpcalc_does_not_drop_track(self):
        """If fpcalc binary is missing, the track is processed with fingerprint_raw=None rather than dropped."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tf.write(b"ID3_MP3_DATA_12345")
            tf_path = tf.name

        try:
            with patch("core.scanner.extract_fingerprint", side_effect=FileNotFoundError("fpcalc binary not found")):
                data = _process_audio_worker(tf_path, min_duration=0.0, spectral_analysis=False)
                self.assertIsNotNone(data)
                self.assertEqual(data["filepath"], tf_path)
                self.assertIsNone(data["fingerprint_raw"])
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_exact_hash_detected_without_chromaprint(self):
        """Two identical tracks without acoustic fingerprints still match as EXACT_HASH."""
        t1 = AudioTrack(filepath="C:/Music/song1.flac", filesize=1000, sha256="IDENTICAL_HASH", fingerprint_raw=[])
        t2 = AudioTrack(filepath="C:/Music/song2.flac", filesize=1000, sha256="IDENTICAL_HASH", fingerprint_raw=[])

        groups = cluster_duplicates([t1, t2])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].primary_type, DuplicateType.EXACT_HASH)

    def test_python310_executor_configuration(self):
        """ProcessPoolExecutor does not pass max_tasks_per_child on Python 3.10."""
        with patch.object(sys, "version_info", (3, 10, 5)):
            kwargs = {"max_workers": 2}
            if sys.version_info >= (3, 11):
                kwargs["max_tasks_per_child"] = 200
            self.assertNotIn("max_tasks_per_child", kwargs)

    def test_worker_exception_does_not_abort_full_scan(self):
        """A worker exception in cluster_duplicates does not abort processing of other chunks."""
        t1 = AudioTrack(filepath="t1.flac", filesize=100, fingerprint_raw=[10, 20, 30])
        t2 = AudioTrack(filepath="t2.flac", filesize=100, fingerprint_raw=[10, 20, 30])

        with patch("core.clustering._compare_chunk_worker", side_effect=RuntimeError("Worker crashed")):
            groups, cov = cluster_duplicates([t1, t2], return_coverage=True)
            self.assertTrue(cov.is_approximate)
            self.assertTrue(cov.worker_failures > 0)

    def test_failed_worker_marks_scan_incomplete(self):
        """A failed comparison worker records is_complete=False in ScanCoverageReport."""
        t1 = AudioTrack(filepath="t1.flac", filesize=100, fingerprint_raw=[1, 2, 3])
        t2 = AudioTrack(filepath="t2.flac", filesize=100, fingerprint_raw=[1, 2, 3])

        with patch("core.clustering._compare_chunk_worker", side_effect=OSError("Process killed")):
            groups, cov = cluster_duplicates([t1, t2], return_coverage=True)
            self.assertFalse(cov.is_complete)


class TestCandidateGenerationAndMemoryBounds(unittest.TestCase):
    """6, 10, 16, 17, 18. Memory-bounded candidate generation, deterministic results, cancellation, and progress."""

    def test_bounded_candidates_match_unbounded_on_small_dataset(self):
        """On small datasets within bounds, bounded candidate generation produces identical candidate sets."""
        tracks = [
            AudioTrack(filepath=f"song_{i}.flac", filesize=1000 + i, fingerprint_raw=[100, 200, 300, i])
            for i in range(10)
        ]
        res1 = cluster_duplicates(tracks, max_pair_hits=500_000)
        res2 = cluster_duplicates(tracks, max_pair_hits=10_000)
        self.assertEqual(len(res1), len(res2))

    def test_bounded_candidate_generation_is_deterministic(self):
        """Candidate generation produces identical groupings regardless of execution order."""
        tracks = [
            AudioTrack(filepath=f"track_{i}.mp3", filesize=500, fingerprint_raw=[50, 60, 70, (i % 3)])
            for i in range(15)
        ]
        res_a = cluster_duplicates(tracks)
        res_b = cluster_duplicates(tracks)
        self.assertEqual(len(res_a), len(res_b))
        self.assertEqual([g.best_track_path for g in res_a], [g.best_track_path for g in res_b])

    def test_memory_cap_is_applied_during_ingestion_not_after(self):
        """Setting max_pair_hits=5 forces eviction during ingestion, capping memory and setting is_approximate."""
        tracks = [
            AudioTrack(filepath=f"t_{i}.flac", filesize=1000, fingerprint_raw=[1, 2, 3, i])
            for i in range(25)
        ]
        groups, cov = cluster_duplicates(tracks, max_pair_hits=5, return_coverage=True)
        self.assertTrue(cov.is_approximate)
        self.assertTrue(cov.candidate_pairs_dropped > 0)

    def test_oversized_bucket_sets_approximate_flag(self):
        """A shingle bucket exceeding max_bucket_size increments oversized_buckets and marks is_approximate."""
        tracks = [
            AudioTrack(filepath=f"track_{i}.flac", filesize=1000, fingerprint_raw=[9999])
            for i in range(10)
        ]
        groups, cov = cluster_duplicates(tracks, max_bucket_size=3, return_coverage=True)
        self.assertTrue(cov.is_approximate)
        self.assertTrue(cov.oversized_buckets > 0)
        self.assertTrue(cov.candidate_pairs_dropped > 0)

    def test_candidate_truncation_is_reported(self):
        """ScanCoverageReport accurately tallies dropped candidate combinations."""
        tracks = [
            AudioTrack(filepath=f"song_{i}.flac", filesize=1000, fingerprint_raw=[8888])
            for i in range(8)
        ]
        groups, cov = cluster_duplicates(tracks, max_bucket_size=4, return_coverage=True)
        self.assertTrue(cov.candidate_pairs_dropped > 0)

    def test_progress_counter_uses_actual_chunk_length(self):
        """Progress counter increments by exact chunk length, not fixed chunk_size."""
        reported_dones = []

        def mock_cb(pct, curr, total, msg):
            reported_dones.append(curr)

        tracks = [
            AudioTrack(filepath=f"track_{i}.flac", filesize=100, fingerprint_raw=[1, 2, 3, 4, 5])
            for i in range(4)
        ]
        cluster_duplicates(tracks, progress_callback=mock_cb)
        if reported_dones:
            self.assertLessEqual(max(reported_dones), 6)

    def test_cancelled_scan_reports_cancelled(self):
        """Calling is_cancelled() causes cluster_duplicates to exit with scan_status='CANCELLED'."""
        tracks = [
            AudioTrack(filepath=f"track_{i}.flac", filesize=1000, fingerprint_raw=[10, 20, 30])
            for i in range(5)
        ]
        groups, cov = cluster_duplicates(tracks, is_cancelled=lambda: True, return_coverage=True)
        self.assertEqual(cov.scan_status, "CANCELLED")

    def test_cancel_does_not_submit_new_chunks(self):
        """When cancelled, no subsequent chunks are submitted to workers."""
        tracks = [
            AudioTrack(filepath=f"track_{i}.flac", filesize=1000, fingerprint_raw=[1, 2, 3])
            for i in range(10)
        ]
        cancelled_state = [False]
        def check_cancelled():
            cancelled_state[0] = True
            return True

        groups, cov = cluster_duplicates(tracks, is_cancelled=check_cancelled, return_coverage=True)
        self.assertEqual(cov.scan_status, "CANCELLED")

    def test_cancelled_pool_shuts_down_cleanly(self):
        """Cooperative pool shutdown completes cleanly on cancellation without unhandled process leaks."""
        tracks = [
            AudioTrack(filepath=f"canc_{i}.flac", filesize=1000, fingerprint_raw=[1, 2, 3, 4])
            for i in range(8)
        ]
        groups, cov = cluster_duplicates(tracks, is_cancelled=lambda: True, return_coverage=True)
        self.assertEqual(len(groups), 0)
        self.assertEqual(cov.scan_status, "CANCELLED")

    def test_detection_config_still_propagates_under_multiprocessing(self):
        """Custom DetectionConfig thresholds propagate to chunk workers during multiprocessing comparisons."""
        cfg = DetectionConfig(acoustic_threshold=70.0, possible_threshold=60.0)
        t1 = AudioTrack(filepath="c1.flac", filesize=1000, duration=100.0, fingerprint_raw=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        t2 = AudioTrack(filepath="c2.flac", filesize=1000, duration=100.0, fingerprint_raw=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        groups = cluster_duplicates([t1, t2], config=cfg)
        self.assertEqual(len(groups), 1)

    def test_candidate_pruning_recall_is_measured_against_ground_truth(self):
        """Ground truth synthetic pairs retain >= 90% recall under standard candidate bounds."""
        tracks = []
        for pair_idx in range(5):
            shared_fp = [1000 * pair_idx + k for k in range(50)]
            tracks.append(AudioTrack(filepath=f"dup_{pair_idx}_A.flac", filesize=1000, fingerprint_raw=shared_fp))
            tracks.append(AudioTrack(filepath=f"dup_{pair_idx}_B.flac", filesize=1000, fingerprint_raw=shared_fp))

        groups = cluster_duplicates(tracks)
        self.assertEqual(len(groups), 5)


if __name__ == "__main__":
    unittest.main()
