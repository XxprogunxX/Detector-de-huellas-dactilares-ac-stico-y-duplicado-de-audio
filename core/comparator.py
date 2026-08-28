"""
Acoustic Fingerprint Comparison and Alignment Engine.
"""

from typing import List, Tuple, Optional
import numpy as np
from core.models import AudioTrack, EvidenceReport, DuplicateType


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
    Vectorized with numpy: ~10x faster than the equivalent Python loop for 300-frame fingerprints.

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

    # Convert to numpy uint32 arrays once for vectorized XOR + popcount
    arr_a = np.asarray(fp_a, dtype=np.uint32)
    arr_b = np.asarray(fp_b, dtype=np.uint32)

    # If both sequences are short, reduce min overlap
    effective_min_overlap = min(min_overlap_frames, min(len_a, len_b) // 2 + 1)
    if effective_min_overlap < 5:
        effective_min_overlap = 5

    best_ber = 1.0
    best_offset = 0

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

        # Vectorized XOR then count set bits via unpackbits on uint8 view
        xor = np.bitwise_xor(
            arr_a[a_start:a_start + overlap],
            arr_b[b_start:b_start + overlap]
        )
        # View the uint32 array as uint8 bytes (4 bytes per element) then count set bits
        diff_bits = int(np.unpackbits(xor.view(np.uint8)).sum())

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


def compare_tracks(track_a: AudioTrack, track_b: AudioTrack) -> EvidenceReport:
    """
    Compares two tracks across all detection tiers (File Hash, PCM Hash, Acoustic Fingerprint).
    Returns an EvidenceReport with multiple signals and explained confidence.
    """
    report = EvidenceReport(
        track_a_path=track_a.filepath,
        track_b_path=track_b.filepath,
        classification=DuplicateType.NO_MATCH,
        confidence=0.0
    )

    # 1. Exact Binary File Hash
    if track_a.sha256 and track_b.sha256 and track_a.sha256 == track_b.sha256:
        report.is_exact_hash = True
        report.confidence = 100.0
        report.classification = DuplicateType.EXACT_HASH
        report.reasons.append("Duplicado Exacto: Archivos idénticos byte por byte (mismo hash SHA-256).")
        return report

    # 2. Exact Decoded PCM Audio Hash
    if track_a.audio_hash and track_b.audio_hash and track_a.audio_hash == track_b.audio_hash:
        report.is_exact_audio = True
        report.duration_diff = abs(track_a.duration - track_b.duration)
        report.confidence = 100.0
        report.classification = DuplicateType.EXACT_AUDIO
        report.reasons.append("Duplicado de Audio Exacto: Misma señal PCM decodificada (diferente contenedor o etiquetas ID3).")
        return report

    # 3. Duration Pre-filtering
    duration_diff = abs(track_a.duration - track_b.duration)
    report.duration_diff = duration_diff
    if duration_diff > 90.0:  # More than 1.5 min difference is likely completely different
        report.confidence = 0.0
        report.classification = DuplicateType.NO_MATCH
        report.reasons.append("Audios distintos (diferencia de duración > 90 segundos).")
        return report

    # 4. Acoustic Fingerprint Comparison
    if not track_a.fingerprint_raw or not track_b.fingerprint_raw:
        report.confidence = 0.0
        report.classification = DuplicateType.UNCERTAIN
        report.reasons.append("Incierto: No fue posible comparar huellas acústicas (fpcalc falló).")
        return report

    similarity, offset = compare_raw_fingerprints(track_a.fingerprint_raw, track_b.fingerprint_raw)
    report.chromaprint_similarity = similarity
    report.temporal_offset_frames = offset

    # Metadata Match Check
    metadata_match = (
        track_a.format == track_b.format and 
        track_a.samplerate == track_b.samplerate and 
        track_a.channels == track_b.channels and 
        track_a.bitrate == track_b.bitrate
    )
    report.metadata_match = metadata_match

    # Spectral Diff
    if track_a.spectral_cutoff > 0.0 and track_b.spectral_cutoff > 0.0:
        spectral_diff = abs(track_a.spectral_cutoff - track_b.spectral_cutoff)
        report.spectral_diff = spectral_diff
    else:
        report.reasons.append("Espectro: N/A (Faltan datos espectrales en una de las pistas)")

    # 5. Evidence Engine Combination Formula
    # Base Confidence
    base_confidence = similarity * 100.0
    
    # Modifier 1: Temporal Alignment Bonus (max +3.0)
    # Linearly decays from +3.0 (at 0 frames) to 0.0 (at 15 frames)
    offset_bonus = max(0.0, 3.0 * (1.0 - abs(offset) / 15.0))
    
    # Modifier 2: Duration Bonus/Penalty
    # Bonus decays from +2.0 (0s) to 0.0 (2.0s)
    duration_bonus = max(0.0, 2.0 * (1.0 - duration_diff / 2.0))
    # Penalty starts at 10.0s (0.0) and scales to 30.0s (-10.0)
    duration_penalty = 0.0
    if duration_diff >= 10.0:
        duration_penalty = max(-10.0, -10.0 * ((duration_diff - 10.0) / 20.0))
        
    # Modifier 3: Spectral Bonus (max +1.0)
    # Linearly decays from +1.0 (0Hz diff) to 0.0 (1000Hz diff)
    spectral_bonus = 0.0
    if report.spectral_diff is not None:
        spectral_bonus = max(0.0, 1.0 * (1.0 - report.spectral_diff / 1000.0))

    total_delta = offset_bonus + duration_bonus + duration_penalty + spectral_bonus
    final_confidence = base_confidence + total_delta
    
    # Limit between 0.0 and 99.9
    final_confidence = max(0.0, min(99.9, final_confidence))
    report.confidence = final_confidence

    if total_delta != 0.0:
        report.reasons.append(
            f"Confidence ajustada por señales secundarias: base {base_confidence:.1f}% "
            f"{'+' if total_delta >= 0 else ''}{total_delta:.1f}% "
            f"(alineamiento={offset_bonus:.1f}, duración={duration_bonus+duration_penalty:.1f}, espectro={spectral_bonus:.1f})"
        )

    # 6. Final Classification Rules
    if final_confidence >= 95.0:
        report.classification = DuplicateType.ACOUSTIC_DUPLICATE
        report.reasons.append(f"Duplicado Acústico ({final_confidence:.1f}%): Misma grabación original.")
    elif final_confidence >= 80.0:
        report.classification = DuplicateType.POSSIBLE_DUPLICATE
        if duration_diff > 2.0:
            report.reasons.append(f"Posible Duplicado ({final_confidence:.1f}%): Variación de duración (posible versión extendida/directo).")
        else:
            report.reasons.append(f"Posible Duplicado ({final_confidence:.1f}%): Variación acústica (posible remasterización o transcodificación).")
    else:
        report.classification = DuplicateType.NO_MATCH
        report.reasons.append(f"Pistas diferentes ({final_confidence:.1f}%).")

    return report
