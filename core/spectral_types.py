"""
Core Spectral Analysis Types (Phase C / AC-005, AC-017).

Pure data structures and enumeration for evidence-based spectral analysis.
Independent of AudioTrack, FFmpeg, SQLite, and GUI frameworks to prevent circular imports.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any


class SpectralAssessment(str, Enum):
    """
    Evidence-based spectral assessment classification states.

    Semantics:
      NO_LOSSY_EVIDENCE:
        The analysis performed found no sufficient spectral evidence of a lossy compression
        artifact. This DOES NOT guarantee the audio is an authentic original lossless master.
      SUSPECTED_TRANSCODE:
        Spectral signals consistent with upscaling or transcoding from a lossy source were detected
        persistently across multiple analysis regions and channels.
      UNKNOWN:
        Fail-closed default. Analysis was attempted but could not produce conclusive evidence
        (e.g., decode failure, corrupt audio, silence, insufficient bandwidth, or borderline signals).
      NOT_ANALYZED:
        Spectral analysis was deliberately not executed (e.g. spectral_analysis=False or lossy container).
    """
    NO_LOSSY_EVIDENCE = "no_lossy_evidence"
    SUSPECTED_TRANSCODE = "suspected_transcode"
    UNKNOWN = "unknown"
    NOT_ANALYZED = "not_analyzed"


@dataclass(frozen=True)
class SpectralResult:
    """
    Structured, auditable result of spectral analysis.

    Attributes:
      assessment: The high-level SpectralAssessment state.
      cutoff_hz: Estimated low-pass frequency cutoff in Hz (None if not detected or unknown).
      confidence: Internal consistency of the evidence found, scaled from 0.0 to 100.0.
                  NOTE: This is NOT a statistical probability of a track being fake lossless.
      analyzed_duration: Usable audio duration analyzed in seconds.
      valid_windows: Number of FFT windows with sufficient energy and valid data.
      rms_dbfs: Measured root-mean-square energy level in dBFS across analyzed segments.
      reason: Machine-readable explanation for the assessment decision.
    """
    assessment: SpectralAssessment
    cutoff_hz: Optional[float]
    confidence: float
    analyzed_duration: float
    valid_windows: int
    rms_dbfs: Optional[float]
    reason: str

    def __post_init__(self):
        if not (0.0 <= self.confidence <= 100.0):
            raise ValueError(f"confidence must be between 0.0 and 100.0, got: {self.confidence}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assessment": self.assessment.value,
            "cutoff_hz": self.cutoff_hz,
            "confidence": float(self.confidence),
            "analyzed_duration": float(self.analyzed_duration),
            "valid_windows": int(self.valid_windows),
            "rms_dbfs": self.rms_dbfs,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpectralResult":
        raw_assess = data.get("assessment", "unknown")
        try:
            assessment = SpectralAssessment(raw_assess)
        except Exception:
            assessment = SpectralAssessment.UNKNOWN

        return cls(
            assessment=assessment,
            cutoff_hz=data.get("cutoff_hz"),
            confidence=float(data.get("confidence", 0.0)),
            analyzed_duration=float(data.get("analyzed_duration", 0.0)),
            valid_windows=int(data.get("valid_windows", 0)),
            rms_dbfs=data.get("rms_dbfs"),
            reason=data.get("reason", "unknown"),
        )
