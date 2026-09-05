"""
Clustering and Duplicate Grouping Engine using Disjoint-Set, Quality Ranking,
and Memory-Bounded Candidate Generation.
"""

from typing import List, Dict, Set, Tuple, Optional, Any, Union
from collections import defaultdict
import os
import sys
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
import itertools

from core.models import AudioTrack, DuplicateGroup, DuplicateType, FileAction, EvidenceReport, ScanCoverageReport
from core.comparator import compare_tracks
from core.config import DetectionConfig


def _compare_chunk_worker(
    pairs_or_item: Any,
    config: Optional[DetectionConfig] = None
) -> List[EvidenceReport]:
    if isinstance(pairs_or_item, tuple) and len(pairs_or_item) == 2 and isinstance(pairs_or_item[1], DetectionConfig):
        pairs, cfg = pairs_or_item
    else:
        pairs = pairs_or_item
        cfg = config or DetectionConfig()

    results = []
    for t_a, t_b in pairs:
        res = compare_tracks(t_a, t_b, config=cfg)
        if res.classification not in (DuplicateType.NO_MATCH, DuplicateType.UNCERTAIN):
            results.append(res)
    return results


class DisjointSet:
    """Disjoint-Set (Union-Find) with path compression and rank."""
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, item: str) -> str:
        if item not in self.parent:
            self.parent[item] = item
            self.rank[item] = 0
            return item
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, a: str, b: str):
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            if self.rank[root_a] < self.rank[root_b]:
                self.parent[root_a] = root_b
            elif self.rank[root_a] > self.rank[root_b]:
                self.parent[root_b] = root_a
            else:
                self.parent[root_b] = root_a
                self.rank[root_a] += 1


class DuplicateGroupList(list):
    """Subclass of list that carries scan coverage metadata."""
    def __init__(self, items=None, coverage: Optional[ScanCoverageReport] = None):
        super().__init__(items or [])
        self.coverage = coverage or ScanCoverageReport()


def cluster_duplicates(
    tracks: List[AudioTrack],
    progress_callback=None,
    is_cancelled=None,
    config: Optional[DetectionConfig] = None,
    max_bucket_size: int = 500,
    max_pair_hits: int = 500_000,
    return_coverage: bool = False
) -> Union[List[DuplicateGroup], Tuple[List[DuplicateGroup], ScanCoverageReport]]:
    """
    Clusters duplicate audio tracks with deterministic, memory-bounded candidate generation.

    Pipeline:
    1. Exact match clustering via SHA-256 (O(N)) and normalized PCM hash.
    2. Duration-bucketed acoustic subfingerprint indexing with bounded candidate ingestion.
    3. Union-Find graph clustering.
    4. Quality scoring and 'Best File' recommendation per group.

    Guarantees:
    - Memory bounded during candidate generation (prevents runaway RAM on 100k tracks).
    - Deterministic matching for identical inputs.
    - Python 3.10+ executor compatibility (conditional max_tasks_per_child).
    - Worker exception isolation (scan does not abort if a single worker crashes).
    - Exact progress reporting: increments by actual len(chunk).
    """
    config = config or DetectionConfig()
    coverage = ScanCoverageReport()

    if len(tracks) < 2:
        return ([], coverage) if return_coverage else DuplicateGroupList([], coverage)

    track_map: Dict[str, AudioTrack] = {t.filepath: t for t in tracks}
    ds = DisjointSet()
    pair_results: Dict[Tuple[str, str], EvidenceReport] = {}

    # Step 1: Exact Hash Matches (SHA256 and PCM Hash)
    sha_groups = defaultdict(list)
    for t in tracks:
        if t.sha256:
            sha_groups[t.sha256].append(t)
            
    for group in sha_groups.values():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    ds.union(group[i].filepath, group[j].filepath)
                    pair_results[(group[i].filepath, group[j].filepath)] = EvidenceReport(
                        track_a_path=group[i].filepath,
                        track_b_path=group[j].filepath,
                        classification=DuplicateType.EXACT_HASH,
                        confidence=100.0,
                        is_exact_hash=True,
                        reasons=["Duplicado Exacto: Archivos idénticos byte por byte (mismo hash SHA-256)."]
                    )

    hash_groups = defaultdict(list)
    for t in tracks:
        if t.audio_hash:
            hash_groups[t.audio_hash].append(t)
            
    for group in hash_groups.values():
        if len(group) > 1:
            from core.fingerprint import verify_full_normalized_pcm_match
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    track_a = group[i]
                    track_b = group[j]
                    dur_diff = abs(track_a.duration - track_b.duration)
                    if dur_diff <= 0.5 and verify_full_normalized_pcm_match(track_a.filepath, track_b.filepath):
                        ds.union(track_a.filepath, track_b.filepath)
                        if (track_a.filepath, track_b.filepath) not in pair_results:
                            pair_results[(track_a.filepath, track_b.filepath)] = EvidenceReport(
                                track_a_path=track_a.filepath,
                                track_b_path=track_b.filepath,
                                classification=DuplicateType.EXACT_AUDIO,
                                confidence=100.0,
                                is_exact_audio=True,
                                duration_diff=dur_diff,
                                reasons=["Duplicado de Audio Exacto: Misma señal PCM normalizada completa decodificada."]
                            )

    # Check cancellation after exact hash step
    if is_cancelled and is_cancelled():
        coverage.scan_status = "CANCELLED"
        coverage.is_complete = False
        return ([], coverage) if return_coverage else DuplicateGroupList([], coverage)

    # Step 2: High-Precision Subfingerprint Index with Duration Bucketing
    if progress_callback:
        progress_callback(0.0, 0, 0, "Indexando huellas acústicas...")

    shingle_index = defaultdict(list)
    for idx, t in enumerate(tracks):
        fp = t.fingerprint_raw
        if not fp:
            continue
        limit = min(300, len(fp))
        seen = set()
        for i in range(limit):
            val = fp[i]
            if val != 0:
                if val not in seen:
                    seen.add(val)
                    shingle_index[val].append(idx)
                prefix_val = val & 0xFFFFFFF0
                if prefix_val != val:
                    if prefix_val not in seen:
                        seen.add(prefix_val)
                        shingle_index[prefix_val].append(idx)

    if progress_callback:
        progress_callback(0.0, 0, 0, "Filtrando coincidencias acústicas...")

    # Memory-Bounded Streaming Candidate Generation
    pair_hits = defaultdict(int)
    is_approximate = False
    oversized_buckets = 0
    candidate_pairs_generated = 0
    candidate_pairs_dropped = 0

    # Deterministic iteration over sorted shingle keys
    sorted_shingles = sorted(shingle_index.keys())

    for s in sorted_shingles:
        if is_cancelled and is_cancelled():
            coverage.scan_status = "CANCELLED"
            coverage.is_complete = False
            return ([], coverage) if return_coverage else DuplicateGroupList([], coverage)

        group = shingle_index[s]
        unique_group = sorted(list(set(group)))
        group_len = len(unique_group)

        if group_len <= 1:
            continue

        # Check oversized bucket limit
        if group_len > max_bucket_size:
            oversized_buckets += 1
            is_approximate = True
            total_possible = (group_len * (group_len - 1)) // 2
            retained_possible = (max_bucket_size * (max_bucket_size - 1)) // 2
            candidate_pairs_dropped += (total_possible - retained_possible)
            unique_group = unique_group[:max_bucket_size]
            group_len = max_bucket_size

        # Ingest pairs into pair_hits
        for i in range(group_len):
            idx_a = unique_group[i]
            for j in range(i + 1, group_len):
                idx_b = unique_group[j]
                p1, p2 = (idx_a, idx_b) if idx_a < idx_b else (idx_b, idx_a)
                candidate_pairs_generated += 1
                pair_hits[(p1, p2)] += 1

        # Memory cap applied DURING ingestion, not purely afterwards
        eviction_threshold = int(max_pair_hits * 1.25)
        if len(pair_hits) > eviction_threshold:
            is_approximate = True
            before_len = len(pair_hits)
            # Evict singleton hit pairs (unlikely to reach min_hits >= 3)
            pair_hits = defaultdict(int, {pair: count for pair, count in pair_hits.items() if count > 1})
            candidate_pairs_dropped += (before_len - len(pair_hits))

            if len(pair_hits) > max_pair_hits:
                before_len2 = len(pair_hits)
                pair_hits = defaultdict(int, {pair: count for pair, count in pair_hits.items() if count > 2})
                candidate_pairs_dropped += (before_len2 - len(pair_hits))

    candidate_pairs_set = set()
    for (idx_a, idx_b), hits in pair_hits.items():
        if hits >= 3:
            t_a = tracks[idx_a]
            t_b = tracks[idx_b]
            if ds.find(t_a.filepath) != ds.find(t_b.filepath):
                p1, p2 = (t_a.filepath, t_b.filepath) if t_a.filepath < t_b.filepath else (t_b.filepath, t_a.filepath)
                candidate_pairs_set.add((p1, p2))

    candidate_pairs_retained = len(candidate_pairs_set)
    pairs_to_compare = [(track_map[p1], track_map[p2]) for p1, p2 in sorted(candidate_pairs_set)]
    total_comparisons_est = len(pairs_to_compare)
    comparison_count = 0
    worker_failures = 0

    if progress_callback:
        progress_callback(0.0, 0, total_comparisons_est, f"Comparando huellas acústicas (0/{total_comparisons_est:,})...")

    if total_comparisons_est > 0:
        cpu_cores = os.cpu_count() or 4
        default_workers = max(1, min(6, cpu_cores - 1 if cpu_cores > 2 else cpu_cores))
        max_workers = config.max_workers if config.max_workers is not None else default_workers

        chunk_size = min(1000, max(50, total_comparisons_est // (max_workers * 6) + 1))
        chunks = [pairs_to_compare[i:i + chunk_size] for i in range(0, total_comparisons_est, chunk_size)]

        executor_kwargs = {"max_workers": max_workers}
        if sys.version_info >= (3, 11):
            executor_kwargs["max_tasks_per_child"] = 200

        with ProcessPoolExecutor(**executor_kwargs) as executor:
            futures = []
            chunk_map = {}
            for chunk in chunks:
                if is_cancelled and is_cancelled():
                    coverage.scan_status = "CANCELLED"
                    break
                f = executor.submit(_compare_chunk_worker, chunk, config)
                futures.append(f)
                chunk_map[f] = chunk

            for future in as_completed(futures):
                if is_cancelled and is_cancelled():
                    coverage.scan_status = "CANCELLED"
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    executor.shutdown(wait=True, cancel_futures=True)
                    break

                chunk = chunk_map.get(future, [])
                try:
                    res_list = future.result()
                    for res in res_list:
                        ds.union(res.track_a_path, res.track_b_path)
                        pair_results[(res.track_a_path, res.track_b_path)] = res
                except Exception as exc:
                    worker_failures += 1
                    is_approximate = True
                    logging.getLogger(__name__).warning("Chunk worker exception isolated: %s", exc)

                # Progreso exacto: incrementa por la longitud real del chunk
                comparison_count += len(chunk)
                curr_done = min(comparison_count, total_comparisons_est)
                if progress_callback:
                    pct = min(1.0, curr_done / total_comparisons_est)
                    progress_callback(pct, curr_done, total_comparisons_est, f"Comparando acústicamente ({curr_done:,}/{total_comparisons_est:,})...")

    # Step 3: Collect Disjoint Sets into Groups
    groups_dict: Dict[str, List[AudioTrack]] = defaultdict(list)
    for t in tracks:
        root = ds.find(t.filepath)
        groups_dict[root].append(t)

    duplicate_groups: List[DuplicateGroup] = []
    group_idx = 1

    for root, group_tracks in groups_dict.items():
        if len(group_tracks) <= 1:
            continue

        # Sort group tracks by quality score, bitrate, filesize
        group_tracks.sort(key=lambda t: (t.quality_score, t.bitrate, t.filesize), reverse=True)

        has_exact_hash = False
        has_exact_audio = False
        has_acoustic = False
        has_possible = False
        has_low_confidence = False
        has_manual_review = False
        max_confidence = 0.0

        for i in range(len(group_tracks)):
            for j in range(i + 1, len(group_tracks)):
                p1 = group_tracks[i].filepath
                p2 = group_tracks[j].filepath
                pair_key = (p1, p2) if (p1, p2) in pair_results else (p2, p1)
                rep = pair_results.get(pair_key)
                if rep:
                    if rep.confidence > max_confidence:
                        max_confidence = rep.confidence
                    if rep.classification == DuplicateType.EXACT_HASH:
                        has_exact_hash = True
                    elif rep.classification == DuplicateType.EXACT_AUDIO:
                        has_exact_audio = True
                    elif rep.classification == DuplicateType.ACOUSTIC_DUPLICATE:
                        has_acoustic = True
                    elif rep.classification == DuplicateType.POSSIBLE_DUPLICATE:
                        has_possible = True
                    elif rep.classification == DuplicateType.LOW_CONFIDENCE_REVIEW:
                        has_low_confidence = True
                    if rep.requires_manual_review:
                        has_manual_review = True

        # Safety firewall: A cluster with any weak link or manual review requirement fails-closed
        if has_low_confidence:
            primary_type = DuplicateType.LOW_CONFIDENCE_REVIEW
            has_manual_review = True
        elif has_possible:
            primary_type = DuplicateType.POSSIBLE_DUPLICATE
            has_manual_review = True
        elif has_acoustic:
            primary_type = DuplicateType.ACOUSTIC_DUPLICATE
        elif has_exact_audio:
            primary_type = DuplicateType.EXACT_AUDIO
        elif has_exact_hash:
            primary_type = DuplicateType.EXACT_HASH
        else:
            primary_type = DuplicateType.ACOUSTIC_DUPLICATE

        best_track = group_tracks[0]
        for t in group_tracks:
            if has_manual_review:
                t.action = FileAction.UNSET
            else:
                t.action = FileAction.KEEP if t.filepath == best_track.filepath else FileAction.DELETE

        reason = f"Mejor calidad detectada ({best_track.quality_score:.0f} pts)"
        group = DuplicateGroup(
            group_id=f"group_{group_idx:03d}",
            primary_type=primary_type,
            tracks=group_tracks,
            best_track_path=best_track.filepath,
            best_track_reason=reason,
            average_similarity=max_confidence,
            requires_manual_review=has_manual_review
        )
        group.recalculate_space_saving()
        duplicate_groups.append(group)
        group_idx += 1

    # Populate final coverage report
    coverage.is_complete = (worker_failures == 0) and (not is_approximate)
    coverage.is_approximate = is_approximate
    coverage.oversized_buckets = oversized_buckets
    coverage.candidate_pairs_generated = candidate_pairs_generated
    coverage.candidate_pairs_retained = candidate_pairs_retained
    coverage.candidate_pairs_dropped = candidate_pairs_dropped
    coverage.worker_failures = worker_failures
    coverage.actual_comparisons = comparison_count
    if coverage.scan_status != "CANCELLED":
        coverage.scan_status = "SUCCESS"

    result_list = DuplicateGroupList(duplicate_groups, coverage)
    return (duplicate_groups, coverage) if return_coverage else result_list
