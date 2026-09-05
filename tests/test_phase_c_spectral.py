"""
Comprehensive Test Suite for Phase C — Evidence-Based SpectralAssessment and Safe Fake Lossless (AC-005, AC-017).

Verifies:
  1. Circular-import free architecture (spectral_types)
  2. Fail-closed UNKNOWN behavior (silence, decode failure, corruption, short duration, insufficient windows, NaN/Inf)
  3. NOT_ANALYZED behavior and zero fabricated cutoffs from bitrate
  4. Multi-window and multi-region temporal aggregation
  5. Multi-channel stereo preservation without mono downmixing
  6. Sample rate / Nyquist bandwidth constraints (22kHz, 32kHz, 44.1kHz, 96kHz)
  7. evaluate_track_quality: 0 bonus for UNKNOWN, NOT_ANALYZED, and NO_LOSSY_EVIDENCE
  8. Legacy wrapper estimate_spectral_cutoff fail-closed behavior
  9. Controlled synthetic signals (broadband, persistent cutoff, silent, isolated abnormal window)
  10. Session backward compatibility and serialization roundtrip
"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import numpy as np

from core.spectral_types import SpectralAssessment, SpectralResult
from core.models import AudioTrack
from core.quality_analyzer import (
    analyze_pcm_samples,
    analyze_spectrum,
    estimate_spectral_cutoff,
    evaluate_track_quality,
    PROVISIONAL_MIN_ENERGY_DBFS,
    PROVISIONAL_MIN_VALID_WINDOWS,
    PROVISIONAL_MIN_DURATION_SECONDS,
    PROVISIONAL_CUTOFF_ATTENUATION_DB,
    PROVISIONAL_PERSISTENCE_RATIO,
    PROVISIONAL_MIN_SAMPLE_RATE,
)
from core.scanner import _process_audio_worker


class TestSpectralCircularImports(unittest.TestCase):
    """1. Test that core modules can be imported cleanly in any order without circular dependency."""

    def test_no_circular_imports_smoke(self):
        import importlib
        import core.spectral_types
        import core.models
        import core.quality_analyzer
        import core.scanner

        self.assertIsNotNone(core.spectral_types.SpectralAssessment)
        self.assertIsNotNone(core.spectral_types.SpectralResult)
        self.assertIsNotNone(core.models.AudioTrack)
        self.assertIsNotNone(core.quality_analyzer.analyze_spectrum)
        self.assertIsNotNone(core.scanner.AudioScanner)


class TestSpectralFailClosedUnknown(unittest.TestCase):
    """Fail-closed behavior: UNKNOWN must be returned for all invalid, corrupt, or silent inputs."""

    def test_silent_audio_returns_unknown(self):
        """1. Audio with RMS < -60 dBFS must return UNKNOWN with reason 'insufficient_signal_energy'."""
        sr = 44100
        # 4 seconds of pure silence / extremely low noise (-90 dBFS)
        silent_samples = np.zeros(sr * 4, dtype=np.float32)
        res = analyze_pcm_samples(silent_samples, sample_rate=sr, channels=1, analyzed_duration=4.0)

        self.assertEqual(res.assessment, SpectralAssessment.UNKNOWN)
        self.assertEqual(res.reason, "insufficient_signal_energy")
        self.assertEqual(res.confidence, 0.0)
        self.assertIsNone(res.cutoff_hz)

    @patch("subprocess.Popen")
    def test_ffmpeg_failure_returns_unknown(self, mock_popen):
        """2. If FFmpeg fails to execute or decode, analyze_spectrum returns UNKNOWN."""
        proc = MagicMock()
        proc.communicate.return_value = (b"", b"Decode error")
        mock_popen.return_value = proc

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tf:
            tf.write(b"fLaC" + b"\x00" * 1000)
            tf_path = tf.name

        try:
            res = analyze_spectrum(tf_path, sample_rate=44100, channels=2, duration=10.0)
            self.assertEqual(res.assessment, SpectralAssessment.UNKNOWN)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_corrupted_audio_returns_unknown(self):
        """3. An empty or non-existent file returns UNKNOWN."""
        res_missing = analyze_spectrum("non_existent_file_abc123.flac")
        self.assertEqual(res_missing.assessment, SpectralAssessment.UNKNOWN)

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tf:
            tf_path = tf.name  # 0 bytes

        try:
            res_empty = analyze_spectrum(tf_path)
            self.assertEqual(res_empty.assessment, SpectralAssessment.UNKNOWN)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_too_short_signal_returns_unknown(self):
        """4. Audio duration < PROVISIONAL_MIN_DURATION_SECONDS (3.0s) returns UNKNOWN."""
        sr = 44100
        # 1.5 seconds of audio
        short_samples = np.random.uniform(-0.1, 0.1, int(sr * 1.5)).astype(np.float32)
        res = analyze_pcm_samples(short_samples, sample_rate=sr, channels=1, analyzed_duration=1.5)

        self.assertEqual(res.assessment, SpectralAssessment.UNKNOWN)

    def test_insufficient_fft_windows_returns_unknown(self):
        """5. Fewer than PROVISIONAL_MIN_VALID_WINDOWS (16) valid windows returns UNKNOWN."""
        sr = 44100
        # Only 4000 samples (less than 16 hops of 1024)
        tiny_samples = np.random.uniform(-0.2, 0.2, 4000).astype(np.float32)
        res = analyze_pcm_samples(tiny_samples, sample_rate=sr, channels=1, analyzed_duration=0.1)

        self.assertEqual(res.assessment, SpectralAssessment.UNKNOWN)

    def test_nan_or_invalid_spectrum_returns_unknown(self):
        """6. Signal containing NaN or Inf returns UNKNOWN with reason 'nan_or_invalid_spectrum'."""
        sr = 44100
        bad_samples = np.random.uniform(-0.2, 0.2, sr * 4).astype(np.float32)
        bad_samples[100] = np.nan
        bad_samples[200] = np.inf

        res = analyze_pcm_samples(bad_samples, sample_rate=sr, channels=1, analyzed_duration=4.0)
        self.assertEqual(res.assessment, SpectralAssessment.UNKNOWN)
        self.assertEqual(res.reason, "nan_or_invalid_spectrum")


class TestQualityScoringWithSpectralAssessment(unittest.TestCase):
    """Quality scoring rules: UNKNOWN, NOT_ANALYZED, and NO_LOSSY_EVIDENCE receive 0 bonus."""

    def test_unknown_gets_zero_quality_bonus(self):
        """7. Track with UNKNOWN assessment gets 0 spectral bonus in evaluate_track_quality."""
        track = AudioTrack(
            filepath="test.flac", format="FLAC", bitrate=900, samplerate=44100, bit_depth=16,
            is_lossless=True, spectral_assessment=SpectralAssessment.UNKNOWN,
            spectral_cutoff=0.0, fake_lossless_confidence=0.0
        )
        evaluate_track_quality(track)
        # Container (45) + Bitrate (30) + BitDepth (5) + SampleRate (4) = 84.0. Spectral bonus = 0.
        self.assertEqual(track.quality_score, 84.0)
        self.assertIn("Resultado espectral no concluyente", track.quality_details)

    def test_not_analyzed_gets_zero_quality_bonus(self):
        """8. Track with NOT_ANALYZED gets 0 spectral bonus."""
        track = AudioTrack(
            filepath="test.flac", format="FLAC", bitrate=900, samplerate=44100, bit_depth=16,
            is_lossless=True, spectral_assessment=SpectralAssessment.NOT_ANALYZED,
            spectral_cutoff=0.0, fake_lossless_confidence=0.0
        )
        evaluate_track_quality(track)
        self.assertEqual(track.quality_score, 84.0)
        self.assertIn("Análisis espectral no realizado", track.quality_details)

    def test_no_lossy_evidence_does_not_receive_unvalidated_positive_bonus(self):
        """5b. NO_LOSSY_EVIDENCE receives 0 positive bonus in Phase C."""
        track = AudioTrack(
            filepath="genuine.flac", format="FLAC", bitrate=900, samplerate=44100, bit_depth=16,
            is_lossless=True, spectral_assessment=SpectralAssessment.NO_LOSSY_EVIDENCE,
            spectral_cutoff=22050.0, fake_lossless_confidence=0.0
        )
        evaluate_track_quality(track)
        # Must be 84.0, NOT 84 + 15 = 99.0
        self.assertEqual(track.quality_score, 84.0)
        self.assertIn("Sin evidencia lossy detectada", track.quality_details)


class TestSpectralDisabledAndBitrateIndependence(unittest.TestCase):
    """Verifies that spectral_analysis=False skips FFT and cutoffs are never derived from bitrate."""

    @patch("core.scanner.analyze_spectrum")
    def test_spectral_disabled_returns_not_analyzed_and_skips_fft(self, mock_analyze):
        """9 & 10. When spectral_analysis=False, analyze_spectrum is NOT called and NOT_ANALYZED is returned."""
        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tf:
            tf.write(b"fLaC" + b"\x00" * 500)
            tf_path = tf.name

        try:
            with patch("core.scanner.compute_file_sha256", return_value="abc"), \
                 patch("core.scanner.extract_metadata", return_value={"format": "FLAC", "duration": 100.0, "is_lossless": True, "samplerate": 44100, "channels": 2}), \
                 patch("core.scanner.extract_fingerprint", return_value=(100.0, [1, 2])):
                data = _process_audio_worker(tf_path, min_duration=5.0, spectral_analysis=False)
                self.assertIsNotNone(data)
                self.assertEqual(data["spectral_assessment"], SpectralAssessment.NOT_ANALYZED)
                self.assertEqual(data["spectral_cutoff"], 0.0)
                mock_analyze.assert_not_called()
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_lossy_not_analyzed_does_not_invent_cutoff(self):
        """11 & 12. Lossy tracks (MP3/AAC) are not given artificial cutoffs derived from bitrate."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tf.write(b"ID3" + b"\x00" * 500)
            tf_path = tf.name

        try:
            with patch("core.scanner.compute_file_sha256", return_value="mp3hash"), \
                 patch("core.scanner.extract_metadata", return_value={"format": "MP3", "duration": 180.0, "is_lossless": False, "bitrate": 128, "samplerate": 44100, "channels": 2}), \
                 patch("core.scanner.extract_fingerprint", return_value=(180.0, [1, 2])):
                data = _process_audio_worker(tf_path, min_duration=5.0, spectral_analysis=True)
                self.assertIsNotNone(data)
                # Previously, 128k MP3 was given 16000.0 Hz. Now it must be 0.0 with NOT_ANALYZED!
                self.assertEqual(data["spectral_cutoff"], 0.0)
                self.assertEqual(data["spectral_assessment"], SpectralAssessment.NOT_ANALYZED)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)


class TestStereoPreservationAndNoDownmix(unittest.TestCase):
    """Multi-channel analysis without mono downmixing."""

    def test_stereo_phase_cancellation_does_not_create_false_cutoff(self):
        """24. Stereo signal with out-of-phase high frequency content (+S on L, -S on R) must not cancel into a fake cutoff."""
        sr = 44100
        dur = 4.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)

        # Midrange signal (in phase) + 20kHz high frequency tone (out of phase)
        mid_signal = 0.3 * np.sin(2 * np.pi * 2000 * t, dtype=np.float32)
        high_signal = 0.2 * np.sin(2 * np.pi * 20000 * t, dtype=np.float32)

        left = mid_signal + high_signal
        right = mid_signal - high_signal  # 180 degrees out of phase

        # If downmixed to mono: (left + right)/2 = mid_signal (20kHz is completely canceled!)
        downmixed = 0.5 * (left + right)
        res_downmixed = analyze_pcm_samples(downmixed, sample_rate=sr, channels=1, analyzed_duration=dur)
        # The downmixed mono signal loses 20kHz and would falsely suspect a cutoff
        self.assertNotEqual(res_downmixed.assessment, SpectralAssessment.NO_LOSSY_EVIDENCE)

        # But with native stereo channel analysis:
        stereo_samples = np.column_stack([left, right])
        res_stereo = analyze_pcm_samples(stereo_samples, sample_rate=sr, channels=2, analyzed_duration=dur)
        # Because both individual channels actually contain the 20kHz tone, no cutoff is falsely accused!
        self.assertEqual(res_stereo.assessment, SpectralAssessment.NO_LOSSY_EVIDENCE)

    def test_high_frequency_content_in_one_channel_prevents_false_transcode(self):
        """25. If Channel 0 has genuine high-frequency content and Channel 1 is filtered, do NOT accuse transcode."""
        sr = 44100
        dur = 4.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)

        # Wideband noise on left channel (full spectrum up to 22kHz)
        np.random.seed(42)
        left = np.random.uniform(-0.3, 0.3, len(t)).astype(np.float32)

        # Low-pass filtered signal on right channel (only low frequencies up to 5kHz)
        right = 0.4 * np.sin(2 * np.pi * 1000 * t, dtype=np.float32)

        stereo = np.column_stack([left, right])
        res = analyze_pcm_samples(stereo, sample_rate=sr, channels=2, analyzed_duration=dur)

        # Conservative rule: Left channel contradicts lossy transcode -> never SUSPECTED_TRANSCODE
        self.assertNotEqual(res.assessment, SpectralAssessment.SUSPECTED_TRANSCODE)


class TestSampleRateAndNyquist(unittest.TestCase):
    """Sample rate and Nyquist frequency constraints."""

    def test_low_sample_rate_does_not_false_positive_transcode(self):
        """26. Low sample rate (22050 Hz, Nyquist 11025 Hz) must return UNKNOWN with insufficient bandwidth."""
        sr = 22050
        samples = np.random.uniform(-0.2, 0.2, sr * 4).astype(np.float32)
        res = analyze_pcm_samples(samples, sample_rate=sr, channels=1, analyzed_duration=4.0)

        self.assertEqual(res.assessment, SpectralAssessment.UNKNOWN)
        self.assertEqual(res.reason, "insufficient_frequency_bandwidth")

    def test_32khz_native_audio_is_not_false_transcode(self):
        """26b. 32000 Hz audio has Nyquist 16 kHz; it must NOT be suspected as transcode just because spectrum ends at 16 kHz."""
        sr = 32000
        # Broadband noise that naturally fills the entire 32 kHz spectrum up to its 16 kHz Nyquist
        np.random.seed(42)
        samples = np.random.uniform(-0.3, 0.3, sr * 4).astype(np.float32)
        res = analyze_pcm_samples(samples, sample_rate=sr, channels=1, analyzed_duration=4.0)

        # Must return UNKNOWN with insufficient_frequency_bandwidth and NEVER SUSPECTED_TRANSCODE
        self.assertEqual(res.assessment, SpectralAssessment.UNKNOWN)
        self.assertEqual(res.reason, "insufficient_frequency_bandwidth")
        self.assertNotEqual(res.assessment, SpectralAssessment.SUSPECTED_TRANSCODE)

    def test_high_sample_rate_analysis_preserves_relevant_band(self):
        """27. High sample rate (96000 Hz) evaluates the target audio band properly."""
        sr = 96000
        dur = 4.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
        # Signal with energy at 1 kHz, 5 kHz, and 20 kHz
        signal = (
            0.3 * np.sin(2 * np.pi * 1000 * t) +
            0.3 * np.sin(2 * np.pi * 5000 * t) +
            0.2 * np.sin(2 * np.pi * 20000 * t)
        ).astype(np.float32)

        res = analyze_pcm_samples(signal, sample_rate=sr, channels=1, analyzed_duration=dur)
        self.assertEqual(res.assessment, SpectralAssessment.NO_LOSSY_EVIDENCE)


class TestMultiRegionTemporalAnalysis(unittest.TestCase):
    """Multiple temporally distributed probe regions."""

    @patch("core.quality_analyzer._probe_audio_segment")
    def test_filtered_intro_does_not_mark_entire_track_transcoded(self, mock_probe):
        """28. Track with filtered intro (e.g. lo-fi radio intro) but full bandwidth in mid/late regions is not marked as transcode."""
        sr = 44100
        dur_segment = 4.0
        t = np.linspace(0, dur_segment, int(sr * dur_segment), endpoint=False, dtype=np.float32)

        # Region 1 (Intro): low-pass filtered at 3kHz
        intro_seg = (0.4 * np.sin(2 * np.pi * 1500 * t)).astype(np.float32)

        # Regions 2 & 3 (Middle & Late): broadband audio with 20kHz energy
        np.random.seed(123)
        full_seg = np.random.uniform(-0.3, 0.3, len(t)).astype(np.float32)

        # Mock probes for early, middle, late regions
        mock_probe.side_effect = [intro_seg, full_seg, full_seg]

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tf:
            tf.write(b"fLaC" + b"\x00" * 1000)
            tf_path = tf.name

        try:
            res = analyze_spectrum(tf_path, sample_rate=sr, channels=1, duration=100.0)
            # Full bandwidth in mid and late contradicts lossy transcode
            self.assertNotEqual(res.assessment, SpectralAssessment.SUSPECTED_TRANSCODE)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    @patch("core.quality_analyzer._probe_audio_segment")
    def test_silent_intro_does_not_make_result_unknown_if_later_regions_are_valid(self, mock_probe):
        """29. Silent intro is skipped and valid later regions provide conclusive analysis."""
        sr = 44100
        dur_segment = 4.0
        t = np.linspace(0, dur_segment, int(sr * dur_segment), endpoint=False, dtype=np.float32)

        # Region 1: silence (RMS < -60 dBFS)
        silent_seg = np.zeros(len(t), dtype=np.float32)
        # Regions 2 & 3: full bandwidth audio
        full_seg = np.random.uniform(-0.3, 0.3, len(t)).astype(np.float32)

        mock_probe.side_effect = [silent_seg, full_seg, full_seg]

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tf:
            tf.write(b"fLaC" + b"\x00" * 1000)
            tf_path = tf.name

        try:
            res = analyze_spectrum(tf_path, sample_rate=sr, channels=1, duration=100.0)
            self.assertEqual(res.assessment, SpectralAssessment.NO_LOSSY_EVIDENCE)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    @patch("core.quality_analyzer._probe_audio_segment")
    def test_persistent_cutoff_across_multiple_track_regions_can_be_suspected(self, mock_probe):
        """30. Track with persistent 16 kHz cutoff across intro, middle, and late regions produces SUSPECTED_TRANSCODE."""
        sr = 44100
        dur_segment = 4.0
        t = np.linspace(0, dur_segment, int(sr * dur_segment), endpoint=False, dtype=np.float32)

        # Create audio filtered strictly below 16 kHz (no energy above 16 kHz)
        # Sum of frequencies up to 15.5 kHz
        cutoff_seg = (
            0.25 * np.sin(2 * np.pi * 1000 * t) +
            0.25 * np.sin(2 * np.pi * 4000 * t) +
            0.25 * np.sin(2 * np.pi * 10000 * t) +
            0.20 * np.sin(2 * np.pi * 15500 * t)
        ).astype(np.float32)

        mock_probe.side_effect = [cutoff_seg, cutoff_seg, cutoff_seg]

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tf:
            tf.write(b"fLaC" + b"\x00" * 1000)
            tf_path = tf.name

        try:
            res = analyze_spectrum(tf_path, sample_rate=sr, channels=1, duration=100.0)
            self.assertEqual(res.assessment, SpectralAssessment.SUSPECTED_TRANSCODE)
            self.assertTrue(res.cutoff_hz is not None and res.cutoff_hz < 17000.0)
            self.assertTrue(0.0 <= res.confidence <= 100.0)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)


class TestControlledSyntheticSignals(unittest.TestCase):
    """Controlled synthetic validation cases (Broadband, persistent lowpass, silence, isolated abnormal window)."""

    def test_synthetic_broadband_signal_has_no_lossy_evidence(self):
        """Case A: Broadband white noise has energy up to Nyquist and yields NO_LOSSY_EVIDENCE."""
        sr = 44100
        dur = 4.0
        np.random.seed(777)
        noise = np.random.uniform(-0.3, 0.3, int(sr * dur)).astype(np.float32)

        res = analyze_pcm_samples(noise, sample_rate=sr, channels=1, analyzed_duration=dur)
        self.assertEqual(res.assessment, SpectralAssessment.NO_LOSSY_EVIDENCE)

    def test_synthetic_single_abnormal_window_does_not_trigger_transcode(self):
        """Case D: A single abnormal filtered window among many normal broadband windows does NOT trigger transcode."""
        sr = 44100
        dur = 6.0
        np.random.seed(888)
        samples = np.random.uniform(-0.3, 0.3, int(sr * dur)).astype(np.float32)

        # Force a single 2048-sample window to be heavily low-pass filtered
        samples[2048:4096] = 0.4 * np.sin(2 * np.pi * 1000 * np.linspace(0, 0.05, 2048)).astype(np.float32)

        res = analyze_pcm_samples(samples, sample_rate=sr, channels=1, analyzed_duration=dur)
        self.assertNotEqual(res.assessment, SpectralAssessment.SUSPECTED_TRANSCODE)

    def test_spectral_confidence_is_bounded_and_consistent(self):
        """7b. Confidence is strictly bounded [0.0, 100.0] and represents internal consistency."""
        sr = 44100
        noise = np.random.uniform(-0.2, 0.2, sr * 4).astype(np.float32)
        res = analyze_pcm_samples(noise, sample_rate=sr, channels=1, analyzed_duration=4.0)

        self.assertGreaterEqual(res.confidence, 0.0)
        self.assertLessEqual(res.confidence, 100.0)

    def test_legacy_cutoff_wrapper_cannot_upgrade_unknown_to_no_lossy_evidence(self):
        """8b. estimate_spectral_cutoff wrapper returns 0.0, 0.0 on unknown and cannot claim full spectrum."""
        with patch("core.quality_analyzer.analyze_spectrum") as mock_analyze:
            mock_analyze.return_value = SpectralResult(
                assessment=SpectralAssessment.UNKNOWN,
                cutoff_hz=None,
                confidence=0.0,
                analyzed_duration=0.0,
                valid_windows=0,
                rms_dbfs=None,
                reason="decode_failed"
            )
            cutoff, conf = estimate_spectral_cutoff("dummy.flac")
            self.assertEqual(cutoff, 0.0)
            self.assertEqual(conf, 0.0)


class TestSessionAndSerializationCompatibility(unittest.TestCase):
    """Session loading compatibility and SpectralResult serialization roundtrip."""

    def test_old_session_without_spectral_assessment_loads_safely(self):
        """20. Deserializing an old session JSON without spectral_assessment defaults to UNKNOWN."""
        old_data = {
            "filepath": "song.flac",
            "format": "FLAC",
            "bitrate": 900,
            "duration": 200.0,
            "is_lossless": True,
            "spectral_cutoff": 22050.0,
            "fake_lossless_confidence": 0.0,
            # Notice: "spectral_assessment" key is absent
        }
        track = AudioTrack.from_dict(old_data)
        self.assertEqual(track.spectral_assessment, SpectralAssessment.UNKNOWN)

    def test_new_spectral_result_roundtrip_serialization(self):
        """21. SpectralResult to_dict and from_dict roundtrip must match exactly."""
        res = SpectralResult(
            assessment=SpectralAssessment.SUSPECTED_TRANSCODE,
            cutoff_hz=16500.0,
            confidence=85.5,
            analyzed_duration=12.0,
            valid_windows=45,
            rms_dbfs=-18.4,
            reason="persistent_lossy_cutoff_detected"
        )
        data = res.to_dict()
        loaded = SpectralResult.from_dict(data)
        self.assertEqual(res, loaded)


if __name__ == "__main__":
    unittest.main()
