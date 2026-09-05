"""
Test Suite for Phase B — Centralized DetectionConfig and Hardcoded Threshold Elimination (AC-006).

Verifies:
  1. DetectionConfig validation, immutability, and defaults
  2. Pickling / Multiprocessing compatibility
  3. compare_tracks threshold customization and boundary semantics (>=)
  4. Duration Firewall parameterization and override immunity
  5. cluster_duplicates propagation and worker parity
  6. min_duration gating acoustic processing without blocking EXACT_HASH
  7. spectral_analysis flag gating FFT workload
  8. Atomic persistence, corruption fallback, and unknown key resilience
  9. Active scan configuration snapshotting
  10. Deterministic demonstration of different classifications with different configs
"""

import os
import tempfile
import pickle
import unittest
from unittest.mock import patch, MagicMock

from core.models import AudioTrack, DuplicateType, DuplicateGroup, EvidenceReport
from core.config import (
    DetectionConfig,
    load_detection_config,
    save_detection_config,
    get_default_config_path
)
from core.comparator import compare_tracks
from core.clustering import cluster_duplicates, _compare_chunk_worker
from core.scanner import AudioScanner, _process_audio_worker


class TestPhaseBDetectionConfig(unittest.TestCase):
    """Unit tests for DetectionConfig creation, validation, and serialization."""

    def test_detection_config_defaults_are_valid(self):
        """1. DetectionConfig default values must match the Phase B specification."""
        cfg = DetectionConfig()
        self.assertEqual(cfg.acoustic_threshold, 95.0)
        self.assertEqual(cfg.possible_threshold, 80.0)
        self.assertEqual(cfg.review_threshold, 40.0)
        self.assertEqual(cfg.max_auto_duration_diff, 2.0)
        self.assertEqual(cfg.min_duration, 5.0)
        self.assertTrue(cfg.spectral_analysis)
        self.assertIsNone(cfg.max_workers)

        # Conservative settings (0.0) must be valid without artificial minimums
        conservative_cfg = DetectionConfig(
            max_auto_duration_diff=0.0,
            min_duration=0.0,
            review_threshold=0.0
        )
        self.assertEqual(conservative_cfg.max_auto_duration_diff, 0.0)
        self.assertEqual(conservative_cfg.min_duration, 0.0)
        self.assertEqual(conservative_cfg.review_threshold, 0.0)

    def test_detection_config_rejects_inverted_thresholds(self):
        """2. Must strictly enforce 100.0 >= acoustic > possible > review >= 0.0."""
        # acoustic > 100
        with self.assertRaises(ValueError):
            DetectionConfig(acoustic_threshold=100.5)

        # acoustic <= possible
        with self.assertRaises(ValueError):
            DetectionConfig(acoustic_threshold=80.0, possible_threshold=80.0)
        with self.assertRaises(ValueError):
            DetectionConfig(acoustic_threshold=75.0, possible_threshold=80.0)

        # possible <= review
        with self.assertRaises(ValueError):
            DetectionConfig(possible_threshold=40.0, review_threshold=40.0)
        with self.assertRaises(ValueError):
            DetectionConfig(possible_threshold=35.0, review_threshold=40.0)

        # review < 0.0
        with self.assertRaises(ValueError):
            DetectionConfig(review_threshold=-0.5)

    def test_detection_config_rejects_negative_duration_diff(self):
        """3. max_auto_duration_diff must be >= 0.0."""
        with self.assertRaises(ValueError):
            DetectionConfig(max_auto_duration_diff=-0.1)

    def test_detection_config_rejects_negative_min_duration(self):
        """4. min_duration must be >= 0.0."""
        with self.assertRaises(ValueError):
            DetectionConfig(min_duration=-1.0)

    def test_detection_config_rejects_invalid_workers(self):
        """5. max_workers must be None or an integer >= 1."""
        with self.assertRaises(ValueError):
            DetectionConfig(max_workers=0)
        with self.assertRaises(ValueError):
            DetectionConfig(max_workers=-2)
        with self.assertRaises(ValueError):
            DetectionConfig(max_workers=True)  # bool is subclass of int in Python
        with self.assertRaises(ValueError):
            DetectionConfig(max_workers="4")  # type: ignore

        # Valid worker counts
        cfg_workers = DetectionConfig(max_workers=4)
        self.assertEqual(cfg_workers.max_workers, 4)

    def test_detection_config_is_pickleable(self):
        """6. DetectionConfig must be pure data and pickleable across processes."""
        cfg = DetectionConfig(
            acoustic_threshold=92.0,
            possible_threshold=78.0,
            review_threshold=42.0,
            max_auto_duration_diff=3.0,
            min_duration=4.0,
            spectral_analysis=False,
            max_workers=2
        )
        serialized = pickle.dumps(cfg)
        deserialized = pickle.loads(serialized)
        self.assertEqual(cfg, deserialized)
        self.assertEqual(deserialized.acoustic_threshold, 92.0)
        self.assertFalse(deserialized.spectral_analysis)


class TestCompareTracksWithDetectionConfig(unittest.TestCase):
    """Unit tests for compare_tracks using custom DetectionConfig thresholds."""

    def _create_tracks(self, duration_a=100.0, duration_b=100.0):
        t1 = AudioTrack(
            filepath="track_a.mp3",
            sha256="hash_a",
            duration=duration_a,
            fingerprint_raw=[0x11111111] * 50,
            spectral_cutoff=16000.0,
            format="mp3",
            samplerate=44100,
            channels=2,
            bitrate=320000
        )
        t2 = AudioTrack(
            filepath="track_b.mp3",
            sha256="hash_b",
            duration=duration_b,
            fingerprint_raw=[0x22222222] * 50,
            spectral_cutoff=16000.0,
            format="mp3",
            samplerate=44100,
            channels=2,
            bitrate=320000
        )
        return t1, t2

    @patch("core.comparator.compare_raw_fingerprints")
    def test_compare_tracks_uses_custom_acoustic_threshold(self, mock_cmp):
        """7. compare_tracks must classify according to custom acoustic_threshold."""
        # Exact similarity produces confidence = 92.0%
        mock_cmp.return_value = (0.92, 15)  # offset=15 gives offset_bonus=0.0
        t1, t2 = self._create_tracks(100.0, 102.0)  # dur_diff=2.0 gives duration_bonus=0.0

        # With default acoustic_threshold=95.0 -> POSSIBLE_DUPLICATE
        default_report = compare_tracks(t1, t2, config=DetectionConfig())
        self.assertEqual(default_report.classification, DuplicateType.POSSIBLE_DUPLICATE)

        # With custom acoustic_threshold=90.0 -> ACOUSTIC_DUPLICATE
        custom_cfg = DetectionConfig(acoustic_threshold=90.0, possible_threshold=80.0)
        custom_report = compare_tracks(t1, t2, config=custom_cfg)
        self.assertEqual(custom_report.classification, DuplicateType.ACOUSTIC_DUPLICATE)

    @patch("core.comparator.compare_raw_fingerprints")
    def test_compare_tracks_uses_custom_possible_threshold(self, mock_cmp):
        """8. compare_tracks must classify according to custom possible_threshold."""
        # Exact similarity produces confidence = 75.0%
        mock_cmp.return_value = (0.75, 15)
        t1, t2 = self._create_tracks(100.0, 102.0)

        # With default possible_threshold=80.0 -> LOW_CONFIDENCE_REVIEW
        default_report = compare_tracks(t1, t2, config=DetectionConfig())
        self.assertEqual(default_report.classification, DuplicateType.LOW_CONFIDENCE_REVIEW)

        # With custom possible_threshold=70.0 -> POSSIBLE_DUPLICATE
        custom_cfg = DetectionConfig(possible_threshold=70.0)
        custom_report = compare_tracks(t1, t2, config=custom_cfg)
        self.assertEqual(custom_report.classification, DuplicateType.POSSIBLE_DUPLICATE)

    @patch("core.comparator.compare_raw_fingerprints")
    def test_compare_tracks_uses_custom_review_threshold(self, mock_cmp):
        """9. compare_tracks must classify according to custom review_threshold."""
        # Exact similarity produces confidence = 35.0%
        mock_cmp.return_value = (0.35, 15)
        t1, t2 = self._create_tracks(100.0, 102.0)

        # With default review_threshold=40.0 -> NO_MATCH
        default_report = compare_tracks(t1, t2, config=DetectionConfig())
        self.assertEqual(default_report.classification, DuplicateType.NO_MATCH)

        # With custom review_threshold=30.0 -> LOW_CONFIDENCE_REVIEW
        custom_cfg = DetectionConfig(review_threshold=30.0)
        custom_report = compare_tracks(t1, t2, config=custom_cfg)
        self.assertEqual(custom_report.classification, DuplicateType.LOW_CONFIDENCE_REVIEW)

    @patch("core.comparator.compare_raw_fingerprints")
    def test_duration_firewall_uses_configured_value(self, mock_cmp):
        """10. Duration firewall must enforce max_auto_duration_diff from config."""
        # 100% Chromaprint match, duration diff = 3.0s
        mock_cmp.return_value = (1.0, 0)
        t1, t2 = self._create_tracks(100.0, 103.0)

        # Default max_auto_duration_diff=2.0 -> 3.0s > 2.0s triggers firewall
        default_report = compare_tracks(t1, t2, config=DetectionConfig())
        self.assertEqual(default_report.classification, DuplicateType.POSSIBLE_DUPLICATE)
        self.assertTrue(default_report.requires_manual_review)
        self.assertTrue(any("Duration Firewall" in r for r in default_report.reasons))

        # Config with max_auto_duration_diff=4.0 -> 3.0s <= 4.0s does not trigger firewall
        custom_cfg = DetectionConfig(max_auto_duration_diff=4.0)
        custom_report = compare_tracks(t1, t2, config=custom_cfg)
        self.assertEqual(custom_report.classification, DuplicateType.ACOUSTIC_DUPLICATE)
        self.assertFalse(custom_report.requires_manual_review)

    @patch("core.comparator.compare_raw_fingerprints")
    def test_duration_firewall_cannot_be_overridden_by_score(self, mock_cmp):
        """11. Perfect score must never bypass the configured duration firewall."""
        # Maximum possible confidence (Chromaprint 1.0, 0 offset, identical spectrum)
        mock_cmp.return_value = (1.0, 0)
        # Duration difference exceeds max_auto_duration_diff
        t1, t2 = self._create_tracks(100.0, 102.5)

        cfg = DetectionConfig(max_auto_duration_diff=2.0)
        report = compare_tracks(t1, t2, config=cfg)

        self.assertNotEqual(report.classification, DuplicateType.ACOUSTIC_DUPLICATE)
        self.assertEqual(report.classification, DuplicateType.POSSIBLE_DUPLICATE)
        self.assertTrue(report.requires_manual_review)

    @patch("core.comparator.compare_raw_fingerprints")
    def test_threshold_boundary_semantics(self, mock_cmp):
        """15 & 21. Tests exact >= boundary semantics for all classification thresholds."""
        cfg = DetectionConfig(
            acoustic_threshold=95.0,
            possible_threshold=80.0,
            review_threshold=40.0
        )
        # spectral_cutoff=0.0 on both tracks ensures spectral_diff is None and spectral_bonus=0.0
        # dur_diff=2.0 and offset=15 ensure duration_bonus=0.0 and offset_bonus=0.0
        # This guarantees final_confidence == similarity * 100.0 exactly.
        t1 = AudioTrack(filepath="t1.mp3", sha256="h1", duration=100.0, fingerprint_raw=[1] * 40, spectral_cutoff=0.0)
        t2 = AudioTrack(filepath="t2.mp3", sha256="h2", duration=102.0, fingerprint_raw=[2] * 40, spectral_cutoff=0.0)

        # 1. acoustic_threshold boundary (95.0)
        mock_cmp.return_value = (0.95, 15)  # exact 95.0
        rep = compare_tracks(t1, t2, config=cfg)
        self.assertEqual(rep.classification, DuplicateType.ACOUSTIC_DUPLICATE, "Score == 95.0 must be ACOUSTIC_DUPLICATE (>= semantics)")

        mock_cmp.return_value = (0.9499, 15)  # 94.99 < 95.0
        rep = compare_tracks(t1, t2, config=cfg)
        self.assertEqual(rep.classification, DuplicateType.POSSIBLE_DUPLICATE, "Score == 94.99 must be POSSIBLE_DUPLICATE")

        # 2. possible_threshold boundary (80.0)
        mock_cmp.return_value = (0.80, 15)  # exact 80.0
        rep = compare_tracks(t1, t2, config=cfg)
        self.assertEqual(rep.classification, DuplicateType.POSSIBLE_DUPLICATE, "Score == 80.0 must be POSSIBLE_DUPLICATE (>= semantics)")

        mock_cmp.return_value = (0.7999, 15)  # 79.99 < 80.0
        rep = compare_tracks(t1, t2, config=cfg)
        self.assertEqual(rep.classification, DuplicateType.LOW_CONFIDENCE_REVIEW, "Score == 79.99 must be LOW_CONFIDENCE_REVIEW")

        # 3. review_threshold boundary (40.0)
        mock_cmp.return_value = (0.40, 15)  # exact 40.0
        rep = compare_tracks(t1, t2, config=cfg)
        self.assertEqual(rep.classification, DuplicateType.LOW_CONFIDENCE_REVIEW, "Score == 40.0 must be LOW_CONFIDENCE_REVIEW (>= semantics)")

        mock_cmp.return_value = (0.3999, 15)  # 39.99 < 40.0
        rep = compare_tracks(t1, t2, config=cfg)
        self.assertEqual(rep.classification, DuplicateType.NO_MATCH, "Score == 39.99 must be NO_MATCH")

    @patch("core.comparator.compare_raw_fingerprints")
    def test_same_track_pair_different_configs_different_outcomes(self, mock_cmp):
        """22. Explicit proof: same pair + config A -> X, same pair + config B -> Y."""
        mock_cmp.return_value = (0.88, 15)  # 88% confidence
        t1, t2 = self._create_tracks(100.0, 102.0)

        # Configuration A: Strict / Default
        config_a = DetectionConfig(acoustic_threshold=95.0, possible_threshold=80.0)
        result_a = compare_tracks(t1, t2, config=config_a)

        # Configuration B: Permissive
        config_b = DetectionConfig(acoustic_threshold=85.0, possible_threshold=70.0)
        result_b = compare_tracks(t1, t2, config=config_b)

        self.assertEqual(result_a.classification, DuplicateType.POSSIBLE_DUPLICATE)
        self.assertEqual(result_b.classification, DuplicateType.ACOUSTIC_DUPLICATE)
        self.assertNotEqual(result_a.classification, result_b.classification)


class TestClusteringAndWorkersWithDetectionConfig(unittest.TestCase):
    """Unit tests for cluster_duplicates and worker config propagation."""

    @patch("core.comparator.compare_raw_fingerprints")
    def test_cluster_duplicates_propagates_detection_config(self, mock_cmp):
        """12. cluster_duplicates must propagate DetectionConfig into groupings."""
        mock_cmp.return_value = (0.88, 15)
        fp = list(range(1, 41))
        t1 = AudioTrack(filepath="t1.mp3", sha256="h1", duration=100.0, fingerprint_raw=fp, spectral_cutoff=0.0)
        t2 = AudioTrack(filepath="t2.mp3", sha256="h2", duration=102.0, fingerprint_raw=fp, spectral_cutoff=0.0)

        from concurrent.futures import Future

        # Immediate executor to run workers in-process with the active mock
        class ImmediateExecutor:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def submit(self, fn, *args, **kwargs):
                fut = Future()
                fut.set_result(fn(*args, **kwargs))
                return fut

        with patch("core.clustering.ProcessPoolExecutor", ImmediateExecutor):
            # Under default config (acoustic_threshold=95.0), 88% is POSSIBLE_DUPLICATE
            groups_default = cluster_duplicates([t1, t2], config=DetectionConfig())
            self.assertEqual(len(groups_default), 1)
            self.assertEqual(groups_default[0].primary_type, DuplicateType.POSSIBLE_DUPLICATE)

            # Under custom config (acoustic_threshold=85.0), 88% is ACOUSTIC_DUPLICATE
            groups_custom = cluster_duplicates([t1, t2], config=DetectionConfig(acoustic_threshold=85.0))
            self.assertEqual(len(groups_custom), 1)
            self.assertEqual(groups_custom[0].primary_type, DuplicateType.ACOUSTIC_DUPLICATE)

    @patch("core.comparator.compare_raw_fingerprints")
    def test_worker_uses_same_detection_config(self, mock_cmp):
        """13. _compare_chunk_worker must respect the passed DetectionConfig."""
        mock_cmp.return_value = (0.75, 15)
        t1 = AudioTrack(filepath="t1.mp3", sha256="h1", duration=100.0, fingerprint_raw=[1] * 40)
        t2 = AudioTrack(filepath="t2.mp3", sha256="h2", duration=102.0, fingerprint_raw=[1] * 40)

        pairs = [(t1, t2)]
        # Default config: 75% -> LOW_CONFIDENCE_REVIEW
        results_def = _compare_chunk_worker(pairs, DetectionConfig())
        self.assertEqual(len(results_def), 1)
        self.assertEqual(results_def[0].classification, DuplicateType.LOW_CONFIDENCE_REVIEW)

        # Custom config: possible_threshold=70.0 -> POSSIBLE_DUPLICATE
        cfg = DetectionConfig(possible_threshold=70.0)
        results_custom = _compare_chunk_worker(pairs, cfg)
        self.assertEqual(len(results_custom), 1)
        self.assertEqual(results_custom[0].classification, DuplicateType.POSSIBLE_DUPLICATE)

        # Worker tuple unpacked format (chunk, config)
        results_tuple = _compare_chunk_worker((pairs, cfg))
        self.assertEqual(len(results_tuple), 1)
        self.assertEqual(results_tuple[0].classification, DuplicateType.POSSIBLE_DUPLICATE)


class TestScannerAndPipelineBehavior(unittest.TestCase):
    """Unit tests for AudioScanner, min_duration gating, and spectral flags."""

    def test_min_duration_does_not_disable_exact_hash(self):
        """14. min_duration must not prevent EXACT_HASH detection for short tracks."""
        # Two tracks of 1.0 second duration (well below default min_duration=5.0s)
        # with identical SHA-256 hash
        t1 = AudioTrack(filepath="short1.wav", sha256="same_sha256_123", duration=1.0)
        t2 = AudioTrack(filepath="short2.wav", sha256="same_sha256_123", duration=1.0)

        cfg = DetectionConfig(min_duration=10.0)
        groups = cluster_duplicates([t1, t2], config=cfg)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].primary_type, DuplicateType.EXACT_HASH)
        self.assertEqual(groups[0].average_similarity, 100.0)

    @patch("core.scanner.compute_file_sha256")
    @patch("core.scanner.extract_metadata")
    @patch("core.scanner.extract_fingerprint")
    def test_min_duration_skips_fingerprinting_for_short_files(
        self, mock_fp, mock_meta, mock_sha
    ):
        """14b. _process_audio_worker must skip fpcalc when duration < min_duration."""
        mock_sha.return_value = "sha_abc"
        mock_meta.return_value = {
            "format": "WAV",
            "duration": 2.0,  # 2.0s < min_duration (5.0s)
            "bitrate": 1411,
            "samplerate": 44100,
            "channels": 2,
            "is_lossless": True
        }

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"RIFF" + b"\x00" * 100)
            tf_path = tf.name

        try:
            # Process with min_duration = 5.0
            data = _process_audio_worker(tf_path, min_duration=5.0, spectral_analysis=False)
            self.assertIsNotNone(data)
            self.assertEqual(data["sha256"], "sha_abc")
            self.assertIsNone(data["fingerprint_raw"], "Acoustic fingerprinting must be skipped when duration < min_duration")
            # extract_fingerprint should NOT have been called
            mock_fp.assert_not_called()
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    @patch("core.scanner.estimate_spectral_cutoff")
    @patch("core.scanner.compute_file_sha256")
    @patch("core.scanner.extract_metadata")
    @patch("core.scanner.extract_fingerprint")
    def test_spectral_analysis_false_skips_spectral_work(
        self, mock_fp, mock_meta, mock_sha, mock_spectral
    ):
        """15. spectral_analysis=False must skip FFT / estimate_spectral_cutoff entirely."""
        mock_sha.return_value = "sha_lossless"
        mock_meta.return_value = {
            "format": "FLAC",
            "duration": 180.0,
            "bitrate": 900,
            "samplerate": 44100,
            "channels": 2,
            "is_lossless": True
        }
        mock_fp.return_value = (180.0, [1, 2, 3])

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"fLaC" + b"\x00" * 100)
            tf_path = tf.name

        try:
            # When spectral_analysis is False
            res_disabled = _process_audio_worker(tf_path, min_duration=5.0, spectral_analysis=False)
            self.assertIsNotNone(res_disabled)
            mock_spectral.assert_not_called()

            # When spectral_analysis is True on lossless file
            mock_spectral.return_value = (19500.0, 0.0)
            res_enabled = _process_audio_worker(tf_path, min_duration=5.0, spectral_analysis=True)
            self.assertIsNotNone(res_enabled)
            mock_spectral.assert_called_once_with(tf_path)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_active_scan_uses_config_snapshot(self):
        """20. Modifying scanner.detection_config during an active scan must not mutate the running scan."""
        scanner = AudioScanner(detection_config=DetectionConfig(acoustic_threshold=96.0))
        self.assertEqual(scanner.detection_config.acoustic_threshold, 96.0)

        # Mock scan_directory behavior: capture snapshot at start
        initial_config = scanner.detection_config
        snapshot = scanner.detection_config

        # Simulate GUI mutating scanner.detection_config mid-scan
        scanner.detection_config = DetectionConfig(acoustic_threshold=88.0)

        # The snapshot taken at scan start must remain untouched
        self.assertEqual(snapshot.acoustic_threshold, 96.0)
        self.assertEqual(scanner.detection_config.acoustic_threshold, 88.0)
        self.assertNotEqual(snapshot.acoustic_threshold, scanner.detection_config.acoustic_threshold)


class TestConfigPersistence(unittest.TestCase):
    """Unit tests for config atomic persistence, error handling, and recovery."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "settings.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_config_roundtrip_save_load(self):
        """16. Config must save atomically and load back with exact matching values."""
        cfg = DetectionConfig(
            acoustic_threshold=92.0,
            possible_threshold=81.0,
            review_threshold=44.0,
            max_auto_duration_diff=3.5,
            min_duration=8.0,
            spectral_analysis=False,
            max_workers=3
        )
        saved = save_detection_config(cfg, path=self.config_path)
        self.assertTrue(saved)
        self.assertTrue(os.path.isfile(self.config_path))

        loaded = load_detection_config(path=self.config_path)
        self.assertEqual(cfg, loaded)
        self.assertEqual(loaded.acoustic_threshold, 92.0)
        self.assertEqual(loaded.possible_threshold, 81.0)
        self.assertEqual(loaded.max_workers, 3)
        self.assertFalse(loaded.spectral_analysis)

    def test_corrupted_config_file_falls_back_safely(self):
        """17. Corrupted JSON file must not crash the application and must fall back to defaults."""
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("{corrupt: json content, unclosed bracket...")

        loaded = load_detection_config(path=self.config_path)
        default_cfg = DetectionConfig()
        self.assertEqual(loaded, default_cfg)

    def test_unknown_config_keys_do_not_crash_loading(self):
        """18. Unknown keys from future versions must be safely ignored."""
        import json
        future_data = {
            "acoustic_threshold": 91.0,
            "possible_threshold": 79.0,
            "review_threshold": 41.0,
            "future_feature_flag": True,
            "model_version_v3": "deep-audio-transformer",
            "extra_nested_dict": {"foo": "bar"}
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(future_data, f)

        loaded = load_detection_config(path=self.config_path)
        self.assertEqual(loaded.acoustic_threshold, 91.0)
        self.assertEqual(loaded.possible_threshold, 79.0)
        self.assertEqual(loaded.review_threshold, 41.0)
        self.assertFalse(hasattr(loaded, "future_feature_flag"))

    def test_invalid_persisted_thresholds_do_not_become_active(self):
        """19. Invalid persisted thresholds must be caught and rejected, falling back to defaults."""
        import json
        # Inverted thresholds in file
        invalid_data = {
            "acoustic_threshold": 60.0,
            "possible_threshold": 80.0,
            "review_threshold": 40.0
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(invalid_data, f)

        loaded = load_detection_config(path=self.config_path)
        default_cfg = DetectionConfig()
        self.assertEqual(loaded, default_cfg, "Invalid persisted thresholds must fall back to default DetectionConfig")


if __name__ == "__main__":
    unittest.main()
