import os
import sys
import shutil
import tempfile
import unittest
import json
import csv
import subprocess
from unittest.mock import patch

# Add parent dir
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tests.test_end_to_end import generate_synthetic_audio
from scipy.io import wavfile
import numpy as np

from scripts.evaluation_runner import run_evaluation, calculate_metrics
from core.models import DuplicateType

class TestValidationFramework(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="audio_dup_fw_test_")
        cls.sample_rate = 44100
        cls.manifest_path = os.path.join(cls.test_dir, "manifest.csv")
        cls.report_path = os.path.join(cls.test_dir, "validation_report.json")
        
        # We generate a few representative cases to verify the runner logic
        # without making the test take forever.
        
        # Master Song A
        song_a_data = generate_synthetic_audio(duration=45.0, melody_type=1)
        cls.song_a_wav = os.path.join(cls.test_dir, "SongA.wav")
        wavfile.write(cls.song_a_wav, cls.sample_rate, song_a_data)
        
        # 1. Exact Copy (EXACT_HASH)
        cls.song_a_copy = os.path.join(cls.test_dir, "SongA_Copy.wav")
        shutil.copy2(cls.song_a_wav, cls.song_a_copy)
        
        # 2. Metadata Diff (EXACT_AUDIO)
        cls.song_a_meta = os.path.join(cls.test_dir, "SongA_Meta.wav")
        shutil.copy2(cls.song_a_wav, cls.song_a_meta)
        # Change a byte at the end to break the hash but keep audio exact
        with open(cls.song_a_meta, "ab") as f:
            f.write(b"metadata123")
            
        # 3. Different Bitrate MP3 (ACOUSTIC_DUPLICATE)
        cls.song_a_mp3 = os.path.join(cls.test_dir, "SongA.mp3")
        subprocess.run(["ffmpeg", "-y", "-i", cls.song_a_wav, "-b:a", "192k", cls.song_a_mp3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 4. Different Song (NO_MATCH)
        song_b_data = generate_synthetic_audio(duration=45.0, melody_type=2)
        cls.song_b_wav = os.path.join(cls.test_dir, "SongB.wav")
        wavfile.write(cls.song_b_wav, cls.sample_rate, song_b_data)
        
        # 5. Radio Edit / Truncated (POSSIBLE_DUPLICATE)
        # 45s vs 14s -> 31s diff -> -10 penalty. Base 100 -> 94 -> POSSIBLE_DUPLICATE
        radio_edit = song_a_data[:int(cls.sample_rate * 14.0)]
        cls.song_a_short = os.path.join(cls.test_dir, "SongA_Short.wav")
        wavfile.write(cls.song_a_short, cls.sample_rate, radio_edit)
        
        # Create Manifest
        rows = [
            {"track_a_path": "SongA.wav", "track_b_path": "SongA_Copy.wav", "expected_category": "EXACT_HASH"},
            {"track_a_path": "SongA.wav", "track_b_path": "SongA_Meta.wav", "expected_category": "EXACT_AUDIO"},
            {"track_a_path": "SongA.wav", "track_b_path": "SongA.mp3", "expected_category": "ACOUSTIC_DUPLICATE"},
            {"track_a_path": "SongA.wav", "track_b_path": "SongB.wav", "expected_category": "NO_MATCH"},
            {"track_a_path": "SongA.wav", "track_b_path": "SongA_Short.wav", "expected_category": "POSSIBLE_DUPLICATE"},
            # Error case (file doesn't exist)
            {"track_a_path": "SongA.wav", "track_b_path": "Missing.wav", "expected_category": "NO_MATCH"},
            # Invalid category case
            {"track_a_path": "SongA.wav", "track_b_path": "SongA_Copy.wav", "expected_category": "INVALID_CAT"},
        ]
        
        with open(cls.manifest_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["track_a_path", "track_b_path", "expected_category"])
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_framework_execution(self):
        # Run evaluation runner
        run_evaluation(self.manifest_path, self.test_dir, self.report_path)
        
        self.assertTrue(os.path.exists(self.report_path), "JSON report was not generated")
        import shutil
        shutil.copy(self.report_path, "scratch_validation_report.json")
        
        with open(self.report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
            
        summary = report["summary"]
        self.assertEqual(summary["total_cases"], 7)
        self.assertEqual(summary["error_cases"], 2) # Missing.wav and INVALID_CAT
        self.assertEqual(summary["evaluated_cases"], 5)
        
        # All 5 valid cases should be correctly predicted
        if summary["exact_matches"] != 5:
            print("MISCLASSIFICATIONS:")
            import pprint
            pprint.pprint(report["misclassifications"])
        self.assertEqual(summary["exact_matches"], 5)
        self.assertEqual(summary["accuracy"], 1.0)
        
        # Verify confusion matrix has counts
        matrix = report["confusion_matrix"]
        self.assertEqual(matrix["EXACT_HASH"]["EXACT_HASH"], 1)
        self.assertEqual(matrix["EXACT_AUDIO"]["EXACT_AUDIO"], 1)
        self.assertEqual(matrix["ACOUSTIC_DUPLICATE"]["ACOUSTIC_DUPLICATE"], 1)
        self.assertEqual(matrix["NO_MATCH"]["NO_MATCH"], 1)
        self.assertEqual(matrix["POSSIBLE_DUPLICATE"]["POSSIBLE_DUPLICATE"], 1)
        
        # Verify misclassifications is empty
        self.assertEqual(len(report["misclassifications"]), 0)
        
        # Verify errors log
        self.assertEqual(len(report["errors"]), 2)
        error_msgs = [e["error"] for e in report["errors"]]
        self.assertTrue(any("Invalid expected_category" in msg for msg in error_msgs))
        self.assertTrue(any("do not exist" in msg for msg in error_msgs))

    def test_metrics_calculation(self):
        # 3 classes: A, B, C for simplicity mapped to Enums
        A = DuplicateType.EXACT_HASH.value
        B = DuplicateType.POSSIBLE_DUPLICATE.value
        C = DuplicateType.NO_MATCH.value
        categories = [e.value for e in DuplicateType]
        
        matrix = {cat: {c: 0 for c in categories} for cat in categories}
        matrix[A][A] = 10
        matrix[A][B] = 2
        matrix[B][A] = 1
        matrix[B][B] = 5
        matrix[B][C] = 2
        matrix[C][B] = 1
        matrix[C][C] = 20
        
        misclassifications = [
            {"expected": C, "predicted": B}, # NO_MATCH -> POSSIBLE_DUPLICATE (CRITICAL)
            {"expected": A, "predicted": B}  # EXACT_HASH -> POSSIBLE_DUPLICATE (MODERATE)
        ]
        
        metrics = calculate_metrics(matrix, misclassifications, 41)
        
        # Test class A (EXACT_HASH)
        # TP = 10
        # FP = 1 (from B)
        # FN = 2 (to B)
        # TN = 41 - (10 + 1 + 2) = 28
        # Precision = 10 / 11 = 0.9091
        # Recall = 10 / 12 = 0.8333
        
        self.assertEqual(metrics["per_class"][A]["tp"], 10)
        self.assertEqual(metrics["per_class"][A]["fp"], 1)
        self.assertEqual(metrics["per_class"][A]["fn"], 2)
        self.assertEqual(metrics["per_class"][A]["tn"], 28)
        self.assertAlmostEqual(metrics["per_class"][A]["precision"], 0.9091, places=3)
        self.assertAlmostEqual(metrics["per_class"][A]["recall"], 0.8333, places=3)
        
        # Test critical errors
        self.assertEqual(metrics["critical_errors_count"], 1)
        self.assertEqual(misclassifications[0]["severity"], "CRITICAL")
        self.assertEqual(misclassifications[1]["severity"], "MODERATE")

if __name__ == '__main__':
    unittest.main()
