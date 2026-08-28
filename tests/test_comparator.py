"""
Unit tests for Acoustic Comparator and Hamming Distance Alignment.
"""

import unittest
from core.models import AudioTrack, DuplicateType
from core.comparator import compare_raw_fingerprints, compare_tracks


class TestComparator(unittest.TestCase):
    def test_identical_fingerprints(self):
        fp = [0x12345678, 0xABCDEF01, 0x13579BDF, 0x2468ACE0] * 10
        sim, offset = compare_raw_fingerprints(fp, fp)
        self.assertAlmostEqual(sim, 1.0, places=2)
        self.assertEqual(offset, 0)

    def test_time_shifted_fingerprints(self):
        base_fp = [0x12345678, 0xABCDEF01, 0x13579BDF, 0x2468ACE0] * 15
        shifted_fp = base_fp[5:] + [0x00000000] * 5
        sim, offset = compare_raw_fingerprints(base_fp, shifted_fp)
        self.assertGreater(sim, 0.90)

    def test_completely_different_fingerprints(self):
        # Two random uncorrelated sequences
        fp_a = [0xAAAAAAAA] * 40
        fp_b = [0x55555555] * 40  # 100% bit inverted
        sim, _ = compare_raw_fingerprints(fp_a, fp_b)
        self.assertLess(sim, 0.20)

    def test_exact_hash_match(self):
        t1 = AudioTrack(filepath="t1.mp3", sha256="abc123hash", duration=180.0)
        t2 = AudioTrack(filepath="t2.mp3", sha256="abc123hash", duration=180.0)
        res = compare_tracks(t1, t2)
        self.assertEqual(res.classification, DuplicateType.EXACT_HASH)
        self.assertEqual(res.confidence, 100.0)

    def test_different_songs_no_match(self):
        t1 = AudioTrack(filepath="songA.mp3", sha256="hashA", duration=180.0, fingerprint_raw=[0x0F0F0F0F] * 30)
        t2 = AudioTrack(filepath="songB.mp3", sha256="hashB", duration=300.0, fingerprint_raw=[0xF0F0F0F0] * 30)
        res = compare_tracks(t1, t2)
        self.assertEqual(res.classification, DuplicateType.NO_MATCH)


from unittest.mock import patch

class TestEvidenceEngine(unittest.TestCase):
    def _create_mock_tracks(self, base_fp, modified_fp, duration_diff, spectral_a, spectral_b):
        t1 = AudioTrack(filepath="t1.mp3", sha256="a", audio_hash="x", duration=100.0, 
                        fingerprint_raw=base_fp, spectral_cutoff=spectral_a,
                        format="mp3", samplerate=44100, channels=2, bitrate=320000)
        t2 = AudioTrack(filepath="t2.mp3", sha256="b", audio_hash="y", duration=100.0 + duration_diff, 
                        fingerprint_raw=modified_fp, spectral_cutoff=spectral_b,
                        format="mp3", samplerate=44100, channels=2, bitrate=128000)
        return t1, t2

    @patch("core.comparator.compare_raw_fingerprints")
    def test_bump_to_acoustic_duplicate(self, mock_compare):
        # Un Chromaprint de ~93% con offset/duración/espectro perfectos SÍ cruza a ACOUSTIC_DUPLICATE
        mock_compare.return_value = (0.93, 0)
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=0.0, spectral_a=16000.0, spectral_b=16000.0)
        res = compare_tracks(t1, t2)
        
        # Max modifiers: Offset (+3), Duration (+2), Spectral (+1) = +6
        # Final confidence should be 93 + 6 = 99 >= 95
        self.assertEqual(res.classification, DuplicateType.ACOUSTIC_DUPLICATE)
        self.assertTrue(res.confidence >= 95.0)
        
        # El reasons debe explicarlo textualmente
        reason_found = any("Confidence ajustada por señales secundarias: base 93.0%" in r for r in res.reasons)
        self.assertTrue(reason_found, "Falta la justificación textual del ajuste en reasons.")

    @patch("core.comparator.compare_raw_fingerprints")
    def test_degrade_to_possible_duplicate(self, mock_compare):
        # Un Chromaprint de 97% con duración muy distinta (>30s) SÍ degrada a POSSIBLE_DUPLICATE
        mock_compare.return_value = (0.97, 0)
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=35.0, spectral_a=16000.0, spectral_b=16000.0)
        res = compare_tracks(t1, t2)
        
        # Penalty should be -10 points. 97 - 10 = 87 -> POSSIBLE_DUPLICATE
        self.assertEqual(res.classification, DuplicateType.POSSIBLE_DUPLICATE)
        self.assertLess(res.confidence, 95.0)
        
        reason_found = any("Variación de duración" in r for r in res.reasons)
        self.assertTrue(reason_found, "Falta la razón de 'versión extendida' en reasons.")

    @patch("core.comparator.compare_raw_fingerprints")
    def test_decay_functions_explicit(self, mock_compare):
        # Use a base similarity of 90% so we don't hit the 99.9 ceiling
        mock_compare.return_value = (0.90, 0)
        
        # Duration Bonus (Offset is 0, Spectral is 0 diff = +3+1 = +4 constants)
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=0.0, spectral_a=16000.0, spectral_b=16000.0)
        c_dur_0 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=1.0, spectral_a=16000.0, spectral_b=16000.0)
        c_dur_1 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=2.0, spectral_a=16000.0, spectral_b=16000.0)
        c_dur_2 = compare_tracks(t1, t2).confidence
        
        self.assertAlmostEqual(c_dur_0 - c_dur_1, 1.0, places=1)
        self.assertAlmostEqual(c_dur_1 - c_dur_2, 1.0, places=1)
        
        # Duration Penalty (Start at 10s, middle at 20s, max at 30s)
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=10.0, spectral_a=16000.0, spectral_b=16000.0)
        c_pen_10 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=20.0, spectral_a=16000.0, spectral_b=16000.0)
        c_pen_20 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=30.0, spectral_a=16000.0, spectral_b=16000.0)
        c_pen_30 = compare_tracks(t1, t2).confidence
        
        self.assertAlmostEqual(c_pen_10 - c_pen_20, 5.0, places=1)
        self.assertAlmostEqual(c_pen_20 - c_pen_30, 5.0, places=1)

        # Spectral Bonus (0, 500, 1000)
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=5.0, spectral_a=16000.0, spectral_b=16000.0)
        c_spec_0 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=5.0, spectral_a=16000.0, spectral_b=15500.0)
        c_spec_500 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=5.0, spectral_a=16000.0, spectral_b=15000.0)
        c_spec_1000 = compare_tracks(t1, t2).confidence
        
        self.assertAlmostEqual(c_spec_0 - c_spec_500, 0.5, places=1)
        self.assertAlmostEqual(c_spec_500 - c_spec_1000, 0.5, places=1)

        # Offset Decay (0, 7.5, 15 frames)
        mock_compare.side_effect = [(0.90, 0), (0.90, 7.5), (0.90, 15)]
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=5.0, spectral_a=16000.0, spectral_b=15000.0)
        c_off_0 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=5.0, spectral_a=16000.0, spectral_b=15000.0)
        c_off_7 = compare_tracks(t1, t2).confidence
        
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=5.0, spectral_a=16000.0, spectral_b=15000.0)
        c_off_15 = compare_tracks(t1, t2).confidence
        
        self.assertAlmostEqual(c_off_0, 93.0, places=1)
        self.assertAlmostEqual(c_off_7, 91.5, places=1)
        self.assertAlmostEqual(c_off_15, 90.0, places=1)

    @patch("core.comparator.compare_raw_fingerprints")
    def test_spectral_na_case(self, mock_compare):
        # Un test que confirme el caso de "N/A" cuando el análisis espectral falla
        mock_compare.return_value = (0.90, 0)
        t1, t2 = self._create_mock_tracks([1], [2], duration_diff=5.0, spectral_a=0.0, spectral_b=16000.0)
        res = compare_tracks(t1, t2)
        
        self.assertIsNone(res.spectral_diff)
        reason_found = any("Espectro: N/A" in r for r in res.reasons)
        self.assertTrue(reason_found, "Debe anotar N/A en reasons si falla.")

    def test_missing_fingerprint_is_uncertain(self):
        """Bug regression: ensure missing fingerprints correctly return UNCERTAIN without AttributeError"""
        t1, t2 = self._create_mock_tracks([], [], duration_diff=0.0, spectral_a=16000.0, spectral_b=16000.0)
        # Manually clear fingerprints just in case _create_mock_tracks injects something
        t1.fingerprint_raw = []
        t2.fingerprint_raw = []
        
        res = compare_tracks(t1, t2)
        self.assertEqual(res.classification, DuplicateType.UNCERTAIN)
        self.assertEqual(res.confidence, 0.0)
        reason_found = any("Incierto:" in r for r in res.reasons)
        self.assertTrue(reason_found)

if __name__ == "__main__":
    unittest.main()
