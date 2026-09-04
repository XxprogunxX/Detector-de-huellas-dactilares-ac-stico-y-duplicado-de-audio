"""
Clustering and Duplicate Grouping Engine using Disjoint-Set and Quality Ranking.
"""

from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
import itertools

from core.models import AudioTrack, DuplicateGroup, DuplicateType, FileAction, EvidenceReport
from core.comparator import compare_tracks


def _compare_chunk_worker(pairs: List[Tuple[AudioTrack, AudioTrack]]) -> List[EvidenceReport]:
    results = []
    for t_a, t_b in pairs:
        res = compare_tracks(t_a, t_b)
        if res.classification != DuplicateType.NO_MATCH:
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
    pair_results: Dict[Tuple[str, str], EvidenceReport] = {}

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
                    # We can directly create an EvidenceReport for exact matches
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
                    # EXACT_AUDIO requires:
                    # 1. Matching 30s prefix audio_hash
                    # 2. Duration variance <= 0.5s
                    # 3. Full normalized PCM stream verification
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
                # 28-bit prefix for MP3 compression tolerance
                prefix_val = val & 0xFFFFFFF0
                if prefix_val != val:
                    if prefix_val not in seen:
                        seen.add(prefix_val)
                        shingle_index[prefix_val].append(idx)

    if progress_callback:
        progress_callback(0.0, 0, 0, "Filtrando coincidencias acústicas...")

    # Accumulate co-occurring token counts between candidate track pairs
    pair_hits = defaultdict(int)
    max_bucket_size = 500

    for s, group in shingle_index.items():
        unique_group = list(set(group))
        group_len = len(unique_group)
        if 1 < group_len <= max_bucket_size:
            for i in range(group_len):
                idx_a = unique_group[i]
                for j in range(i + 1, group_len):
                    idx_b = unique_group[j]
                    p1, p2 = (idx_a, idx_b) if idx_a < idx_b else (idx_b, idx_a)
                    pair_hits[(p1, p2)] += 1

    candidate_pairs_set = set()

    # Cap pair_hits to avoid unbounded memory growth on very large libraries (100k+ tracks)
    MAX_PAIRS = 500_000
    if len(pair_hits) > MAX_PAIRS:
        # Keep the pairs with highest hit count (most likely true duplicates)
        pair_hits = dict(sorted(pair_hits.items(), key=lambda x: x[1], reverse=True)[:MAX_PAIRS])

    for (idx_a, idx_b), hits in pair_hits.items():
        t_a = tracks[idx_a]
        t_b = tracks[idx_b]
        min_hits = 3
        if hits >= min_hits:
            if ds.find(t_a.filepath) != ds.find(t_b.filepath):
                p1, p2 = (t_a.filepath, t_b.filepath) if t_a.filepath < t_b.filepath else (t_b.filepath, t_a.filepath)
                candidate_pairs_set.add((p1, p2))


    pairs_to_compare = [(track_map[p1], track_map[p2]) for p1, p2 in candidate_pairs_set]
    total_comparisons_est = len(pairs_to_compare)
    comparison_count = 0

    if progress_callback:
        progress_callback(0.0, 0, total_comparisons_est, f"Comparando huellas acústicas (0/{total_comparisons_est:,})...")


    if total_comparisons_est > 0:
        # Keep 1-2 CPU cores free to prevent system freeze and overheating
        cpu_cores = os.cpu_count() or 4
        max_workers = max(1, min(6, cpu_cores - 1 if cpu_cores > 2 else cpu_cores))
        
        # Responsive chunk size for smooth UI progress
        chunk_size = min(1000, max(50, total_comparisons_est // (max_workers * 6) + 1))
        chunks = [pairs_to_compare[i:i + chunk_size] for i in range(0, total_comparisons_est, chunk_size)]
        
        # max_tasks_per_child=200: recycle worker processes periodically to prevent
        # memory leaks from numpy/ffmpeg accumulated buffers in long-running scans.
        with ProcessPoolExecutor(max_workers=max_workers, max_tasks_per_child=200) as executor:
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
                curr_done = min(comparison_count, total_comparisons_est)
                if progress_callback:
                    pct = min(1.0, curr_done / total_comparisons_est)
                    progress_callback(pct, curr_done, total_comparisons_est, f"Comparando acústicamente ({curr_done:,}/{total_comparisons_est:,})...")


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
            fmt_bonus = 2.0 if t.format in ("FLAC", "ALAC") and t.fake_lossless_confidence <= 50.0 else 0.0
            
            effective_score = t.quality_score - dur_penalty + fmt_bonus
            return (effective_score, t.quality_score, t.duration, -t.filesize)

        group_tracks.sort(key=track_rank_key, reverse=True)

        best_track = group_tracks[0]

        # Determine primary duplicate type and average similarity
        types_in_group = set()
        total_sim = 0.0
        pair_count = 0

        for i in range(len(group_tracks)):
            for j in range(i + 1, len(group_tracks)):
                p1 = group_tracks[i].filepath
                p2 = group_tracks[j].filepath
                res = pair_results.get((p1, p2)) or pair_results.get((p2, p1))
                if res:
                    types_in_group.add(res.classification)
                    total_sim += res.confidence / 100.0
                    pair_count += 1
        
        avg_sim = (total_sim / pair_count) * 100.0 if pair_count > 0 else 100.0

        has_weak_link = False
        if DuplicateType.POSSIBLE_DUPLICATE in types_in_group:
            has_weak_link = True
        if hasattr(DuplicateType, "LOW_CONFIDENCE_REVIEW") and DuplicateType.LOW_CONFIDENCE_REVIEW in types_in_group:
            has_weak_link = True

        if DuplicateType.EXACT_HASH in types_in_group and len(types_in_group) == 1:
            primary_type = DuplicateType.EXACT_HASH
        elif DuplicateType.EXACT_AUDIO in types_in_group and len(types_in_group) <= 2:
            primary_type = DuplicateType.EXACT_AUDIO
        elif DuplicateType.ACOUSTIC_DUPLICATE in types_in_group:
            primary_type = DuplicateType.ACOUSTIC_DUPLICATE
        else:
            primary_type = DuplicateType.POSSIBLE_DUPLICATE

        if has_weak_link:
            primary_type = DuplicateType.POSSIBLE_DUPLICATE

        # Formulate human explanation for best track recommendation
        best_reason = f"Mayor fidelidad: {best_track.quality_details} (Puntuación: {best_track.quality_score}/100)"
        if best_track.fake_lossless_confidence > 50.0:
            best_reason = f"⚠️ Nota: Transcodificación probable ({best_track.fake_lossless_confidence:.0f}%). Se seleccionó la mejor fuente disponible."

        # Determine if this group requires manual review
        req_review = primary_type in (DuplicateType.POSSIBLE_DUPLICATE, DuplicateType.LOW_CONFIDENCE_REVIEW) if hasattr(DuplicateType, "LOW_CONFIDENCE_REVIEW") else (primary_type == DuplicateType.POSSIBLE_DUPLICATE)

        # Mark default actions: keep best, mark others as delete (or unset for review)
        for t in group_tracks:
            if req_review:
                t.action = FileAction.UNSET
            else:
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
            average_similarity=round(avg_sim, 1),
            requires_manual_review=req_review
        )
        dup_group.recalculate_space_saving()
        duplicate_groups.append(dup_group)
        group_idx += 1

    # Sort groups by potential space saving (highest first)
    duplicate_groups.sort(key=lambda g: g.space_saving_bytes, reverse=True)
    return duplicate_groups
