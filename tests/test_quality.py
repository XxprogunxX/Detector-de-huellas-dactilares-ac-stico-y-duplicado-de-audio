"""
Unit tests for Quality Scoring and Transcode / Fake Lossless Detection.
"""

import unittest
from core.models import AudioTrack
from core.quality_analyzer import evaluate_track_quality


class TestQualityAnalyzer(unittest.TestCase):
    def test_genuine_flac_scores_higher_than_mp3(self):
        flac_track = AudioTrack(
            filepath="song.flac",
            format="FLAC",
            bitrate=950,
            samplerate=44100,
            bit_depth=16,
            is_lossless=True,
            spectral_cutoff=22050.0,
            is_fake_lossless=False
        )
        evaluate_track_quality(flac_track)

        mp3_track = AudioTrack(
            filepath="song.mp3",
            format="MP3",
            bitrate=320,
            samplerate=44100,
            bit_depth=16,
            is_lossless=False,
            spectral_cutoff=20000.0,
            is_fake_lossless=False
        )
        evaluate_track_quality(mp3_track)

        self.assertGreater(flac_track.quality_score, mp3_track.quality_score)
        self.assertIn("Lossless Auténtico", flac_track.quality_details)

    def test_fake_flac_transcode_penalized(self):
        fake_flac = AudioTrack(
            filepath="fake_upscale.flac",
            format="FLAC",
            bitrate=850,
            samplerate=44100,
            bit_depth=16,
            is_lossless=True,
            spectral_cutoff=15800.0,
            is_fake_lossless=True
        )
        evaluate_track_quality(fake_flac)

        mp3_320 = AudioTrack(
            filepath="original.mp3",
            format="MP3",
            bitrate=320,
            samplerate=44100,
            bit_depth=16,
            is_lossless=False,
            spectral_cutoff=20000.0,
            is_fake_lossless=False
        )
        evaluate_track_quality(mp3_320)

        # Genuine MP3 320k MUST score higher than a fake FLAC transcoded from 128k MP3
        self.assertGreater(mp3_320.quality_score, fake_flac.quality_score)
        self.assertIn("Falso Lossless", fake_flac.quality_details)


if __name__ == "__main__":
    unittest.main()
