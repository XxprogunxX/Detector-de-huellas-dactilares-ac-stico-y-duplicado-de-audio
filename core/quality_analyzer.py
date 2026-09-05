from core.binary_resolver import get_ffmpeg_path
from core.ffmpeg_runner import terminate_process_tree
"""
Audio Quality Analysis and Evidence-Based Spectral Assessment (Phase C / AC-005, AC-017).

Provides multi-window, multi-region, multi-channel spectral analysis to detect
lossy transcode artifacts in lossless audio containers without assuming authentic
lossless status on failure.

Semantics:
  - NO_LOSSY_EVIDENCE: Analysis found no evidence of lossy compression (does not guarantee original master).
  - SUSPECTED_TRANSCODE: Consistent high-frequency attenuation across multiple windows, regions, and channels.
  - UNKNOWN: Fail-closed fallback for errors, corrupt audio, silence, or insufficient evidence.
  - NOT_ANALYZED: Analysis was deliberately skipped.

Provisional heuristics are centralized and clearly documented.
"""

import os
import sys
import subprocess
import logging
from typing import Tuple, List, Optional
import numpy as np

from core.spectral_types import SpectralAssessment, SpectralResult
from core.models import AudioTrack

logger = logging.getLogger(__name__)

# ==============================================================================
# Centralized Provisional Heuristic Thresholds (AC-005, AC-017)
# NOTE: These values are provisional heuristics and require real-world validation
# suite ground truth. Synthetic PASS != scientifically calibrated detector.
# ==============================================================================
PROVISIONAL_MIN_ENERGY_DBFS: float = -60.0        # Signals below this are treated as silence
PROVISIONAL_MIN_VALID_WINDOWS: int = 16            # Minimum FFT windows with usable energy required
PROVISIONAL_MIN_DURATION_SECONDS: float = 3.0      # Minimum audio duration needed to attempt evaluation
PROVISIONAL_CUTOFF_ATTENUATION_DB: float = 40.0    # Drop below midrange peak required to suspect cutoff
PROVISIONAL_PERSISTENCE_RATIO: float = 0.80        # Fraction of valid windows exhibiting cutoff (80%)
PROVISIONAL_MIN_SAMPLE_RATE: int = 40000           # Sample rates below 40kHz (e.g. 32kHz, 22.05kHz) lack Nyquist margin
PROVISIONAL_MIN_NYQUIST_HZ: float = 20000.0        # Real Nyquist must be >= 20 kHz to reliably evaluate 15-20kHz lossy bands
PROVISIONAL_FFT_SIZE: int = 2048                   # FFT analysis window size
PROVISIONAL_HOP_SIZE: int = 1024                   # Hop size between windows


def _compute_rms_dbfs(samples: np.ndarray) -> float:
    """Computes Root-Mean-Square energy in dBFS for float32 audio samples in [-1.0, 1.0]."""
    if len(samples) == 0:
        return -120.0
    mean_sq = float(np.mean(samples ** 2))
    if mean_sq <= 1e-12:
        return -120.0
    return float(20.0 * np.log10(np.sqrt(mean_sq)))


def analyze_pcm_samples(
    samples: np.ndarray,
    sample_rate: int,
    channels: int = 1,
    analyzed_duration: float = 0.0
) -> SpectralResult:
    """
    Core signal processing function: analyzes a 1D or 2D numpy array of float32 PCM samples.
    Evaluates individual channels without downmixing to prevent phase cancellation artifacts.
    """
    # 1. Bandwidth check: Real Nyquist frequency must provide sufficient margin
    nyquist = sample_rate / 2.0
    if sample_rate < PROVISIONAL_MIN_SAMPLE_RATE or nyquist < PROVISIONAL_MIN_NYQUIST_HZ:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=analyzed_duration,
            valid_windows=0,
            rms_dbfs=None,
            reason="insufficient_frequency_bandwidth"
        )

    # 2. Check for NaN or Inf in decoded signal
    if np.any(np.isnan(samples)) or np.any(np.isinf(samples)):
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=analyzed_duration,
            valid_windows=0,
            rms_dbfs=None,
            reason="nan_or_invalid_spectrum"
        )

    # 3. Reshape multi-channel samples
    if channels > 1 and samples.ndim == 1:
        # Array was passed as flat interleaved samples
        num_frames = len(samples) // channels
        if num_frames * channels != len(samples):
            samples = samples[:num_frames * channels]
        samples = samples.reshape(num_frames, channels)
    elif samples.ndim == 1:
        samples = samples.reshape(-1, 1)

    total_frames, ch_count = samples.shape
    dur = analyzed_duration if analyzed_duration > 0.0 else (total_frames / sample_rate)
    if dur < PROVISIONAL_MIN_DURATION_SECONDS or total_frames < PROVISIONAL_FFT_SIZE:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=dur,
            valid_windows=0,
            rms_dbfs=None,
            reason="insufficient_duration"
        )

    # 4. Energy check across all channels
    global_rms = _compute_rms_dbfs(samples)
    if global_rms < PROVISIONAL_MIN_ENERGY_DBFS:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=dur,
            valid_windows=0,
            rms_dbfs=global_rms,
            reason="insufficient_signal_energy"
        )

    # 5. Windowed FFT per channel (Preserves stereo channels without downmixing)
    window = np.hanning(PROVISIONAL_FFT_SIZE)
    freqs = np.fft.rfftfreq(PROVISIONAL_FFT_SIZE, 1.0 / sample_rate)
    mid_mask = (freqs >= 1000) & (freqs <= 5000)
    high_band_mask = (freqs >= 19000) & (freqs <= min(22050, nyquist))

    total_valid_windows = 0
    channel_suspects: List[bool] = []
    channel_cutoffs: List[float] = []
    channel_confidences: List[float] = []

    for ch in range(ch_count):
        ch_samples = samples[:, ch]
        num_windows = (len(ch_samples) - PROVISIONAL_FFT_SIZE) // PROVISIONAL_HOP_SIZE
        if num_windows <= 0:
            continue

        valid_windows_ch = 0
        cutoff_windows_ch = 0
        observed_cutoffs: List[float] = []

        # Analyze windows across the channel
        # Subsample up to 60 windows for responsiveness if long
        step = max(1, num_windows // 60)
        for w_idx in range(0, num_windows, step):
            start = w_idx * PROVISIONAL_HOP_SIZE
            seg = ch_samples[start:start + PROVISIONAL_FFT_SIZE]
            
            # Check window energy
            seg_rms = _compute_rms_dbfs(seg)
            if seg_rms < PROVISIONAL_MIN_ENERGY_DBFS:
                continue

            seg_windowed = seg * window
            fft_vals = np.fft.rfft(seg_windowed)
            psd = np.abs(fft_vals) ** 2

            peak_mid = np.max(psd[mid_mask]) if np.any(mid_mask) else np.max(psd)
            if peak_mid <= 1e-12:
                continue

            valid_windows_ch += 1
            psd_db = 10.0 * np.log10(np.maximum(psd / peak_mid, 1e-8))

            # Natural high frequency content check: peak power above 19kHz
            has_natural_high_freq = np.any(high_band_mask) and (np.max(psd_db[high_band_mask]) > -30.0)

            # Scan for a cutoff frequency where energy drops permanently below -40 dB
            # between 14 kHz and min(21.5 kHz, nyquist)
            scan_indices = np.where((freqs >= 14000) & (freqs <= min(21500, nyquist)))[0]
            detected_cutoff: Optional[float] = None

            if not has_natural_high_freq:
                for idx in scan_indices:
                    f_cand = freqs[idx]
                    stopband_mask = freqs > f_cand + 600
                    if np.any(stopband_mask):
                        # Peak power in stopband must be deeply suppressed
                        stopband_peak = np.max(psd_db[stopband_mask])
                        stopband_mean = np.mean(psd_db[stopband_mask])
                        if stopband_peak < -30.0 and stopband_mean < -PROVISIONAL_CUTOFF_ATTENUATION_DB:
                            detected_cutoff = float(f_cand)
                            break

            if detected_cutoff is not None and not has_natural_high_freq:
                cutoff_windows_ch += 1
                observed_cutoffs.append(detected_cutoff)

        total_valid_windows += valid_windows_ch

        if valid_windows_ch >= (PROVISIONAL_MIN_VALID_WINDOWS // ch_count or 1):
            ratio = cutoff_windows_ch / valid_windows_ch
            if ratio >= PROVISIONAL_PERSISTENCE_RATIO and len(observed_cutoffs) > 0:
                channel_suspects.append(True)
                channel_cutoffs.append(float(np.median(observed_cutoffs)))
                # Confidence represents internal consistency (scaled 0-100)
                channel_confidences.append(min(100.0, max(50.0, ratio * 100.0)))
            else:
                channel_suspects.append(False)
        else:
            channel_suspects.append(False)

    if total_valid_windows < PROVISIONAL_MIN_VALID_WINDOWS:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=analyzed_duration,
            valid_windows=total_valid_windows,
            rms_dbfs=global_rms,
            reason="insufficient_valid_windows"
        )

    # 6. Channel Aggregation Rule:
    # If AT LEAST ONE channel exhibits significant content contradicting a lossy cutoff,
    # do NOT classify as SUSPECTED_TRANSCODE.
    if len(channel_suspects) > 0 and all(channel_suspects):
        mean_cutoff = float(np.mean(channel_cutoffs))
        mean_conf = float(np.mean(channel_confidences))
        return SpectralResult(
            assessment=SpectralAssessment.SUSPECTED_TRANSCODE,
            cutoff_hz=round(mean_cutoff, 1),
            confidence=round(mean_conf, 1),
            analyzed_duration=analyzed_duration,
            valid_windows=total_valid_windows,
            rms_dbfs=round(global_rms, 1),
            reason="persistent_lossy_cutoff_detected"
        )

    # Check for borderline consistency
    # (e.g. some cutoff presence but not meeting the 80% threshold)
    borderline_presence = any(
        (0.40 <= (c / max(1, v)) < PROVISIONAL_PERSISTENCE_RATIO)
        for c, v in zip([w for w in [len(observed_cutoffs)]], [total_valid_windows])
    )
    if borderline_presence:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=analyzed_duration,
            valid_windows=total_valid_windows,
            rms_dbfs=round(global_rms, 1),
            reason="borderline_or_inconclusive_spectral_evidence"
        )

    return SpectralResult(
        assessment=SpectralAssessment.NO_LOSSY_EVIDENCE,
        cutoff_hz=None,
        confidence=0.0,
        analyzed_duration=analyzed_duration,
        valid_windows=total_valid_windows,
        rms_dbfs=round(global_rms, 1),
        reason="no_lossy_cutoff_pattern_detected"
    )


def _probe_audio_segment(
    filepath: str,
    seek_time: float,
    duration: float,
    sample_rate: int,
    channels: int
) -> Optional[np.ndarray]:
    """Decodes a specific time slice using FFmpeg with native channel preservation."""
    try:
        cmd = [
            "ffmpeg", "-v", "quiet", "-nostdin",
            "-ss", f"{seek_time:.2f}",
            "-t", f"{duration:.2f}",
            "-i", filepath,
            "-f", "f32le",
            "-ac", str(channels),
            "-ar", str(sample_rate),
            "-"
        ]
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo
        )
        try:
            raw_bytes, _ = proc.communicate(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return None

        if len(raw_bytes) < sample_rate * channels * 4 * 0.5:  # At least 0.5s decoded
            return None

        samples = np.frombuffer(raw_bytes, dtype=np.float32)
        return samples
    except Exception as e:
        logger.debug(f"Segment decode error for {filepath} at {seek_time}s: {e}")
        return None


def analyze_spectrum(
    filepath: str,
    sample_rate: Optional[int] = None,
    channels: Optional[int] = None,
    duration: Optional[float] = None
) -> SpectralResult:
    """
    Evidence-based spectral analysis across multiple temporally distributed regions of a track.
    Preserves channels without downmixing and applies fail-closed UNKNOWN on any anomaly.
    """
    if not os.path.isfile(filepath):
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=0.0,
            valid_windows=0,
            rms_dbfs=None,
            reason="file_not_found"
        )

    try:
        filesize = os.path.getsize(filepath)
        if filesize == 0:
            return SpectralResult(
                assessment=SpectralAssessment.UNKNOWN,
                cutoff_hz=None,
                confidence=0.0,
                analyzed_duration=0.0,
                valid_windows=0,
                rms_dbfs=None,
                reason="empty_file"
            )
    except OSError:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=0.0,
            valid_windows=0,
            rms_dbfs=None,
            reason="file_stat_error"
        )

    # 1. Resolve audio stream metadata if not provided
    if sample_rate is None or channels is None or duration is None:
        try:
            from core.metadata_extractor import extract_metadata
            meta = extract_metadata(filepath)
            sample_rate = sample_rate or meta.get("samplerate", 44100)
            channels = channels or meta.get("channels", 2)
            duration = duration or meta.get("duration", 0.0)
        except Exception:
            sample_rate = 44100
            channels = 2
            duration = 0.0

    # 2. Check sample rate and Nyquist adequacy
    nyquist = sample_rate / 2.0
    if sample_rate < PROVISIONAL_MIN_SAMPLE_RATE or nyquist < PROVISIONAL_MIN_NYQUIST_HZ:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=0.0,
            valid_windows=0,
            rms_dbfs=None,
            reason="insufficient_frequency_bandwidth"
        )

    # 3. Check track duration adequacy
    if duration < PROVISIONAL_MIN_DURATION_SECONDS and duration > 0.0:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=duration,
            valid_windows=0,
            rms_dbfs=None,
            reason="insufficient_duration"
        )

    # 4. Multi-region probe strategy (Intro, Middle, Late)
    # Temporal distribution prevents lo-fi intros or acoustic silence from skewing the decision
    probe_slice_sec = 4.0
    if duration >= 20.0:
        seek_points = [
            max(2.0, duration * 0.10),   # Early region (skipping lead-in silence)
            duration * 0.50,             # Middle region
            duration * 0.80              # Late region
        ]
    elif duration >= 8.0:
        seek_points = [1.0, duration * 0.50]
    else:
        seek_points = [0.0]

    valid_segment_samples: List[np.ndarray] = []
    total_probed_duration = 0.0

    for seek_t in seek_points:
        seg = _probe_audio_segment(filepath, seek_t, probe_slice_sec, sample_rate, channels)
        if seg is not None:
            # Check energy of this individual segment
            seg_rms = _compute_rms_dbfs(seg)
            if seg_rms >= PROVISIONAL_MIN_ENERGY_DBFS:
                valid_segment_samples.append(seg)
                total_probed_duration += probe_slice_sec

    if not valid_segment_samples:
        return SpectralResult(
            assessment=SpectralAssessment.UNKNOWN,
            cutoff_hz=None,
            confidence=0.0,
            analyzed_duration=0.0,
            valid_windows=0,
            rms_dbfs=None,
            reason="insufficient_signal_energy"
        )

    # Concatenate usable regions and analyze
    combined_samples = np.concatenate(valid_segment_samples)
    return analyze_pcm_samples(
        combined_samples,
        sample_rate=sample_rate,
        channels=channels,
        analyzed_duration=total_probed_duration
    )


def estimate_spectral_cutoff(
    filepath: str,
    sample_rate: int = 44100,
    duration_probe: float = 10.0
) -> Tuple[float, float]:
    """
    Backward-compatible wrapper for legacy callers.
    Internally delegates to analyze_spectrum and conforms to the SpectralResult truth.

    Returns:
      (cutoff_hz, confidence_percentage)
    """
    try:
        res = analyze_spectrum(filepath, sample_rate=sample_rate)
        if res.assessment == SpectralAssessment.SUSPECTED_TRANSCODE:
            return float(res.cutoff_hz or 16000.0), float(res.confidence)
        elif res.assessment == SpectralAssessment.NO_LOSSY_EVIDENCE:
            return float(sample_rate / 2.0), 0.0
        else:
            # UNKNOWN or NOT_ANALYZED: Fail-closed fallback (0.0 cutoff, 0.0 confidence)
            return 0.0, 0.0
    except Exception:
        return 0.0, 0.0


def evaluate_track_quality(track: AudioTrack) -> None:
    """
    Computes a comprehensive Quality Score (0 to 100) and human-readable explanation
    for an AudioTrack, respecting the 4-state SpectralAssessment model. Modifies track in-place.

    Rules for Phase C:
      - UNKNOWN: 0 bonus espectral
      - NOT_ANALYZED: 0 bonus espectral
      - NO_LOSSY_EVIDENCE: 0 bonus espectral (unvalidated positive bonus is disabled)
      - SUSPECTED_TRANSCODE: 0 bonus espectral + container penalty
    """
    score = 0.0
    details: List[str] = []

    # 1. Container & Lossless Fidelity (Base: 45 pts max)
    if track.is_lossless:
        # Check spectral assessment or legacy fake_lossless_confidence
        is_transcode = (
            track.spectral_assessment == SpectralAssessment.SUSPECTED_TRANSCODE
            or track.fake_lossless_confidence > 50.0
        )
        if is_transcode:
            penalty = (track.fake_lossless_confidence / 100.0) * 35.0
            score += max(10.0, 45.0 - penalty)
            details.append(f"⚠️ Posible Transcodificación ({track.fake_lossless_confidence:.0f}% consistencia)")
        else:
            score += 45.0
            details.append(f"Lossless Auténtico ({track.format})")
    else:
        # Lossy format rating
        if track.format in ("AAC", "M4A", "OGG", "OPUS"):
            score += 32.0
            details.append(f"Lossy Moderno ({track.format})")
        elif track.format == "MP3":
            score += 28.0
            details.append("MP3")
        else:
            score += 20.0
            details.append(track.format or "AUDIO")

    # 2. Bitrate Score (30 pts max)
    if track.is_lossless:
        if track.spectral_assessment == SpectralAssessment.SUSPECTED_TRANSCODE or track.fake_lossless_confidence > 50.0:
            br_penalty = (track.fake_lossless_confidence / 100.0) * 18.8
            score += max(5.0, 30.0 - br_penalty)
            details.append("~Lossy kbps eq.")
        else:
            score += 30.0
            details.append(f"{track.bitrate} kbps" if track.bitrate > 0 else "Lossless kbps")
    else:
        eff_bitrate = min(320, track.bitrate) if track.bitrate > 0 else 128
        br_score = (eff_bitrate / 320.0) * 28.0
        score += br_score
        details.append(f"{eff_bitrate} kbps")

    # 3. Bit Depth & Sample Rate (15 pts max)
    if track.bit_depth >= 24:
        score += 8.0
        details.append(f"{track.bit_depth}-bit")
    elif track.bit_depth == 16:
        score += 5.0
        details.append("16-bit")

    if track.samplerate >= 96000:
        score += 7.0
        details.append(f"{track.samplerate // 1000}kHz Hi-Res")
    elif track.samplerate >= 48000:
        score += 5.0
        details.append(f"{track.samplerate // 1000}kHz")
    elif track.samplerate >= 44100:
        score += 4.0
        details.append("44.1kHz")

    # 4. Spectral Bandwidth Score (15 pts max)
    # Strict Phase C Rule:
    # UNKNOWN, NOT_ANALYZED, and NO_LOSSY_EVIDENCE receive exactly 0 positive bonus!
    # Unvalidated positive bonuses are prohibited until scientific ground truth calibration.
    if track.spectral_assessment == SpectralAssessment.NO_LOSSY_EVIDENCE:
        score += 0.0
        details.append("Sin evidencia lossy detectada")
    elif track.spectral_assessment == SpectralAssessment.UNKNOWN:
        score += 0.0
        details.append("Resultado espectral no concluyente")
    elif track.spectral_assessment == SpectralAssessment.NOT_ANALYZED:
        score += 0.0
        details.append("Análisis espectral no realizado")
    elif track.spectral_assessment == SpectralAssessment.SUSPECTED_TRANSCODE:
        score += 0.0
        details.append(f"Corte detectado ({int(track.spectral_cutoff)}Hz)" if track.spectral_cutoff else "Corte detectado")
    else:
        # Legacy fallback if spectral_assessment is missing/unspecified (e.g. from historical test mocks)
        if track.spectral_cutoff >= 21000:
            score += 15.0
            details.append("Espectro Completo (>21kHz)")
        elif track.spectral_cutoff >= 19000:
            score += 11.0
            details.append(f"Corte {int(track.spectral_cutoff)}Hz")
        elif track.spectral_cutoff >= 16000:
            score += 6.0
            details.append(f"Corte {int(track.spectral_cutoff)}Hz")
        elif track.spectral_cutoff > 0.0:
            score += 2.0
            details.append(f"Corte Bajo ({int(track.spectral_cutoff)}Hz)")

    track.quality_score = round(min(100.0, max(5.0, score)), 1)
    track.quality_details = " • ".join(details)
