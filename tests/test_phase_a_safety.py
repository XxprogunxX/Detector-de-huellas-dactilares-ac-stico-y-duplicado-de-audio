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
from unittest.mock import MagicMock

from core.models import AudioTrack, DuplicateGroup, DuplicateType, FileAction
from core.database import Database
from core.fingerprint import (
    verify_full_normalized_pcm_match,
    get_audio_stream_info,
    get_canonical_pcm_format,
    compute_audio_pcm_hash
)
from core.comparator import compare_tracks
from core.clustering import cluster_duplicates
from core.file_manager import (
    FileOperationService,
    OperationStatus,
    OperationJournal,
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


if __name__ == "__main__":
    unittest.main()
