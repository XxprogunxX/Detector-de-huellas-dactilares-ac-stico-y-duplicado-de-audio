"""
Comprehensive Synthetic Scalability & Ground Truth Benchmark.
Explicit Description:
'100k synthetic candidate-generation/clustering benchmark'
Measures the in-memory candidate generation, shingle indexing, memory bounding,
Union-Find graph clustering, and duplicate recall.
Does NOT measure real physical disk audio I/O or full FFmpeg decode for 100,000 files.
"""

import os
import sys

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import random
import argparse
import threading
from typing import List, Tuple, Set, Dict, Any
from collections import defaultdict

try:
    import psutil
except ImportError:
    psutil = None

from core.models import AudioTrack, DuplicateGroup, DuplicateType, ScanCoverageReport
from core.clustering import cluster_duplicates
from core.config import DetectionConfig


def get_total_rss_mb() -> float:
    """Returns current process + child processes RSS in MB via psutil."""
    if not psutil:
        return 0.0
    try:
        proc = psutil.Process()
        total_rss = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                total_rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total_rss / (1024 * 1024)
    except Exception:
        return 0.0


class MemoryMonitor:
    """Samples process tree RSS at high frequency to capture true peak RAM."""
    def __init__(self, interval: float = 0.05):
        self.interval = interval
        self.peak_rss_mb = get_total_rss_mb()
        self._running = False
        self._thread = None

    def _monitor(self):
        while self._running:
            current = get_total_rss_mb()
            if current > self.peak_rss_mb:
                self.peak_rss_mb = current
            time.sleep(self.interval)

    def start(self):
        self.peak_rss_mb = get_total_rss_mb()
        self._running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self) -> float:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        final = get_total_rss_mb()
        if final > self.peak_rss_mb:
            self.peak_rss_mb = final
        return self.peak_rss_mb


def generate_synthetic_dataset(
    num_tracks: int,
    scenario: str = "B",
    seed: int = 42
) -> Tuple[List[AudioTrack], Set[Tuple[str, str]]]:
    """
    Generates synthetic AudioTracks with deterministic ground truth duplicate pairs.

    Scenarios:
    - A: Easy / Random (random distinct shingles, very low collision)
    - B: Normal Library (clusters of 2-4 duplicates, mixed diverse tracks)
    - C: Dense Duplicates (large duplicate clusters of 10-20 versions)
    - D: Adversarial (heavy collisions on common tokens to stress-test memory bounds)
    """
    rng = random.Random(seed)
    tracks: List[AudioTrack] = []
    ground_truth_pairs: Set[Tuple[str, str]] = set()

    if scenario == "A":
        # Purely random fingerprints
        for i in range(num_tracks):
            fp = [rng.randint(1, 0x7FFFFFFF) for _ in range(80)]
            tracks.append(AudioTrack(
                filepath=f"track_A_{i:06d}.flac",
                filesize=10_000_000 + i,
                duration=180.0,
                fingerprint_raw=fp
            ))

    elif scenario == "B":
        # Normal library: 75% unique songs, 25% duplicate pairs (cluster sizes 2-4)
        curr_idx = 0
        while curr_idx < num_tracks:
            is_dup = rng.random() < 0.25 and (num_tracks - curr_idx) >= 2
            if is_dup:
                group_size = min(rng.randint(2, 4), num_tracks - curr_idx)
                base_fp = [rng.randint(1, 0x7FFFFFFF) for _ in range(100)]
                group_paths = []
                for g in range(group_size):
                    path = f"song_B_{curr_idx:06d}.flac"
                    # Mutate 2-5% of tokens to simulate realistic compression/re-encode
                    track_fp = list(base_fp)
                    for m in range(rng.randint(2, 5)):
                        track_fp[rng.randint(0, 99)] = rng.randint(1, 0x7FFFFFFF)
                    tracks.append(AudioTrack(
                        filepath=path,
                        filesize=15_000_000 + curr_idx,
                        duration=200.0,
                        fingerprint_raw=track_fp
                    ))
                    group_paths.append(path)
                    curr_idx += 1

                for i in range(len(group_paths)):
                    for j in range(i + 1, len(group_paths)):
                        p1, p2 = (group_paths[i], group_paths[j]) if group_paths[i] < group_paths[j] else (group_paths[j], group_paths[i])
                        ground_truth_pairs.add((p1, p2))
            else:
                fp = [rng.randint(1, 0x7FFFFFFF) for _ in range(80)]
                tracks.append(AudioTrack(
                    filepath=f"song_B_{curr_idx:06d}.flac",
                    filesize=12_000_000 + curr_idx,
                    duration=210.0,
                    fingerprint_raw=fp
                ))
                curr_idx += 1

    elif scenario == "C":
        # Dense duplicates: large clusters (10-15 versions per cluster)
        curr_idx = 0
        while curr_idx < num_tracks:
            cluster_size = min(rng.randint(10, 15), num_tracks - curr_idx)
            base_fp = [rng.randint(1, 0x7FFFFFFF) for _ in range(100)]
            cluster_paths = []
            for c in range(cluster_size):
                path = f"dense_C_{curr_idx:06d}.flac"
                track_fp = list(base_fp)
                for _ in range(rng.randint(1, 4)):
                    track_fp[rng.randint(0, 99)] = rng.randint(1, 0x7FFFFFFF)
                tracks.append(AudioTrack(
                    filepath=path,
                    filesize=20_000_000 + curr_idx,
                    duration=190.0,
                    fingerprint_raw=track_fp
                ))
                cluster_paths.append(path)
                curr_idx += 1

            for i in range(len(cluster_paths)):
                for j in range(i + 1, len(cluster_paths)):
                    p1, p2 = (cluster_paths[i], cluster_paths[j]) if cluster_paths[i] < cluster_paths[j] else (cluster_paths[j], cluster_paths[i])
                    ground_truth_pairs.add((p1, p2))

    elif scenario == "D":
        # Adversarial: 10 common intro tokens shared across thousands of tracks to force oversized buckets
        common_tokens = [0x5555AAAA + k for k in range(10)]
        for i in range(num_tracks):
            fp = list(common_tokens) + [rng.randint(1, 0x7FFFFFFF) for _ in range(70)]
            tracks.append(AudioTrack(
                filepath=f"adv_D_{i:06d}.flac",
                filesize=10_000_000 + i,
                duration=180.0,
                fingerprint_raw=fp
            ))

    return tracks, ground_truth_pairs


def run_benchmark_trial(
    num_tracks: int,
    scenario: str = "B",
    max_workers: int = 4
) -> Dict[str, Any]:
    """Runs a single scalability benchmark trial and returns measured metrics."""
    tracks, ground_truth_pairs = generate_synthetic_dataset(num_tracks, scenario=scenario)

    config = DetectionConfig(
        acoustic_threshold=80.0,
        possible_threshold=65.0,
        max_workers=max_workers
    )

    monitor = MemoryMonitor()
    monitor.start()
    t_start = time.monotonic()

    groups, coverage = cluster_duplicates(
        tracks,
        config=config,
        return_coverage=True
    )

    elapsed = time.monotonic() - t_start
    peak_rss = monitor.stop()

    # Calculate Ground Truth metrics
    discovered_pairs = set()
    for g in groups:
        paths = sorted([t.filepath for t in g.tracks])
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                discovered_pairs.add((paths[i], paths[j]))

    if ground_truth_pairs:
        tp = len(discovered_pairs & ground_truth_pairs)
        fp = len(discovered_pairs - ground_truth_pairs)
        fn = len(ground_truth_pairs - discovered_pairs)
        candidate_recall = (tp / len(ground_truth_pairs)) * 100.0
        precision = (tp / len(discovered_pairs) * 100.0) if discovered_pairs else 100.0
    else:
        tp = fp = fn = 0
        candidate_recall = 100.0
        precision = 100.0

    return {
        "scenario": scenario,
        "tracks": num_tracks,
        "elapsed_seconds": elapsed,
        "peak_rss_mb": peak_rss,
        "candidate_pairs_generated": coverage.candidate_pairs_generated,
        "candidate_pairs_retained": coverage.candidate_pairs_retained,
        "candidate_pairs_dropped": coverage.candidate_pairs_dropped,
        "actual_comparisons": coverage.actual_comparisons,
        "oversized_buckets": coverage.oversized_buckets,
        "worker_failures": coverage.worker_failures,
        "is_approximate": coverage.is_approximate,
        "ground_truth_pairs": len(ground_truth_pairs),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "candidate_recall_pct": candidate_recall,
        "precision_pct": precision
    }


def main():
    parser = argparse.ArgumentParser(description="100k synthetic candidate-generation/clustering benchmark")
    parser.add_argument("--scale", type=str, default="quick", choices=["smoke", "quick", "full", "100k"])
    args = parser.parse_args()

    if args.scale == "smoke":
        sizes = [1_000]
    elif args.scale == "quick":
        sizes = [1_000, 5_000, 10_000]
    elif args.scale == "full":
        sizes = [1_000, 5_000, 10_000, 25_000, 50_000]
    elif args.scale == "100k":
        sizes = [100_000]

    print("=" * 80)
    print("AUDIO-CLEANER — 100k Synthetic Candidate-Generation & Clustering Benchmark")
    print("Stage: In-Memory Shingle Ingestion, Bounded Candidate Pairs & Union-Find")
    print(f"Memory Metric: Total Process Tree RSS (Main + Workers via psutil: {psutil is not None})")
    print("=" * 80)

    results = []
    for sz in sizes:
        for scen in (["B", "D"] if sz <= 25000 else ["B"]):
            print(f"\nRunning benchmark: {sz:,} tracks | Scenario: {scen}...")
            res = run_benchmark_trial(sz, scenario=scen)
            results.append(res)
            print(f"  Tracks: {res['tracks']:,} | Time: {res['elapsed_seconds']:.2f}s | Peak RSS: {res['peak_rss_mb']:.1f} MB")
            print(f"  Candidates Gen/Ret/Drop: {res['candidate_pairs_generated']:,} / {res['candidate_pairs_retained']:,} / {res['candidate_pairs_dropped']:,}")
            print(f"  Comparisons: {res['actual_comparisons']:,} | Oversized Buckets: {res['oversized_buckets']} | Approximate: {res['is_approximate']}")
            if res["ground_truth_pairs"] > 0:
                print(f"  Recall: {res['candidate_recall_pct']:.1f}% | Precision: {res['precision_pct']:.1f}% (FN: {res['false_negatives']})")

    print("\n" + "=" * 80)
    print("RESUMEN DE RESULTADOS — SCALABILITY BENCHMARK")
    print("=" * 80)
    print(f"{'Tracks':<10} | {'Scen':<5} | {'Tiempo (s)':<10} | {'Peak RSS':<10} | {'Candidates':<12} | {'Comparisons':<12} | {'Approx':<8} | {'Recall':<8}")
    print("-" * 80)
    for r in results:
        rec_str = f"{r['candidate_recall_pct']:.1f}%" if r['ground_truth_pairs'] > 0 else "N/A"
        print(f"{r['tracks']:<10,d} | {r['scenario']:<5} | {r['elapsed_seconds']:<10.2f} | {r['peak_rss_mb']:<7.1f} MB | {r['candidate_pairs_retained']:<12,d} | {r['actual_comparisons']:<12,d} | {str(r['is_approximate']):<8} | {rec_str:<8}")
    print("=" * 80)


if __name__ == "__main__":
    main()
