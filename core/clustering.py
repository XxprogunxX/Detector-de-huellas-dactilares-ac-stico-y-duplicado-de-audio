"""
Clustering and Duplicate Grouping Engine using Disjoint-Set and Quality Ranking.
"""

from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

from core.models import AudioTrack, DuplicateGroup, DuplicateType, FileAction, ComparisonResult
from core.comparator import compare_tracks


def _compare_chunk_worker(pairs: List[Tuple[AudioTrack, AudioTrack]]) -> List[ComparisonResult]:
    results = []
    for t_a, t_b in pairs:
        res = compare_tracks(t_a, t_b)
        if res.duplicate_type != DuplicateType.NO_MATCH:
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


def cluster_duplicates(
    tracks: List[AudioTrack],
    progress_callback=None,
    is_cancelled=None
) -> List[DuplicateGroup]:
    """
    Efficiently clusters duplicates from a list of scanned AudioTracks.
    
    1. Instant $O(N)$ grouping by exact SHA-256 and PCM audio hashes.
    2. Duration-bucketed acoustic fingerprint comparison on remaining tracks.
    3. Union-Find graph clustering.
    4. Quality scoring and 'Best File' recommendation per group.
    """
    if len(tracks) < 2:
        return []

    track_map: Dict[str, AudioTrack] = {t.filepath: t for t in tracks}
    ds = DisjointSet()
    pair_results: Dict[Tuple[str, str], ComparisonResult] = {}

    # Step 1: Instant Exact Hash Clustering (O(N))
    # Step 1: Exact Matches (SHA256 and PCM Hash)
    sha_groups = defaultdict(list)
    for t in tracks:
        if t.sha256:
            sha_groups[t.sha256].append(t)
            
    for group in sha_groups.values():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    ds.union(group[i].filepath, group[j].filepath)
                    # We can directly create a ComparisonResult for exact matches
                    pair_results[(group[i].filepath, group[j].filepath)] = ComparisonResult(
                        track_a_path=group[i].filepath,
                        track_b_path=group[j].filepath,
                        similarity=1.0,
                        duplicate_type=DuplicateType.EXACT_HASH,
                        duration_diff=0.0,
                        reason="Duplicado Exacto: El archivo es idéntico bit a bit (SHA-256)."
                    )

    hash_groups = defaultdict(list)
    for t in tracks:
        if t.audio_hash:
            hash_groups[t.audio_hash].append(t)
            
    for group in hash_groups.values():
        if len(group) > 1:
            first = group[0].filepath
            for other in group[1:]:
                ds.union(first, other.filepath)
                if (first, other.filepath) not in pair_results:
                    pair_results[(first, other.filepath)] = ComparisonResult(
                        track_a_path=first,
                        track_b_path=other.filepath,
                        similarity=1.0,
                        duplicate_type=DuplicateType.EXACT_AUDIO,
                        duration_diff=abs(group[0].duration - other.duration),
                        reason="Duplicado de Audio Exacto: Misma señal PCM decodificada."
                    )

    # Step 2: LSH Pre-filtering for Acoustic Comparisons
    # Index exact 32-bit values of the first 100 frames. 
    # This reduces candidate pairs from 30,000,000 down to a few thousand.
    lsh_index = defaultdict(list)
    for t in tracks:
        if not t.fingerprint_raw:
            continue
        for val in set(t.fingerprint_raw[:100]):
            lsh_index[val].append(t)

    candidate_pairs_set = set()
    for sub_hash, group in lsh_index.items():
        if 1 < len(group) < 50:  # If more than 50 share the exact same frame, it's likely silence/noise
            group.sort(key=lambda x: x.duration)
            for i in range(len(group)):
                t_a = group[i]
                for j in range(i + 1, len(group)):
                    t_b = group[j]
                    if t_b.duration - t_a.duration > 15.0:
                        break
                    if ds.find(t_a.filepath) != ds.find(t_b.filepath):
                        p1, p2 = (t_a.filepath, t_b.filepath) if t_a.filepath < t_b.filepath else (t_b.filepath, t_a.filepath)
                        candidate_pairs_set.add((p1, p2))

    pairs_to_compare = [(track_map[p1], track_map[p2]) for p1, p2 in candidate_pairs_set]
    total_comparisons_est = len(pairs_to_compare)
    comparison_count = 0

    if total_comparisons_est > 0:
        max_workers = max(1, min(8, (os.cpu_count() or 4)))
        # Cap chunk size to 5000 so the UI updates frequently
        chunk_size = min(5000, max(100, total_comparisons_est // (max_workers * 4)))
        chunks = [pairs_to_compare[i:i + chunk_size] for i in range(0, total_comparisons_est, chunk_size)]
        
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_compare_chunk_worker, chunk) for chunk in chunks]
            
            for future in as_completed(futures):
                if is_cancelled and is_cancelled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                
                res_list = future.result()
                for res in res_list:
                    ds.union(res.track_a_path, res.track_b_path)
                    pair_results[(res.track_a_path, res.track_b_path)] = res
                
                comparison_count += chunk_size
                if progress_callback:
                    pct = min(1.0, comparison_count / total_comparisons_est)
                    progress_callback(pct, f"Comparando acústicamente ({min(comparison_count, total_comparisons_est)}/{total_comparisons_est})...")

    # Step 3: Collect Disjoint Sets into Groups
    groups_dict: Dict[str, List[AudioTrack]] = defaultdict(list)
    for t in tracks:
        root = ds.find(t.filepath)
        groups_dict[root].append(t)

    # Filter out singletons (groups with only 1 file)
    duplicate_groups: List[DuplicateGroup] = []
    group_idx = 1

    for root, group_tracks in groups_dict.items():
        if len(group_tracks) <= 1:
            continue

        # Sort group tracks by (effective quality score with duration penalty, quality_score, filesize)
        max_duration = max((t.duration for t in group_tracks), default=0.0)
        
        def track_rank_key(t: AudioTrack):
            # Duration penalty: if track is truncated (> 1.5s shorter than group maximum), penalize score
            dur_penalty = 0.0
            if max_duration > 0 and t.duration < (max_duration - 1.5):
                dur_penalty = min(30.0, ((max_duration - t.duration) / max_duration) * 40.0)
            
            # Format preference bonus: FLAC/ALAC have proper metadata tags over raw WAV
            fmt_bonus = 2.0 if t.format in ("FLAC", "ALAC") and not t.is_fake_lossless else 0.0
            
            effective_score = t.quality_score - dur_penalty + fmt_bonus
            return (effective_score, t.quality_score, t.duration, -t.filesize)

        group_tracks.sort(key=track_rank_key, reverse=True)

        best_track = group_tracks[0]

        # Determine primary duplicate type and average similarity
        types_in_group = set()
        sim_scores = []

        for i in range(len(group_tracks)):
            for j in range(i + 1, len(group_tracks)):
                p1 = group_tracks[i].filepath
                p2 = group_tracks[j].filepath
                res = pair_results.get((p1, p2)) or pair_results.get((p2, p1))
                if res:
                    types_in_group.add(res.duplicate_type)
                    sim_scores.append(res.similarity)

        avg_sim = (sum(sim_scores) / len(sim_scores) * 100.0) if sim_scores else 100.0

        if DuplicateType.EXACT_HASH in types_in_group and len(types_in_group) == 1:
            primary_type = DuplicateType.EXACT_HASH
        elif DuplicateType.EXACT_AUDIO in types_in_group and len(types_in_group) <= 2:
            primary_type = DuplicateType.EXACT_AUDIO
        elif DuplicateType.ACOUSTIC_DUPLICATE in types_in_group:
            primary_type = DuplicateType.ACOUSTIC_DUPLICATE
        else:
            primary_type = DuplicateType.POSSIBLE_DUPLICATE

        # Formulate human explanation for best track recommendation
        best_reason = f"Mayor fidelidad: {best_track.quality_details} (Puntuación: {best_track.quality_score}/100)"
        if best_track.is_fake_lossless:
            best_reason = f"⚠️ Nota: El archivo lossless es falso (corte en {int(best_track.spectral_cutoff)}Hz). Se seleccionó la mejor fuente disponible."

        # Mark default actions: keep best, mark others as delete (or unset for review)
        for t in group_tracks:
            if t.filepath == best_track.filepath:
                t.action = FileAction.KEEP
            else:
                t.action = FileAction.DELETE

        dup_group = DuplicateGroup(
            group_id=f"GRP-{group_idx:04d}",
            primary_type=primary_type,
            tracks=group_tracks,
            best_track_path=best_track.filepath,
            best_track_reason=best_reason,
            average_similarity=round(avg_sim, 1)
        )
        dup_group.recalculate_space_saving()
        duplicate_groups.append(dup_group)
        group_idx += 1

    # Sort groups by potential space saving (highest first)
    duplicate_groups.sort(key=lambda g: g.space_saving_bytes, reverse=True)
    return duplicate_groups
