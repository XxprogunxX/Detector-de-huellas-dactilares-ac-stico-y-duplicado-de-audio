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
        self.assertEqual(res.duplicate_type, DuplicateType.EXACT_HASH)
        self.assertEqual(res.similarity, 1.0)

    def test_different_songs_no_match(self):
        t1 = AudioTrack(filepath="songA.mp3", sha256="hashA", duration=180.0, fingerprint_raw=[0x0F0F0F0F] * 30)
        t2 = AudioTrack(filepath="songB.mp3", sha256="hashB", duration=300.0, fingerprint_raw=[0xF0F0F0F0] * 30)
        res = compare_tracks(t1, t2)
        self.assertEqual(res.duplicate_type, DuplicateType.NO_MATCH)


if __name__ == "__main__":
    unittest.main()
