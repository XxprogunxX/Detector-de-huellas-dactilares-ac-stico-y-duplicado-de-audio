"""
Audio Quality Analysis and Fake Lossless (Transcode) Detection using Spectral FFT.
"""

import os
import sys
import subprocess
import numpy as np
from typing import Tuple, Dict, Any, Optional
from core.models import AudioTrack


def estimate_spectral_cutoff(filepath: str, sample_rate: int = 22050, duration_probe: float = 10.0) -> Tuple[float, float]:
    """
    Estimates the true acoustic high-frequency cutoff frequency (in Hz)
    using FFT spectral energy analysis to detect up-sampled / transcoded audio.

    Optimized for low-memory operation:
    - sample_rate=22050 (Nyquist 11kHz, sufficient for cutoff detection up to 20kHz)
    - duration_probe=10.0s (enough for a reliable spectrum estimate)
    - Early-exit for very small files

    Returns:
        (spectral_cutoff_hz, fake_lossless_confidence_percentage)
    """
    ext = os.path.splitext(filepath)[1].lower().lstrip(".")
    is_lossless_container = ext in {"flac", "wav", "alac", "aiff", "ape", "wv"}

    # Early-exit: very small files (<2 MB) are likely short clips or corrupt,
    # no need to run the full FFT pipeline — return a conservative default.
    try:
        if os.path.getsize(filepath) < 2_000_000:
            return (22050.0 if is_lossless_container else 18000.0), 0.0
    except OSError:
        return (22050.0 if is_lossless_container else 18000.0), 0.0
    
    try:
        # Probe up to 10 seconds of audio (sufficient for reliable spectrum estimate)
        # Using ffmpeg to extract raw float32 mono PCM at 22050 Hz (half RAM vs 44100 Hz)
        cmd = [
            "ffmpeg", "-v", "quiet", "-nostdin",
            "-i", filepath,
            "-t", str(duration_probe),
            "-f", "f32le", "-ac", "1", "-ar", str(sample_rate),
            "-"
        ]
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, startupinfo=startupinfo)
        try:
            raw_bytes, _ = proc.communicate(timeout=15.0)  # 15s timeout for a 10s probe
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raw_bytes = b""

        if len(raw_bytes) < sample_rate * 2:
            return 22050.0 if is_lossless_container else 20000.0, 0.0

        audio_samples = np.frombuffer(raw_bytes, dtype=np.float32)
        if len(audio_samples) == 0 or np.max(np.abs(audio_samples)) < 1e-5:
            return 22050.0 if is_lossless_container else 20000.0, 0.0

        # Compute Power Spectral Density via Welch / FFT
        n_fft = 2048
        hop_length = 1024
        # Windowed STFT
        num_frames = (len(audio_samples) - n_fft) // hop_length
        if num_frames <= 0:
            return 22050.0, 0.0

        window = np.hanning(n_fft)
        power_spectrum = np.zeros(n_fft // 2 + 1, dtype=np.float64)
        
        step = max(1, num_frames // 40)  # Sample up to 40 frames for speed
        count = 0
        for i in range(0, num_frames, step):
            start = i * hop_length
            segment = audio_samples[start:start + n_fft] * window
            fft_vals = np.fft.rfft(segment)
            power_spectrum += np.abs(fft_vals) ** 2
            count += 1

        if count > 0:
            power_spectrum /= count

        freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
        
        # Convert to dB relative to peak power in midrange (1kHz - 5kHz)
        mid_mask = (freqs >= 1000) & (freqs <= 5000)
        peak_mid_power = np.max(power_spectrum[mid_mask]) if np.any(mid_mask) else np.max(power_spectrum)
        if peak_mid_power <= 1e-12:
            return 22050.0, 0.0

        power_db = 10.0 * np.log10(np.maximum(power_spectrum / peak_mid_power, 1e-8))

        # Scan from 14kHz to Nyquist (22.05kHz) to find where energy drops permanently below -45dB
        high_freq_idx = np.where((freqs >= 14000) & (freqs <= sample_rate / 2.0))[0]
        cutoff_hz = sample_rate / 2.0
        
        # Find frequency where power falls below -45dB and stays down
        for idx in high_freq_idx:
            # Check remaining window to see if it remains suppressed
            window_ahead = power_db[idx:idx + 10]
            if len(window_ahead) > 0 and np.mean(window_ahead) < -42.0:
                cutoff_hz = freqs[idx]
                break

        # Transcode / Fake Lossless Detection:
        # If in a FLAC/WAV container, but cutoff is <= 16.5 kHz (128k MP3 transcode)
        # or <= 18.8 kHz with sharp drop (192k MP3 transcode)
        confidence = 0.0
        if is_lossless_container:
            if cutoff_hz < 16800:
                confidence = min(100.0, max(80.0, 100.0 - (cutoff_hz - 14000) / 2800 * 20.0))
            elif cutoff_hz < 19200 and np.mean(power_db[freqs > 19500]) < -55.0:
                confidence = min(80.0, max(50.0, 80.0 - (cutoff_hz - 16800) / 2400 * 30.0))

        return float(cutoff_hz), confidence

    except Exception:
        return 22050.0 if is_lossless_container else 20000.0, 0.0


def evaluate_track_quality(track: AudioTrack) -> None:
    """
    Computes a comprehensive Quality Score (0 to 100) and human-readable explanation
    for an AudioTrack, taking into account format, bitrate, sample rate, bit depth,
    and genuine spectral cutoff. Modifies track in-place.
    """
    score = 0.0
    details = []

    # 1. Container & Lossless fidelity (Base: 40 pts max)
    if track.is_lossless:
        if track.fake_lossless_confidence > 50.0:
            penalty = (track.fake_lossless_confidence - 50.0) / 50.0 * 27.0
            score += (45.0 - penalty)
            details.append(f"⚠️ Posible Transcodificación ({track.fake_lossless_confidence:.0f}% prob. desde lossy)")
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
            details.append(track.format)

    # 2. Bitrate Score (30 pts max)
    if track.is_lossless and track.fake_lossless_confidence <= 50.0:
        score += 30.0
        details.append(f"{track.bitrate} kbps")
    else:
        # Scale lossy bitrate (e.g. 320k -> 28 pts, 256k -> 24 pts, 128k -> 12 pts)
        eff_bitrate = min(320, track.bitrate) if track.bitrate > 0 else 128
        if track.fake_lossless_confidence > 50.0:
            eff_bitrate = 128 if track.spectral_cutoff < 17000 else 192
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
    if track.spectral_cutoff >= 21000:
        score += 15.0
        details.append("Espectro Completo (>21kHz)")
    elif track.spectral_cutoff >= 19000:
        score += 11.0
        details.append(f"Corte {int(track.spectral_cutoff)}Hz")
    elif track.spectral_cutoff >= 16000:
        score += 6.0
        details.append(f"Corte {int(track.spectral_cutoff)}Hz")
    else:
        score += 2.0
        details.append(f"Corte Bajo ({int(track.spectral_cutoff)}Hz)")

    track.quality_score = round(min(100.0, max(5.0, score)), 1)
    track.quality_details = " • ".join(details)
