"""
Regression and validation suite for Phase A (Safety and Consistency):
- AC-001: EXACT_AUDIO is strictly information-preserving (channels, sample rate, bit depth, ffprobe validation).
- AC-002: Duration Firewall prevents auto-deletion on duration gaps > 2.0s.
- AC-003: Fail-Closed manual review default and structured OperationResult.
- AC-004: Durable Operation Journal with FS_DONE/COMPLETED and startup reconciliation.
- Transitive clustering security: mixed safe and possible edges force entire group into manual review.
"""

import os
import sys
import math
import wave
import struct
import shutil
import sqlite3
import tempfile
import unittest
import subprocess
from unittest.mock import MagicMock, patch

from core.models import AudioTrack, DuplicateGroup, DuplicateType, FileAction
from core.database import Database
from core.fingerprint import (
    verify_full_normalized_pcm_match,
    get_audio_stream_info,
    get_canonical_pcm_format,
    compute_audio_pcm_hash,
    AudioStreamInfo
)
from core.comparator import compare_tracks
from core.clustering import cluster_duplicates
from core.file_manager import (
    FileOperationService,
    OperationStatus,
    OperationJournal,
    JournalError,
    OperationResult,
    delete_marked_duplicates_permanently
)


def create_pcm_wav(
    filepath: str,
    duration_sec: float = 1.0,
    sample_rate: int = 44100,
    channels: int = 2,
    freq_left: float = 440.0,
    freq_right: float = 440.0
):
    """Generates a synthetic PCM 16-bit WAV file with explicit channel frequencies."""
    with wave.open(filepath, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        total_samples = int(duration_sec * sample_rate)
        frames = bytearray()
        for i in range(total_samples):
            t = i / sample_rate
            val_l = int(16000.0 * math.sin(2.0 * math.pi * freq_left * t))
            if channels == 1:
                frames.extend(struct.pack("<h", val_l))
            else:
                val_r = int(16000.0 * math.sin(2.0 * math.pi * freq_right * t))
                frames.extend(struct.pack("<hh", val_l, val_r))
        wav.writeframes(frames)


class TestPhaseASafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="audioclean_phase_a_")

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.test_dir):
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        self.case_dir = tempfile.mkdtemp(dir=self.test_dir)
        self.db_path = os.path.join(self.case_dir, "test_library.db")
        self.journal_path = os.path.join(self.case_dir, "test_journal.db")
        self.db = Database(db_path=self.db_path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        if os.path.exists(self.case_dir):
            shutil.rmtree(self.case_dir, ignore_errors=True)

    # ─────────────────────────────────────────────────────────────────────────
    #  AC-001: Information-Preserving EXACT_AUDIO Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_exact_audio_same_pcm_flac_vs_wav(self):
        """FLAC and WAV with identical raw audio must match 100% and be EXACT_AUDIO."""
        wav_path = os.path.join(self.case_dir, "tone.wav")
        flac_path = os.path.join(self.case_dir, "tone.flac")
        create_pcm_wav(wav_path, duration_sec=1.0, sample_rate=44100, channels=2)

        # Convert to lossless FLAC
        res = subprocess.run(
            ["ffmpeg", "-y", "-v", "quiet", "-i", wav_path, "-c:a", "flac", flac_path],
            capture_output=True
        )
        self.assertEqual(res.returncode, 0, "ffmpeg must convert WAV to FLAC cleanly")

        # Stream info inspection
        info_wav = get_audio_stream_info(wav_path)
        info_flac = get_audio_stream_info(flac_path)
        self.assertIsNotNone(info_wav)
        self.assertIsNotNone(info_flac)
        self.assertEqual(info_wav.channels, info_flac.channels)
        self.assertEqual(info_wav.sample_rate, info_flac.sample_rate)

        # Direct PCM stream verification
        match = verify_full_normalized_pcm_match(wav_path, flac_path)
        self.assertTrue(match, "FLAC and WAV from the same lossless source must match PCM exactly")

        # Comparator integration
        hash_prefix = compute_audio_pcm_hash(wav_path, max_seconds=30.0)
        t_wav = AudioTrack(filepath=wav_path, duration=1.0, audio_hash=hash_prefix)
        t_flac = AudioTrack(filepath=flac_path, duration=1.0, audio_hash=hash_prefix)
        report = compare_tracks(t_wav, t_flac)

        self.assertEqual(report.classification, DuplicateType.EXACT_AUDIO)
        self.assertEqual(report.confidence, 100.0)
        self.assertTrue(report.is_exact_audio)
        self.assertFalse(report.requires_manual_review)

    def test_exact_audio_stereo_difference_rejected(self):
        """Audio files identical on left channel but differing on right channel must NOT be EXACT_AUDIO."""
        wav_a = os.path.join(self.case_dir, "stereo_a.wav")
        wav_b = os.path.join(self.case_dir, "stereo_b.wav")
        # A: Left=440Hz, Right=440Hz
        create_pcm_wav(wav_a, duration_sec=1.0, sample_rate=44100, channels=2, freq_left=440.0, freq_right=440.0)
        # B: Left=440Hz, Right=880Hz (Stereo channel difference!)
        create_pcm_wav(wav_b, duration_sec=1.0, sample_rate=44100, channels=2, freq_left=440.0, freq_right=880.0)

        match = verify_full_normalized_pcm_match(wav_a, wav_b)
        self.assertFalse(match, "Stereo channel difference must be preserved and cause verification to fail")

    def test_exact_audio_high_frequency_difference_rejected(self):
        """High frequency difference must NOT be erased by forced downsampling to 11025 Hz."""
        wav_a = os.path.join(self.case_dir, "hf_a.wav")
        wav_b = os.path.join(self.case_dir, "hf_b.wav")
        # Generate 44100 Hz files
        create_pcm_wav(wav_a, duration_sec=1.0, sample_rate=44100, channels=2, freq_left=440.0, freq_right=440.0)
        # Generate file B with 15 kHz high-frequency tone
        create_pcm_wav(wav_b, duration_sec=1.0, sample_rate=44100, channels=2, freq_left=15000.0, freq_right=15000.0)

        match = verify_full_normalized_pcm_match(wav_a, wav_b)
        self.assertFalse(match, "High frequency differences must not match")

    def test_exact_audio_bit_depth_mismatch_rejected(self):
        """16-bit vs 24-bit audio must be rejected as strict EXACT_AUDIO candidates."""
        wav_16 = os.path.join(self.case_dir, "tone_16.wav")
        wav_24 = os.path.join(self.case_dir, "tone_24.wav")
        create_pcm_wav(wav_16, duration_sec=1.0, sample_rate=44100, channels=2)

        subprocess.run(
            ["ffmpeg", "-y", "-v", "quiet", "-i", wav_16, "-c:a", "pcm_s24le", wav_24],
            capture_output=True
        )

        match = verify_full_normalized_pcm_match(wav_16, wav_24)
        self.assertFalse(match, "Different bit depths must be rejected for strict EXACT_AUDIO")

    def test_exact_audio_channel_layout_mismatch_rejected(self):
        """Mono vs Stereo must be rejected as strict EXACT_AUDIO."""
        wav_mono = os.path.join(self.case_dir, "mono.wav")
        wav_stereo = os.path.join(self.case_dir, "stereo.wav")
        create_pcm_wav(wav_mono, duration_sec=1.0, sample_rate=44100, channels=1)
        create_pcm_wav(wav_stereo, duration_sec=1.0, sample_rate=44100, channels=2)

        match = verify_full_normalized_pcm_match(wav_mono, wav_stereo)
        self.assertFalse(match, "Mono vs Stereo layout mismatch must be rejected for strict EXACT_AUDIO")

    def test_exact_audio_flac_vs_mp3_delegated_to_acoustic(self):
        """MP3 vs FLAC must be rejected for strict EXACT_AUDIO and delegated to acoustic comparator."""
        wav_path = os.path.join(self.case_dir, "source.wav")
        flac_path = os.path.join(self.case_dir, "source.flac")
        mp3_path = os.path.join(self.case_dir, "source.mp3")
        create_pcm_wav(wav_path, duration_sec=2.0, sample_rate=44100, channels=2)

        subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-i", wav_path, "-c:a", "flac", flac_path], capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-i", wav_path, "-c:a", "libmp3lame", mp3_path], capture_output=True)

        match = verify_full_normalized_pcm_match(flac_path, mp3_path)
        self.assertFalse(match, "Lossy (MP3) vs Lossless (FLAC) must never match as EXACT_AUDIO")

        hash_flac = compute_audio_pcm_hash(flac_path, max_seconds=30.0)
        t_flac = AudioTrack(filepath=flac_path, duration=2.0, audio_hash=hash_flac)
        t_mp3 = AudioTrack(filepath=mp3_path, duration=2.0, audio_hash=hash_flac)
        report = compare_tracks(t_flac, t_mp3)

        self.assertNotEqual(report.classification, DuplicateType.EXACT_AUDIO)
        self.assertFalse(report.is_exact_audio)

    # ─────────────────────────────────────────────────────────────────────────
    #  AC-002: Duration Firewall Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_duration_firewall_over_2s_forces_possible_duplicate(self):
        """Even with 100% matching Chromaprint, duration difference > 2.0s must force POSSIBLE_DUPLICATE."""
        # Synthetic fingerprints of 30 frames
        fake_fp = [1234567, 7654321, 9876543] * 10
        t_a = AudioTrack(
            filepath="song_radio.mp3",
            duration=200.0,
            fingerprint_raw=fake_fp,
            format="MP3",
            bitrate=320,
            samplerate=44100
        )
        t_b = AudioTrack(
            filepath="song_extended.mp3",
            duration=205.0,  # 5.0 seconds longer!
            fingerprint_raw=fake_fp,
            format="MP3",
            bitrate=320,
            samplerate=44100
        )

        report = compare_tracks(t_a, t_b)

        self.assertNotEqual(
            report.classification,
            DuplicateType.ACOUSTIC_DUPLICATE,
            "Duration firewall must forbid ACOUSTIC_DUPLICATE when duration diff > 2.0s"
        )
        self.assertEqual(report.classification, DuplicateType.POSSIBLE_DUPLICATE)
        self.assertTrue(report.requires_manual_review)
        self.assertTrue(any("Duration Firewall" in r for r in report.reasons))

    # ─────────────────────────────────────────────────────────────────────────
    #  AC-003: Fail-Closed File Operations & Structured Results
    # ─────────────────────────────────────────────────────────────────────────

    def test_manual_review_blocked_by_default(self):
        """FileOperationService must block groups with requires_manual_review by default (fail-closed)."""
        f_keep = os.path.join(self.case_dir, "keep.mp3")
        f_del = os.path.join(self.case_dir, "delete.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep audio")
        with open(f_del, "wb") as f: f.write(b"delete audio")

        t_keep = AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)

        group = DuplicateGroup(
            group_id="G_PROTECTED",
            primary_type=DuplicateType.POSSIBLE_DUPLICATE,
            tracks=[t_keep, t_del],
            best_track_path=f_keep,
            requires_manual_review=True
        )

        # Call with default allow_manual_review_bypass=False
        result = FileOperationService.delete_permanently([group], db=self.db, journal_path=self.journal_path)

        self.assertEqual(result.status, OperationStatus.BLOCKED)
        self.assertEqual(result.blocked, 1)
        self.assertEqual(result.success, 0)
        self.assertTrue(os.path.exists(f_del), "Target file must NOT be deleted when blocked")
        self.assertTrue(any("OPERACIÓN BLOQUEADA" in log for log in result.logs))

    def test_manual_review_bypass_requires_explicit_true(self):
        """When explicit bypass authorization is granted, deletion proceeds cleanly."""
        f_keep = os.path.join(self.case_dir, "keep2.mp3")
        f_del = os.path.join(self.case_dir, "delete2.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep audio")
        with open(f_del, "wb") as f: f.write(b"delete audio")

        t_keep = AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)

        group = DuplicateGroup(
            group_id="G_AUTHORIZED",
            primary_type=DuplicateType.POSSIBLE_DUPLICATE,
            tracks=[t_keep, t_del],
            best_track_path=f_keep,
            requires_manual_review=True
        )

        result = FileOperationService.delete_permanently(
            [group],
            db=self.db,
            allow_manual_review_bypass=True,
            journal_path=self.journal_path
        )

        self.assertEqual(result.status, OperationStatus.SUCCESS)
        self.assertEqual(result.success, 1)
        self.assertFalse(os.path.exists(f_del), "Target file must be deleted when user explicitly authorizes")
        self.assertTrue(os.path.exists(f_keep), "Keeper track must remain untouched")

    # ─────────────────────────────────────────────────────────────────────────
    #  AC-004: Operation Journal and Startup Reconciliation Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_fs_success_db_failure_recorded_as_partial_failure(self):
        """If file is deleted on disk but SQLite raises an error, operation must report PARTIAL_FAILURE."""
        f_keep = os.path.join(self.case_dir, "keep_part.mp3")
        f_del = os.path.join(self.case_dir, "del_part.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep audio")
        with open(f_del, "wb") as f: f.write(b"delete audio")

        t_keep = AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)

        group = DuplicateGroup(
            group_id="G_PARTIAL",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_keep, t_del],
            best_track_path=f_keep,
            requires_manual_review=False
        )

        # Mock database that fails on delete_track
        mock_db = MagicMock()
        mock_db.delete_track.side_effect = sqlite3.OperationalError("database is locked")

        result = FileOperationService.delete_permanently(
            [group],
            db=mock_db,
            journal_path=self.journal_path
        )

        # Must report partial failure, NOT complete success
        self.assertEqual(result.status, OperationStatus.PARTIAL_FAILURE)
        self.assertEqual(result.partial_failures, 1)
        self.assertEqual(result.success, 0)
        self.assertFalse(os.path.exists(f_del), "Filesystem deletion did occur")

        # Journal must have recorded FS_DONE state
        journal = OperationJournal(db_path=self.journal_path)
        incomplete = journal.get_incomplete_operations()
        self.assertEqual(len(incomplete), 1)
        self.assertEqual(incomplete[0]["state"], "FS_DONE")
        self.assertEqual(incomplete[0]["filepath"], f_del)

    def test_journal_reconciliation_on_startup(self):
        """Incomplete operations in the journal must be automatically reconciled on startup."""
        f_del = os.path.join(self.case_dir, "orphan_in_db.mp3")
        # Ensure file does not exist on disk
        if os.path.exists(f_del):
            os.remove(f_del)

        # Upsert track in library database
        track = AudioTrack(filepath=f_del, filesize=5000)
        self.db.upsert_track(track)
        self.assertIsNotNone(self.db.get_track(f_del), "Track must exist in DB before reconciliation")

        # Record FS_DONE in operation journal
        journal = OperationJournal(db_path=self.journal_path)
        journal.record_pending("op-rec-01", f_del, "permanent")
        journal.update_state("op-rec-01", "FS_DONE")

        # Run startup reconciliation
        logs = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)

        self.assertTrue(any("Reconciliación exitosa" in log for log in logs))
        self.assertIsNone(self.db.get_track(f_del), "Track must be purged from DB during reconciliation")
        self.assertEqual(len(journal.get_incomplete_operations()), 0, "Journal must have 0 incomplete operations")

    # ─────────────────────────────────────────────────────────────────────────
    #  Transitive Clustering Security Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_mixed_safe_and_possible_edges_make_entire_group_manual_review(self):
        """A cluster with one safe edge and one possible edge must fail-closed (requires_manual_review=True)."""
        fp_base = [11111, 22222, 33333] * 10
        # Track 1 & 2: Same duration, identical fingerprint -> safe edge
        t1 = AudioTrack(filepath="track1.mp3", duration=180.0, fingerprint_raw=fp_base, format="MP3", bitrate=320, filesize=1000)
        t2 = AudioTrack(filepath="track2.mp3", duration=180.0, fingerprint_raw=fp_base, format="MP3", bitrate=320, filesize=1000)
        # Track 3: Same fingerprint, but duration 185.0s (+5s) -> Duration Firewall flags POSSIBLE_DUPLICATE
        t3 = AudioTrack(filepath="track3.mp3", duration=185.0, fingerprint_raw=fp_base, format="MP3", bitrate=320, filesize=1000)

        groups = cluster_duplicates([t1, t2, t3])

        self.assertEqual(len(groups), 1, "The 3 tracks must cluster into 1 group")
        group = groups[0]

        # Fail-closed invariant:
        self.assertTrue(group.requires_manual_review, "Mixed cluster MUST require manual review")
        self.assertEqual(group.primary_type, DuplicateType.POSSIBLE_DUPLICATE)
        for t in group.tracks:
            self.assertEqual(t.action, FileAction.UNSET, "No tracks should be auto-set to DELETE in a mixed group")

    # ─────────────────────────────────────────────────────────────────────────
    #  Audit Requirements: Journal Fail-Closed, Crash Windows, PCM & GUI
    # ─────────────────────────────────────────────────────────────────────────

    def test_journal_write_failure_blocks_destructive_operation(self):
        """Failure to persist PENDING in the journal must block operation and NOT touch filesystem."""
        f_keep = os.path.join(self.case_dir, "keep_jw.mp3")
        f_del = os.path.join(self.case_dir, "del_jw.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep audio")
        with open(f_del, "wb") as f: f.write(b"delete audio")

        t_keep = AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)

        group = DuplicateGroup(
            group_id="G_JW_FAIL",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_keep, t_del],
            best_track_path=f_keep
        )

        with patch.object(OperationJournal, "record_pending", side_effect=JournalError("Simulated disk error")):
            result = FileOperationService.delete_permanently([group], db=self.db, journal_path=self.journal_path)

        self.assertTrue(os.path.exists(f_del), "Filesystem MUST remain untouched when journal write fails")
        self.assertNotEqual(result.status, OperationStatus.SUCCESS)
        self.assertEqual(result.success, 0)
        self.assertGreater(result.failed, 0)

    def test_journal_init_failure_blocks_destructive_operation(self):
        """Failure to initialize OperationJournal must fail-closed and leave all files untouched."""
        f_keep = os.path.join(self.case_dir, "keep_ji.mp3")
        f_del = os.path.join(self.case_dir, "del_ji.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep audio")
        with open(f_del, "wb") as f: f.write(b"delete audio")

        t_keep = AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)

        group = DuplicateGroup(
            group_id="G_JI_FAIL",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            tracks=[t_keep, t_del],
            best_track_path=f_keep
        )

        with patch.object(OperationJournal, "__init__", side_effect=JournalError("Cannot open SQLite journal")):
            result = FileOperationService.delete_permanently([group], db=self.db, journal_path=self.journal_path)

        self.assertTrue(os.path.exists(f_del), "Filesystem MUST remain untouched when journal init fails")
        self.assertEqual(result.status, OperationStatus.BLOCKED)
        self.assertEqual(result.success, 0)
        self.assertIn("JOURNAL_INIT_FAILED", result.reason)

    def test_crash_after_fs_before_fs_done_is_recoverable(self):
        """Crash between filesystem deletion and FS_DONE leaves PENDING; reconciliation must purge DB and mark COMPLETED."""
        f_del = os.path.join(self.case_dir, "crashed_post_fs.mp3")
        if os.path.exists(f_del):
            os.remove(f_del)

        track = AudioTrack(filepath=f_del, filesize=1000)
        self.db.upsert_track(track)
        self.assertIsNotNone(self.db.get_track(f_del))

        journal = OperationJournal(db_path=self.journal_path)
        journal.record_pending("op-crash-fs-01", f_del, "permanent")

        logs = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)

        self.assertIsNone(self.db.get_track(f_del), "DB record must be purged on reconciliation")
        incomplete = journal.get_incomplete_operations()
        self.assertEqual(len(incomplete), 0, "Journal must have transitioned to COMPLETED")

    def test_pending_operation_reconciled_on_startup(self):
        """Crash before filesystem deletion leaves PENDING; reconciliation must safely transition to ABORTED without modifying DB."""
        f_del = os.path.join(self.case_dir, "crashed_pre_fs.mp3")
        with open(f_del, "wb") as f: f.write(b"intact audio")

        track = AudioTrack(filepath=f_del, filesize=1000)
        self.db.upsert_track(track)

        journal = OperationJournal(db_path=self.journal_path)
        journal.record_pending("op-crash-pre-01", f_del, "permanent")

        logs = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)

        self.assertTrue(os.path.exists(f_del), "File must remain on disk")
        self.assertIsNotNone(self.db.get_track(f_del), "DB record must NOT be deleted if file is intact")
        incomplete = journal.get_incomplete_operations()
        self.assertEqual(len(incomplete), 0, "Journal record must have transitioned to ABORTED")

    def test_reconciliation_is_idempotent(self):
        """Running reconciliation multiple times must produce identical results without error or side-effects."""
        f_del = os.path.join(self.case_dir, "idempotent_test.mp3")
        if os.path.exists(f_del):
            os.remove(f_del)

        track = AudioTrack(filepath=f_del, filesize=500)
        self.db.upsert_track(track)

        journal = OperationJournal(db_path=self.journal_path)
        journal.record_pending("op-idem-01", f_del, "trash")

        # Run 1
        logs1 = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)
        self.assertIsNone(self.db.get_track(f_del))

        # Run 2
        logs2 = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)
        self.assertEqual(len(journal.get_incomplete_operations()), 0)
        self.assertEqual(len(logs2), 0, "Second reconciliation run must find 0 incomplete operations")

    def test_unknown_bit_depth_rejected_for_exact_audio(self):
        """Unknown or non-positive bit depth must reject EXACT_AUDIO canonical PCM mapping."""
        info_none = AudioStreamInfo("flac", 44100, 2, "stereo", "s16", None, True)
        info_valid = AudioStreamInfo("flac", 44100, 2, "stereo", "s16", 16, True)
        info_zero = AudioStreamInfo("flac", 44100, 2, "stereo", "s16", 0, True)

        self.assertIsNone(get_canonical_pcm_format(info_none, info_valid))
        self.assertIsNone(get_canonical_pcm_format(info_valid, info_none))
        self.assertIsNone(get_canonical_pcm_format(info_zero, info_zero))

    def test_pcm_float32_preserves_precision_or_is_rejected(self):
        """32-bit floating point PCM must map to f32le and never be downconverted to s16le."""
        info_a = AudioStreamInfo("pcm_f32le", 48000, 2, "stereo", "flt", 32, True)
        info_b = AudioStreamInfo("pcm_f32le", 48000, 2, "stereo", "flt", 32, True)
        fmt = get_canonical_pcm_format(info_a, info_b)
        self.assertEqual(fmt, "f32le")

    def test_pcm_float64_not_downconverted_to_s16(self):
        """64-bit float PCM must map to f64le and never be downconverted to s16le."""
        info_a = AudioStreamInfo("pcm_f64le", 48000, 2, "stereo", "dbl", 64, True)
        info_b = AudioStreamInfo("pcm_f64le", 48000, 2, "stereo", "dbl", 64, True)
        fmt = get_canonical_pcm_format(info_a, info_b)
        self.assertEqual(fmt, "f64le")
        self.assertNotEqual(fmt, "s16le")

    def test_20bit_pcm_not_downconverted_to_s16(self):
        """20-bit PCM cannot be preserved in byte-aligned canonical streaming without loss; must reject (return None)."""
        info_a = AudioStreamInfo("flac", 44100, 2, "stereo", "s32", 20, True)
        info_b = AudioStreamInfo("flac", 44100, 2, "stereo", "s32", 20, True)
        fmt = get_canonical_pcm_format(info_a, info_b)
        self.assertIsNone(fmt, "20-bit PCM must be rejected and delegated to acoustic comparator")

    def test_same_bit_depth_different_sample_fmt_rejected(self):
        """Same bit depth (32-bit) but incompatible format (s32 integer vs flt float) must be rejected."""
        info_int = AudioStreamInfo("pcm_s32le", 48000, 2, "stereo", "s32", 32, True)
        info_flt = AudioStreamInfo("pcm_f32le", 48000, 2, "stereo", "flt", 32, True)
        self.assertIsNone(get_canonical_pcm_format(info_int, info_flt))

    def test_gui_preserves_partial_failure_status(self):
        """DeleteModal.execute_action must preserve and return the structured OperationResult with PARTIAL_FAILURE."""
        try:
            from PyQt6.QtWidgets import QApplication
            from gui.components.delete_modal import DeleteModal
            _app = QApplication.instance() or QApplication(["test"])
        except Exception:
            self.skipTest("PyQt6 GUI unavailable in test environment")

        f_keep = os.path.join(self.case_dir, "keep_gui.mp3")
        f_del = os.path.join(self.case_dir, "del_gui.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep")
        with open(f_del, "wb") as f: f.write(b"del")

        t_keep = AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP)
        t_del = AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)
        group = DuplicateGroup("G_GUI", DuplicateType.ACOUSTIC_DUPLICATE, [t_keep, t_del], best_track_path=f_keep)

        expected_res = OperationResult(
            success=1,
            failed=1,
            logs=["Error DB"],
            blocked=0,
            status=OperationStatus.PARTIAL_FAILURE,
            partial_failures=1,
            reason="DB_SYNC_FAILED"
        )

        with patch.object(FileOperationService, "trash", return_value=expected_res):
            modal = DeleteModal([group], db=self.db)
            modal._selected_mode = "trash"
            result = modal.execute_action()

        self.assertIsInstance(result, OperationResult)
        self.assertEqual(result.status, OperationStatus.PARTIAL_FAILURE)
        self.assertEqual(result.partial_failures, 1)
        self.assertEqual(result.reason, "DB_SYNC_FAILED")

    def test_mixed_success_and_failure_not_reported_success(self):
        """When one operation succeeds and another fails, status must be PARTIAL_FAILURE, never SUCCESS."""
        f_keep = os.path.join(self.case_dir, "keep_mix.mp3")
        f_del_good = os.path.join(self.case_dir, "del_good.mp3")
        f_del_missing = os.path.join(self.case_dir, "del_missing_ghost.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep")
        with open(f_del_good, "wb") as f: f.write(b"good")

        t_keep = AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP)
        t_good = AudioTrack(filepath=f_del_good, filesize=10, action=FileAction.DELETE)
        t_bad = AudioTrack(filepath=f_del_missing, filesize=10, action=FileAction.DELETE)

        group = DuplicateGroup("G_MIX", DuplicateType.ACOUSTIC_DUPLICATE, [t_keep, t_good, t_bad], best_track_path=f_keep)
        result = FileOperationService.delete_permanently([group], db=self.db, journal_path=self.journal_path)

        self.assertEqual(result.success, 1)
        self.assertEqual(result.failed, 1)
        self.assertNotEqual(result.status, OperationStatus.SUCCESS)
        self.assertEqual(result.status, OperationStatus.PARTIAL_FAILURE)

    def test_mixed_success_and_blocked_not_reported_success(self):
        """When one group succeeds and another group is blocked by manual review, status must be PARTIAL_SUCCESS, not SUCCESS."""
        f1_keep = os.path.join(self.case_dir, "g1_keep.mp3")
        f1_del = os.path.join(self.case_dir, "g1_del.mp3")
        f2_keep = os.path.join(self.case_dir, "g2_keep.mp3")
        f2_del = os.path.join(self.case_dir, "g2_del.mp3")
        for f in (f1_keep, f1_del, f2_keep, f2_del):
            with open(f, "wb") as fp: fp.write(b"audio")

        g1 = DuplicateGroup(
            "G1_OK", DuplicateType.ACOUSTIC_DUPLICATE,
            [AudioTrack(filepath=f1_keep, filesize=10, action=FileAction.KEEP), AudioTrack(filepath=f1_del, filesize=10, action=FileAction.DELETE)],
            best_track_path=f1_keep, requires_manual_review=False
        )
        g2 = DuplicateGroup(
            "G2_BLK", DuplicateType.POSSIBLE_DUPLICATE,
            [AudioTrack(filepath=f2_keep, filesize=10, action=FileAction.KEEP), AudioTrack(filepath=f2_del, filesize=10, action=FileAction.DELETE)],
            best_track_path=f2_keep, requires_manual_review=True
        )

        result = FileOperationService.delete_permanently([g1, g2], db=self.db, allow_manual_review_bypass=False, journal_path=self.journal_path)

        self.assertEqual(result.success, 1)
        self.assertEqual(result.blocked, 1)
        self.assertNotEqual(result.status, OperationStatus.SUCCESS)
        self.assertEqual(result.status, OperationStatus.PARTIAL_SUCCESS)

    def test_backup_journal_records_exact_collision_resolved_target(self):
        """Backup journal record must store the actual collision-resolved filename (e.g. song_1.mp3), not just folder."""
        backup_dir = os.path.join(self.case_dir, "backup_dest")
        os.makedirs(backup_dir, exist_ok=True)

        existing_target = os.path.join(backup_dir, "track.mp3")
        with open(existing_target, "wb") as f: f.write(b"existing backup")

        f_keep = os.path.join(self.case_dir, "source_keep.mp3")
        f_source = os.path.join(self.case_dir, "track.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep")
        with open(f_source, "wb") as f: f.write(b"new track to backup")

        group = DuplicateGroup(
            "G_COLLISION", DuplicateType.ACOUSTIC_DUPLICATE,
            [AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP), AudioTrack(filepath=f_source, filesize=10, action=FileAction.DELETE)],
            best_track_path=f_keep
        )

        result = FileOperationService.backup([group], destination_folder=backup_dir, db=self.db, journal_path=self.journal_path)

        self.assertEqual(result.success, 1)
        expected_collision_path = os.path.join(backup_dir, "track_1.mp3")
        self.assertTrue(os.path.exists(expected_collision_path))

        journal = OperationJournal(db_path=self.journal_path)
        with journal._get_connection() as conn:
            row = conn.execute("SELECT target_path FROM operation_journal WHERE filepath = ?", (f_source,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(os.path.normpath(row[0]), os.path.normpath(expected_collision_path))

    def test_space_saving_all_unset_is_zero(self):
        """When all tracks in a group are UNSET, space_saving_bytes must be strictly 0."""
        t1 = AudioTrack(filepath="t1.mp3", filesize=5000, action=FileAction.UNSET)
        t2 = AudioTrack(filepath="t2.mp3", filesize=6000, action=FileAction.UNSET)
        group = DuplicateGroup("G_UNSET", DuplicateType.POSSIBLE_DUPLICATE, [t1, t2], best_track_path="t1.mp3")
        saving = group.recalculate_space_saving()
        self.assertEqual(saving, 0)
        self.assertEqual(group.space_saving_bytes, 0)

    def test_only_partial_failure_is_never_reported_success(self):
        """An operation with success=0, failed=0 and partial_failures=1 must evaluate to PARTIAL_FAILURE."""
        f_keep = os.path.join(self.case_dir, "keep_op.mp3")
        f_del = os.path.join(self.case_dir, "del_op.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep")
        with open(f_del, "wb") as f: f.write(b"del")

        group = DuplicateGroup(
            "G_OPF", DuplicateType.ACOUSTIC_DUPLICATE,
            [AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP), AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)],
            best_track_path=f_keep
        )

        mock_db = MagicMock()
        mock_db.delete_track.side_effect = sqlite3.OperationalError("db locked")

        result = FileOperationService.delete_permanently([group], db=mock_db, journal_path=self.journal_path)
        self.assertEqual(result.partial_failures, 1)
        self.assertEqual(result.status, OperationStatus.PARTIAL_FAILURE)
        self.assertNotEqual(result.status, OperationStatus.SUCCESS)

    def test_fs_success_fs_done_journal_failure_is_recoverable(self):
        """If filesystem succeeds but update_state(FS_DONE) fails, PENDING is preserved and recoverable at startup."""
        f_keep = os.path.join(self.case_dir, "keep_rec_jfail.mp3")
        f_del = os.path.join(self.case_dir, "del_rec_jfail.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep")
        with open(f_del, "wb") as f: f.write(b"del")

        self.db.upsert_track(AudioTrack(filepath=f_del, filesize=10))

        group = DuplicateGroup(
            "G_JFAIL", DuplicateType.ACOUSTIC_DUPLICATE,
            [AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP), AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)],
            best_track_path=f_keep
        )

        orig_update = OperationJournal.update_state
        def fail_on_fs_done(self_obj, op_id, state):
            if state == "FS_DONE":
                raise JournalError("Simulated write error on FS_DONE")
            return orig_update(self_obj, op_id, state)

        with patch.object(OperationJournal, "update_state", side_effect=fail_on_fs_done):
            result = FileOperationService.delete_permanently([group], db=self.db, journal_path=self.journal_path)

        self.assertFalse(os.path.exists(f_del))
        self.assertEqual(result.status, OperationStatus.PARTIAL_FAILURE)

        logs = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)
        self.assertIsNone(self.db.get_track(f_del))
        journal = OperationJournal(db_path=self.journal_path)
        self.assertEqual(len(journal.get_incomplete_operations()), 0)

    def test_db_success_completed_journal_failure_is_recoverable(self):
        """If DB succeeds but update_state(COMPLETED) fails, reconciliation must close record idempotently."""
        f_keep = os.path.join(self.case_dir, "keep_comp_fail.mp3")
        f_del = os.path.join(self.case_dir, "del_comp_fail.mp3")
        with open(f_keep, "wb") as f: f.write(b"keep")
        with open(f_del, "wb") as f: f.write(b"del")

        self.db.upsert_track(AudioTrack(filepath=f_del, filesize=10))

        group = DuplicateGroup(
            "G_COMP_FAIL", DuplicateType.ACOUSTIC_DUPLICATE,
            [AudioTrack(filepath=f_keep, filesize=10, action=FileAction.KEEP), AudioTrack(filepath=f_del, filesize=10, action=FileAction.DELETE)],
            best_track_path=f_keep
        )

        orig_update = OperationJournal.update_state
        def fail_on_completed(self_obj, op_id, state):
            if state == "COMPLETED":
                raise JournalError("Simulated write error on COMPLETED")
            return orig_update(self_obj, op_id, state)

        with patch.object(OperationJournal, "update_state", side_effect=fail_on_completed):
            result = FileOperationService.delete_permanently([group], db=self.db, journal_path=self.journal_path)

        self.assertEqual(result.status, OperationStatus.PARTIAL_FAILURE)
        logs = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)
        journal = OperationJournal(db_path=self.journal_path)
        self.assertEqual(len(journal.get_incomplete_operations()), 0)

    def test_reconcile_does_not_purge_db_when_source_volume_unavailable(self):
        """Reconciliation must NOT purge DB tracks or mark COMPLETED if the storage volume is offline/unavailable."""
        offline_path = "Z:\\nonexistent_volume\\music\\song.mp3"
        track = AudioTrack(filepath=offline_path, filesize=5000)
        self.db.upsert_track(track)

        journal = OperationJournal(db_path=self.journal_path)
        journal.record_pending("op-offline-01", offline_path, "permanent")

        logs = FileOperationService.reconcile_pending_operations(db=self.db, journal_path=self.journal_path)

        self.assertIsNotNone(self.db.get_track(offline_path), "Track must NOT be purged when storage volume is offline")
        incomplete = journal.get_incomplete_operations()
        self.assertEqual(len(incomplete), 1, "Operation must remain pending/unresolved while volume is unavailable")
        self.assertTrue(any("desconectado" in l or "accesible" in l for l in logs))

    def test_24bit_reported_as_s32_raw24_preserved(self):
        """FFmpeg reporting 24-bit PCM as sample_fmt=s32 and bit_depth=24 must map to s24le."""
        info_a = AudioStreamInfo("flac", 44100, 2, "stereo", "s32", 24, True)
        info_b = AudioStreamInfo("flac", 44100, 2, "stereo", "s32", 24, True)
        fmt = get_canonical_pcm_format(info_a, info_b)
        self.assertEqual(fmt, "s24le")

    def test_s64_pcm_not_downconverted(self):
        """64-bit integer PCM must map to s64le without downconversion."""
        info_a = AudioStreamInfo("pcm_s64le", 96000, 2, "stereo", "s64", 64, True)
        info_b = AudioStreamInfo("pcm_s64le", 96000, 2, "stereo", "s64", 64, True)
        fmt = get_canonical_pcm_format(info_a, info_b)
        self.assertEqual(fmt, "s64le")
        self.assertNotEqual(fmt, "s16le")


if __name__ == "__main__":
    unittest.main()
