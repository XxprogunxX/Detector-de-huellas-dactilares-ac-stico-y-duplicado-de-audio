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


def generate_synthetic_audio(duration: float = 35.0, sample_rate: int = 44100, melody_type: int = 1) -> np.ndarray:
    """Generates synthetic multi-tone full-spectrum waveform for testing."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    if melody_type == 1:
        # Song A: A-minor chord arpeggio with frequency sweep to generate > 3 unique tokens
        f_sweep = 440 + 10 * t
        sig = 0.4 * np.sin(2 * np.pi * f_sweep * t) + 0.3 * np.sin(2 * np.pi * 554 * t) + 0.2 * np.sin(2 * np.pi * 659 * t)
        sig += 0.05 * np.sin(2 * np.pi * 3000 * t) # Midband anchor for spectral analysis
        # Full-spectrum broadband noise ensures the genuine file registers a 22kHz cutoff
        sig += 0.05 * np.random.randn(len(t))

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

        # Generate Base Song A (35 seconds)
        song_a_data = generate_synthetic_audio(duration=35.0, melody_type=1)
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
        subprocess.run(["ffmpeg", "-y", "-i", cls.song_a_master_wav, "-b:a", "128k", "-af", "firequalizer=gain='if(gte(f,16000), -120, 0)'", cls.song_a_128k_mp3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
        song_b_data = generate_synthetic_audio(duration=35.0, melody_type=2)
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
        db.close()

    def test_incremental_scan_cache_speed(self):
        db = Database(db_path=self.db_path)
        scanner = AudioScanner(db=db, max_workers=2)

        # Second scan should hit cache
        groups = scanner.scan_directory(self.test_dir)
        self.assertGreaterEqual(scanner.stats.files_from_cache, 5, "Files should be loaded from SQLite cache")
        db.close()

    def test_synthetic_fixture_exceeds_size_threshold(self):
        """Bug A: Ensure synthetic fixture exceeds 2MB so it doesn't get skipped by early-exit."""
        size = os.path.getsize(self.song_a_master_wav)
        self.assertGreater(size, 2_000_000, "Synthetic file must be > 2MB to trigger FFT analysis in production")

    def test_spectral_analysis_sample_rate_supports_20khz(self):
        """Bug B: Ensure sample rate is high enough (Nyquist) to detect frequencies above 11kHz."""
        from core.quality_analyzer import estimate_spectral_cutoff
        cutoff_hz, conf = estimate_spectral_cutoff(self.song_a_fake_flac, sample_rate=44100)
        # 128kbps MP3 cuts around 14-16kHz, so it should be well below 22kHz
        self.assertLess(cutoff_hz, 17000.0, "Fake FLAC cutoff must be detected correctly, not artificially capped")
        self.assertGreater(conf, 50.0, "Confidence must be > 50% for Fake Lossless")

    def test_synthetic_fixture_has_midband_anchor(self):
        """Bug C: Ensure synthetic file has energy in the 1kHz-5kHz band for correct dB normalization."""
        import numpy as np
        import scipy.io.wavfile as wavfile
        sr, data = wavfile.read(self.song_a_master_wav)
        # A simple FFT check to ensure energy exists near 3000 Hz
        n_fft = 2048
        segment = data[:n_fft]
        power = np.abs(np.fft.rfft(segment * np.hanning(n_fft))) ** 2
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
        mid_mask = (freqs >= 1000) & (freqs <= 5000)
        peak_mid_power = np.max(power[mid_mask]) if np.any(mid_mask) else 0.0
        self.assertGreater(peak_mid_power, 1e-4, "Missing midband anchor in synthetic test data")


if __name__ == "__main__":
    unittest.main()
