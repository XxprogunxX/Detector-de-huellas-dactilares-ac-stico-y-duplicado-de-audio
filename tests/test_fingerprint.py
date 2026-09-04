"""
Unit tests for fingerprinting, prefix PCM hash, and full streaming PCM verification.
"""

import os
import wave
import struct
import math
import tempfile
import shutil
import unittest

from core.fingerprint import (
    compute_audio_pcm_hash,
    verify_full_normalized_pcm_match
)
from core.models import AudioTrack, DuplicateType
from core.comparator import compare_tracks
from core.clustering import cluster_duplicates


def create_test_wav(filepath: str, duration_sec: float, freq: float = 440.0, sample_rate: int = 11025):
    """Generates a clean synthetic PCM WAV file."""
    with wave.open(filepath, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        total_samples = int(duration_sec * sample_rate)
        frames = bytearray()
        for i in range(total_samples):
            val = int(16000.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            frames.extend(struct.pack("<h", val))
        wav_file.writeframes(frames)


def create_divergent_wav(filepath: str, duration_sec: float, split_sec: float = 30.0, freq1: float = 440.0, freq2: float = 880.0, sample_rate: int = 11025):
    """Generates a WAV file with freq1 for first split_sec, then freq2 thereafter."""
    with wave.open(filepath, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        total_samples = int(duration_sec * sample_rate)
        split_samples = int(split_sec * sample_rate)
        frames = bytearray()
        for i in range(total_samples):
            freq = freq1 if i < split_samples else freq2
            val = int(16000.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            frames.extend(struct.pack("<h", val))
        wav_file.writeframes(frames)


class TestFingerprintAndExactAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="audioclean_pcm_test_")

        # Audio A: 32 seconds of 440 Hz
        cls.path_a = os.path.join(cls.test_dir, "audio_a.wav")
        create_test_wav(cls.path_a, duration_sec=32.0, freq=440.0)

        # Audio B: 32 seconds. First 30s are 440 Hz (IDENTICAL to A), last 2s are 880 Hz (DIVERGENT)
        cls.path_b = os.path.join(cls.test_dir, "audio_b.wav")
        create_divergent_wav(cls.path_b, duration_sec=32.0, split_sec=30.0, freq1=440.0, freq2=880.0)

        # Audio C: 32 seconds of 440 Hz (100% IDENTICAL to A across entire duration)
        cls.path_c = os.path.join(cls.test_dir, "audio_c.wav")
        create_test_wav(cls.path_c, duration_sec=32.0, freq=440.0)

        # Audio D: 35 seconds of 440 Hz (First 30s identical to A, but duration is 35s vs 32s -> diff 3s > 0.5s)
        cls.path_d = os.path.join(cls.test_dir, "audio_d.wav")
        create_test_wav(cls.path_d, duration_sec=35.0, freq=440.0)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.test_dir):
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_prefix_hash_identical_for_first_30s(self):
        """Demuestra que el hash de 30s es idéntico entre A y B (motivo de la necesidad de verificación completa)."""
        hash_a = compute_audio_pcm_hash(self.path_a, max_seconds=30.0)
        hash_b = compute_audio_pcm_hash(self.path_b, max_seconds=30.0)
        self.assertTrue(len(hash_a) > 0)
        self.assertEqual(hash_a, hash_b, "El hash de 30s DEBE colisionar porque los primeros 30s son idénticos")

    def test_c_divergent_after_30s_fails_full_pcm_and_not_exact_audio(self):
        """
        TEST C: Dos audios con primeros 30 segundos idénticos, pero audio diferente después de 30s.
        Resultado esperado: NO EXACT_AUDIO.
        """
        # 1. Verificación directa de streaming PCM
        match = verify_full_normalized_pcm_match(self.path_a, self.path_b)
        self.assertFalse(match, "verify_full_normalized_pcm_match debe retornar False al diferir tras segundo 30")

        # 2. En comparador
        hash_prefix = compute_audio_pcm_hash(self.path_a, max_seconds=30.0)
        track_a = AudioTrack(filepath=self.path_a, audio_hash=hash_prefix, duration=32.0, sha256="sha_a")
        track_b = AudioTrack(filepath=self.path_b, audio_hash=hash_prefix, duration=32.0, sha256="sha_b")

        report = compare_tracks(track_a, track_b)
        self.assertNotEqual(
            report.classification,
            DuplicateType.EXACT_AUDIO,
            "BUG P0: Dos pistas con cola diferente no deben clasificarse como EXACT_AUDIO"
        )
        self.assertFalse(report.is_exact_audio)

    def test_d_equivalent_full_audio_is_exact_audio_100_percent(self):
        """
        TEST D: Dos audios completos equivalentes.
        Si el PCM normalizado completo coincide: EXACT_AUDIO 100%.
        """
        match = verify_full_normalized_pcm_match(self.path_a, self.path_c)
        self.assertTrue(match, "verify_full_normalized_pcm_match debe retornar True para audios completos idénticos")

        hash_prefix = compute_audio_pcm_hash(self.path_a, max_seconds=30.0)
        track_a = AudioTrack(filepath=self.path_a, audio_hash=hash_prefix, duration=32.0, sha256="sha_a")
        track_c = AudioTrack(filepath=self.path_c, audio_hash=hash_prefix, duration=32.0, sha256="sha_c_diff_tags")

        report = compare_tracks(track_a, track_c)
        self.assertEqual(report.classification, DuplicateType.EXACT_AUDIO)
        self.assertEqual(report.confidence, 100.0)
        self.assertTrue(report.is_exact_audio)

    def test_e_duration_diff_greater_than_half_second_not_exact_audio(self):
        """
        TEST E: Dos archivos con prefijo idéntico pero diferencia de duración mayor a 0.5s.
        Resultado: NO EXACT_AUDIO.
        """
        hash_prefix = compute_audio_pcm_hash(self.path_a, max_seconds=30.0)
        # Duración A = 32.0s, Duración D = 35.0s (diferencia = 3.0s > 0.5s)
        track_a = AudioTrack(filepath=self.path_a, audio_hash=hash_prefix, duration=32.0, sha256="sha_a")
        track_d = AudioTrack(filepath=self.path_d, audio_hash=hash_prefix, duration=35.0, sha256="sha_d")

        report = compare_tracks(track_a, track_d)
        self.assertNotEqual(
            report.classification,
            DuplicateType.EXACT_AUDIO,
            "No debe otorgarse EXACT_AUDIO si la diferencia de duración supera 0.5 segundos"
        )
        self.assertFalse(report.is_exact_audio)

    def test_clustering_with_exact_and_divergent_tracks(self):
        """Verifica que cluster_duplicates no una pistas divergentes como EXACT_AUDIO."""
        hash_prefix = compute_audio_pcm_hash(self.path_a, max_seconds=30.0)
        track_a = AudioTrack(filepath=self.path_a, audio_hash=hash_prefix, duration=32.0, sha256="sha_a")
        track_b = AudioTrack(filepath=self.path_b, audio_hash=hash_prefix, duration=32.0, sha256="sha_b")
        track_c = AudioTrack(filepath=self.path_c, audio_hash=hash_prefix, duration=32.0, sha256="sha_c")

        groups = cluster_duplicates([track_a, track_b, track_c])
        # track_a y track_c deben ser EXACT_AUDIO
        exact_groups = [g for g in groups if g.primary_type == DuplicateType.EXACT_AUDIO]
        for g in exact_groups:
            paths = [t.filepath for t in g.tracks]
            self.assertNotIn(
                self.path_b, paths,
                "La pista divergente track_b NO debe agruparse en EXACT_AUDIO con track_a"
            )


if __name__ == "__main__":
    unittest.main()
