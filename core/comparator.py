"""
Acoustic Fingerprint Comparison and Alignment Engine.
"""

from typing import List, Tuple, Optional
from core.models import AudioTrack, ComparisonResult, DuplicateType


def popcount32(x: int) -> int:
    """Returns number of set bits in integer."""
    return x.bit_count()


def compare_raw_fingerprints(
    fp_a: List[int],
    fp_b: List[int],
    max_offset_frames: int = 15,
    min_overlap_frames: int = 40
) -> Tuple[float, int]:
    """
    Performs sliding-window bit-error-rate (BER) alignment between two Chromaprint sequences.
    
    Args:
        fp_a: List of 32-bit uints for track A.
        fp_b: List of 32-bit uints for track B.
        max_offset_frames: Maximum temporal offset search window (+- frames).
        min_overlap_frames: Minimum frame overlap to consider valid comparison.
        
    Returns:
        (max_similarity, best_offset) where similarity is 0.0 to 1.0.
    """
    len_a = len(fp_a)
    len_b = len(fp_b)

    if len_a == 0 or len_b == 0:
        return 0.0, 0

    # If both sequences are short, reduce min overlap
    effective_min_overlap = min(min_overlap_frames, min(len_a, len_b) // 2 + 1)
    if effective_min_overlap < 5:
        effective_min_overlap = 5

    best_ber = 1.0
    best_offset = 0
    max_similarity = 0.0

    # Constrain search window
    start_offset = -min(max_offset_frames, len_a - effective_min_overlap)
    end_offset = min(max_offset_frames, len_b - effective_min_overlap)

    for offset in range(start_offset, end_offset + 1):
        if offset >= 0:
            a_start = 0
            b_start = offset
            overlap = min(len_a, len_b - offset)
        else:
            a_start = -offset
            b_start = 0
            overlap = min(len_a + offset, len_b)

        if overlap < effective_min_overlap:
            continue

        # Fast bitwise popcount comparison over overlapping window
        diff_bits = 0
        for i in range(overlap):
            diff_bits += (fp_a[a_start + i] ^ fp_b[b_start + i]).bit_count()

        total_bits = overlap * 32
        ber = diff_bits / total_bits

        if ber < best_ber or (ber == best_ber and abs(offset) < abs(best_offset)):
            best_ber = ber
            best_offset = offset

    # Convert BER to similarity: In Chromaprint random tracks have BER ~ 0.50 (50% random bit flip)
    # A true match has BER < 0.15 (Similarity > 85%)
    # Perfect match has BER ~ 0.00 (Similarity 100%)
    if best_ber <= 0.50:
        # Scale 0.0 -> 1.0 (0.0 BER = 100%, 0.5 BER = 0% similarity)
        max_similarity = max(0.0, 1.0 - (best_ber / 0.50))
    else:
        max_similarity = 0.0

    return max_similarity, best_offset


def compare_tracks(track_a: AudioTrack, track_b: AudioTrack) -> ComparisonResult:
    """
    Compares two tracks across all detection tiers (File Hash, PCM Hash, Acoustic Fingerprint).
    
    Returns:
        ComparisonResult with similarity score, duplicate type, and human explanation.
    """
    # 1. Exact Binary File Hash
    if track_a.sha256 and track_b.sha256 and track_a.sha256 == track_b.sha256:
        return ComparisonResult(
            track_a_path=track_a.filepath,
            track_b_path=track_b.filepath,
            similarity=1.0,
            duplicate_type=DuplicateType.EXACT_HASH,
            duration_diff=0.0,
            reason="Duplicado Exacto: Archivos idénticos byte por byte (mismo hash SHA-256)."
        )

    # 2. Exact Decoded PCM Audio Hash
    if track_a.audio_hash and track_b.audio_hash and track_a.audio_hash == track_b.audio_hash:
        return ComparisonResult(
            track_a_path=track_a.filepath,
            track_b_path=track_b.filepath,
            similarity=1.0,
            duplicate_type=DuplicateType.EXACT_AUDIO,
            duration_diff=abs(track_a.duration - track_b.duration),
            reason="Duplicado de Audio Exacto: Misma señal PCM decodificada (diferente contenedor o etiquetas ID3)."
        )

    # 3. Duration Pre-filtering
    duration_diff = abs(track_a.duration - track_b.duration)
    if duration_diff > 90.0:  # More than 1.5 min difference is likely completely different
        return ComparisonResult(
            track_a_path=track_a.filepath,
            track_b_path=track_b.filepath,
            similarity=0.0,
            duplicate_type=DuplicateType.NO_MATCH,
            duration_diff=duration_diff,
            reason="Audios distintos (diferencia de duración > 90 segundos)."
        )

    # 4. Acoustic Fingerprint Comparison
    if not track_a.fingerprint_raw or not track_b.fingerprint_raw:
        return ComparisonResult(
            track_a_path=track_a.filepath,
            track_b_path=track_b.filepath,
            similarity=0.0,
            duplicate_type=DuplicateType.NO_MATCH,
            duration_diff=duration_diff,
            reason="No fue posible comparar huellas acústicas."
        )

    similarity, offset = compare_raw_fingerprints(track_a.fingerprint_raw, track_b.fingerprint_raw)

    # 5. Classification Rules
    if similarity >= 0.98 and duration_diff <= 2.0:
        return ComparisonResult(
            track_a_path=track_a.filepath,
            track_b_path=track_b.filepath,
            similarity=similarity,
            duplicate_type=DuplicateType.ACOUSTIC_DUPLICATE,
            duration_diff=duration_diff,
            reason=f"Duplicado Acústico ({similarity*100:.1f}%): Misma grabación original en diferente formato/bitrate/volumen."
        )
    elif similarity >= 0.80:
        # Possible variations: Remaster, Radio Edit, Live, Extended
        sub_reason = "Posible Duplicado (Variante): "
        if duration_diff > 2.0:
            sub_reason += f"Variación de duración (Δ{duration_diff:.1f}s - posible radio edit / versión extendida / directo)."
        else:
            sub_reason += "Variación acústica (posible remasterización o edición con diferente ecualización/compresión)."

        return ComparisonResult(
            track_a_path=track_a.filepath,
            track_b_path=track_b.filepath,
            similarity=similarity,
            duplicate_type=DuplicateType.POSSIBLE_DUPLICATE,
            duration_diff=duration_diff,
            reason=f"{sub_reason} ({similarity*100:.1f}% similitud)."
        )
    else:
        return ComparisonResult(
            track_a_path=track_a.filepath,
            track_b_path=track_b.filepath,
            similarity=similarity,
            duplicate_type=DuplicateType.NO_MATCH,
            duration_diff=duration_diff,
            reason=f"Pistas diferentes ({similarity*100:.1f}% similitud acústica)."
        )
