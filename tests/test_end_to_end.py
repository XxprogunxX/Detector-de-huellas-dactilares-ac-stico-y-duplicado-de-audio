"""
End-to-end integration test suite generating synthetic audio tracks
and verifying all duplicate detection scenarios.
"""

import os
import sys
import shutil
import tempfile
import unittest
import subprocess
import numpy as np
from scipy.io import wavfile

from core.models import DuplicateType, FileAction
from core.scanner import AudioScanner
from core.database import Database
from core.file_manager import move_marked_duplicates


def generate_synthetic_audio(duration: float = 8.0, sample_rate: int = 44100, melody_type: int = 1) -> np.ndarray:
    """Generates synthetic multi-tone full-spectrum waveform for testing."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    if melody_type == 1:
        # Song A: A-minor chord arpeggio with high-frequency harmonics up to 20kHz
        sig = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 554 * t) + 0.2 * np.sin(2 * np.pi * 659 * t)
        sig += 0.15 * np.sin(2 * np.pi * 880 * t) * np.sin(2 * np.pi * 2 * t)
        # Rich upper harmonics up to 19.5kHz (which MP3 128k cuts off at 16kHz)
        sig += 0.08 * np.sin(2 * np.pi * 14500 * t) + 0.08 * np.sin(2 * np.pi * 17500 * t) + 0.06 * np.sin(2 * np.pi * 19500 * t)
    else:
        # Song B: C-major fast rhythm
        sig = 0.6 * np.sin(2 * np.pi * 261 * t) + 0.4 * np.sin(2 * np.pi * 329 * t) + 0.3 * np.sin(2 * np.pi * 392 * t)
        sig *= np.sin(2 * np.pi * 6 * t)

    # Normalize to int16
    sig = sig / np.max(np.abs(sig)) * 0.95
    return (sig * 32767).astype(np.int16)


class TestEndToEndDuplicateDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="audio_dup_test_")
        cls.db_path = os.path.join(cls.test_dir, "test_cache.db")
        cls.sample_rate = 44100

        # Generate Base Song A (8 seconds)
        song_a_data = generate_synthetic_audio(duration=8.0, melody_type=1)
        cls.song_a_master_wav = os.path.join(cls.test_dir, "Master_SongA.wav")
        wavfile.write(cls.song_a_master_wav, cls.sample_rate, song_a_data)

        # 1. Exact Copy of Song A
        cls.song_a_copy_wav = os.path.join(cls.test_dir, "Master_SongA - Copia.wav")
        shutil.copy2(cls.song_a_master_wav, cls.song_a_copy_wav)

        # 2. Song A encoded to MP3 320kbps
        cls.song_a_320k_mp3 = os.path.join(cls.test_dir, "SongA_320kbps.mp3")
        subprocess.run(["ffmpeg", "-y", "-i", cls.song_a_master_wav, "-b:a", "320k", cls.song_a_320k_mp3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 3. Song A encoded to MP3 128kbps (low bitrate)
        cls.song_a_128k_mp3 = os.path.join(cls.test_dir, "SongA_128kbps.mp3")
        subprocess.run(["ffmpeg", "-y", "-i", cls.song_a_master_wav, "-b:a", "128k", cls.song_a_128k_mp3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 4. Song A in FLAC (lossless)
        cls.song_a_flac = os.path.join(cls.test_dir, "SongA_Lossless.flac")
        subprocess.run(["ffmpeg", "-y", "-i", cls.song_a_master_wav, cls.song_a_flac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 5. Fake FLAC (Upscaled from 128kbps MP3)
        cls.song_a_fake_flac = os.path.join(cls.test_dir, "SongA_Fake_Lossless.flac")
        subprocess.run(["ffmpeg", "-y", "-i", cls.song_a_128k_mp3, cls.song_a_fake_flac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 6. Song A Remaster (slight EQ boost)
        remaster_data = (song_a_data * 1.05).clip(-32768, 32767).astype(np.int16)
        cls.song_a_remaster_wav = os.path.join(cls.test_dir, "SongA_Remastered.wav")
        wavfile.write(cls.song_a_remaster_wav, cls.sample_rate, remaster_data)

        # 7. Song A Radio Edit (truncated duration: 5 seconds)
        radio_edit_data = song_a_data[:int(cls.sample_rate * 5.0)]
        cls.song_a_radio_wav = os.path.join(cls.test_dir, "SongA_Radio_Edit.wav")
        wavfile.write(cls.song_a_radio_wav, cls.sample_rate, radio_edit_data)

        # 8. Song B: Distinct Song (must NOT match Song A)
        song_b_data = generate_synthetic_audio(duration=8.0, melody_type=2)
        cls.song_b_wav = os.path.join(cls.test_dir, "SongB_Different_Track.wav")
        wavfile.write(cls.song_b_wav, cls.sample_rate, song_b_data)

        # 9. Song B copy with random filename
        cls.song_b_mp3 = os.path.join(cls.test_dir, "Track_99_Unknown.mp3")
        subprocess.run(["ffmpeg", "-y", "-i", cls.song_b_wav, "-b:a", "256k", cls.song_b_mp3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_full_scan_and_clustering(self):
        db = Database(db_path=self.db_path)
        scanner = AudioScanner(db=db, max_workers=2)

        groups = scanner.scan_directory(self.test_dir)

        self.assertGreaterEqual(len(groups), 1, "Should find duplicate groups")
        
        # Verify Song A group exists
        song_a_group = None
        for g in groups:
            files_in_g = [t.filepath for t in g.tracks]
            if self.song_a_master_wav in files_in_g:
                song_a_group = g
                break

        self.assertIsNotNone(song_a_group, "Song A files should form a duplicate group")
        group_files = [t.filepath for t in song_a_group.tracks]

        # Verify Song A variants are correctly grouped
        self.assertIn(self.song_a_copy_wav, group_files)
        self.assertIn(self.song_a_flac, group_files)
        self.assertIn(self.song_a_320k_mp3, group_files)

        # CRITICAL TEST: Zero False Positives
        # Song B MUST NOT be in Song A's duplicate group!
        self.assertNotIn(self.song_b_wav, group_files, "False positive! Song B was grouped into Song A")
        self.assertNotIn(self.song_b_mp3, group_files, "False positive! Song B MP3 was grouped into Song A")

        # Verify Best Track selection
        # Genuine FLAC or Master WAV should be recommended over 128k MP3 and fake FLAC
        best_path = song_a_group.best_track_path
        self.assertTrue(
            best_path in (self.song_a_flac, self.song_a_master_wav, self.song_a_copy_wav),
            f"Recommended best file was '{os.path.basename(best_path)}' instead of genuine lossless"
        )

        # Verify fake FLAC detection
        fake_flac_track = next((t for t in song_a_group.tracks if t.filepath == self.song_a_fake_flac), None)
        if fake_flac_track:
            self.assertTrue(fake_flac_track.fake_lossless_confidence > 50.0, "Fake FLAC upscaled from 128k MP3 should be flagged")

    def test_incremental_scan_cache_speed(self):
        db = Database(db_path=self.db_path)
        scanner = AudioScanner(db=db, max_workers=2)

        # Second scan should hit cache
        groups = scanner.scan_directory(self.test_dir)
        self.assertGreaterEqual(scanner.stats.files_from_cache, 5, "Files should be loaded from SQLite cache")


if __name__ == "__main__":
    unittest.main()
