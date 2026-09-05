"""
Data models for the Audio Duplicate & Acoustic Fingerprinting Detector.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
import os


from core.spectral_types import SpectralAssessment, SpectralResult


class DuplicateType(str, Enum):
    EXACT_HASH = "EXACT_HASH"                # 100% Bit-for-bit identical file hash
    EXACT_AUDIO = "EXACT_AUDIO"              # 100% Identical decoded PCM audio stream
    ACOUSTIC_DUPLICATE = "ACOUSTIC_DUPLICATE"# >= 95% Acoustic similarity (same recording)
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"# 80% - 94.9% or duration variance (remaster, edit, live)
    LOW_CONFIDENCE_REVIEW = "LOW_CONFIDENCE_REVIEW"  # 40% - 79.9% Requires Human Review
    NO_MATCH = "NO_MATCH"                    # Distinct audio
    UNCERTAIN = "UNCERTAIN"                  # Missing signals, could not compare


class FileAction(str, Enum):
    UNSET = "UNSET"
    KEEP = "KEEP"
    DELETE = "DELETE"


@dataclass
class AudioTrack:
    filepath: str
    filesize: int = 0
    mtime: float = 0.0
    mtime_ns: int = 0
    quick_signature: str = ""
    sha256: str = ""
    audio_hash: str = ""
    duration: float = 0.0
    format: str = ""
    bitrate: int = 0          # in kbps
    samplerate: int = 0       # in Hz
    channels: int = 2
    bit_depth: int = 16
    is_lossless: bool = False
    spectral_cutoff: float = 0.0  # in Hz
    fake_lossless_confidence: float = 0.0  # 0.0 to 100.0%
    spectral_assessment: SpectralAssessment = SpectralAssessment.UNKNOWN
    quality_score: float = 0.0
    quality_details: str = ""
    fingerprint_raw: List[int] = field(default_factory=list)
    title: str = ""
    artist: str = ""
    album: str = ""
    action: FileAction = FileAction.UNSET
    id: Optional[int] = None

    @property
    def filename(self) -> str:
        return os.path.basename(self.filepath)

    @property
    def formatted_duration(self) -> str:
        mins = int(self.duration // 60)
        secs = int(self.duration % 60)
        return f"{mins}:{secs:02d}"

    @property
    def formatted_size(self) -> str:
        if self.filesize < 1024 * 1024:
            return f"{self.filesize / 1024:.1f} KB"
        return f"{self.filesize / (1024 * 1024):.1f} MB"

    @property
    def display_title(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        if self.title:
            return self.title
        # Fallback to filename without extension
        return os.path.splitext(self.filename)[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "filepath": self.filepath,
            "filename": self.filename,
            "filesize": self.filesize,
            "mtime": self.mtime,
            "mtime_ns": self.mtime_ns,
            "quick_signature": self.quick_signature,
            "sha256": self.sha256,
            "audio_hash": self.audio_hash,
            "duration": self.duration,
            "format": self.format,
            "bitrate": self.bitrate,
            "samplerate": self.samplerate,
            "channels": self.channels,
            "bit_depth": self.bit_depth,
            "is_lossless": self.is_lossless,
            "spectral_cutoff": self.spectral_cutoff,
            "fake_lossless_confidence": self.fake_lossless_confidence,
            "spectral_assessment": self.spectral_assessment.value if isinstance(self.spectral_assessment, SpectralAssessment) else str(self.spectral_assessment),
            "quality_score": self.quality_score,
            "quality_details": self.quality_details,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "action": self.action.value,
        }


    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioTrack':
        action_val = data.get("action", "UNSET")
        try:
            action = FileAction(action_val)
        except Exception:
            action = FileAction.UNSET

        raw_assess = data.get("spectral_assessment")
        if raw_assess:
            try:
                spectral_assessment = SpectralAssessment(raw_assess)
            except Exception:
                spectral_assessment = SpectralAssessment.UNKNOWN
        else:
            # Historical session fallback: if not present in saved session, default to UNKNOWN
            spectral_assessment = SpectralAssessment.UNKNOWN

        return cls(
            id=data.get("id"),
            filepath=data.get("filepath", ""),
            filesize=data.get("filesize", 0),
            mtime=data.get("mtime", 0.0),
            mtime_ns=data.get("mtime_ns", int(data.get("mtime", 0.0) * 1_000_000_000)),
            quick_signature=data.get("quick_signature", ""),
            sha256=data.get("sha256", ""),
            audio_hash=data.get("audio_hash", ""),
            duration=data.get("duration", 0.0),
            format=data.get("format", ""),
            bitrate=data.get("bitrate", 0),
            samplerate=data.get("samplerate", 44100),
            channels=data.get("channels", 2),
            bit_depth=data.get("bit_depth", 16),
            is_lossless=bool(data.get("is_lossless", False)),
            spectral_cutoff=data.get("spectral_cutoff", 0.0),
            fake_lossless_confidence=float(data.get("fake_lossless_confidence", 0.0)),
            spectral_assessment=spectral_assessment,
            quality_score=data.get("quality_score", 0.0),
            quality_details=data.get("quality_details", ""),
            fingerprint_raw=data.get("fingerprint_raw", []),
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            album=data.get("album", ""),
            action=action
        )


@dataclass
class EvidenceReport:
    track_a_path: str
    track_b_path: str
    classification: DuplicateType
    confidence: float  # 0.0 to 100.0
    
    # Raw signals
    is_exact_hash: bool = False
    is_exact_audio: bool = False
    chromaprint_similarity: Optional[float] = None
    temporal_offset_frames: Optional[int] = None
    duration_diff: Optional[float] = None
    spectral_diff: Optional[float] = None
    metadata_match: Optional[bool] = None
    requires_manual_review: bool = False
    
    # Human readable explanation
    reasons: List[str] = field(default_factory=list)


@dataclass
class DuplicateGroup:
    group_id: str
    primary_type: DuplicateType
    tracks: List[AudioTrack] = field(default_factory=list)
    best_track_path: str = ""
    best_track_reason: str = ""
    average_similarity: float = 100.0
    space_saving_bytes: int = 0
    requires_manual_review: bool = False

    def recalculate_space_saving(self) -> int:
        if len(self.tracks) <= 1:
            self.space_saving_bytes = 0
            return 0
        self.space_saving_bytes = sum(t.filesize for t in self.tracks if t.action == FileAction.DELETE)
        return self.space_saving_bytes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "primary_type": self.primary_type.value,
            "best_track_path": self.best_track_path,
            "best_track_reason": self.best_track_reason,
            "average_similarity": self.average_similarity,
            "space_saving_bytes": self.space_saving_bytes,
            "requires_manual_review": self.requires_manual_review,
            "tracks": [t.to_dict() for t in self.tracks]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DuplicateGroup':
        try:
            ptype = DuplicateType(data.get("primary_type", "ACOUSTIC_DUPLICATE"))
        except Exception:
            ptype = DuplicateType.ACOUSTIC_DUPLICATE

        tracks = [AudioTrack.from_dict(t) for t in data.get("tracks", [])]

        # Defensive fallback: restore serialized flag, but strictly enforce True
        # for POSSIBLE_DUPLICATE and LOW_CONFIDENCE_REVIEW to prevent accidental auto-deletion
        req_review = bool(data.get("requires_manual_review", False))
        if ptype in (DuplicateType.POSSIBLE_DUPLICATE, DuplicateType.LOW_CONFIDENCE_REVIEW):
            req_review = True

        return cls(
            group_id=data.get("group_id", ""),
            primary_type=ptype,
            tracks=tracks,
            best_track_path=data.get("best_track_path", ""),
            best_track_reason=data.get("best_track_reason", ""),
            average_similarity=float(data.get("average_similarity", 100.0)),
            space_saving_bytes=int(data.get("space_saving_bytes", 0)),
            requires_manual_review=req_review
        )


def prune_duplicate_groups(groups: List[DuplicateGroup]) -> List[DuplicateGroup]:
    """
    Prunes zombie duplicate groups where len(group.tracks) <= 1:
    1. A group with <= 1 tracks is no longer a duplicate group.
       - It is pruned from the returned list.
       - Any surviving track has any obsolete DELETE action cleared (reset to FileAction.KEEP).
       - Space saving is reset to 0 so it contributes no fake savings.
    2. For surviving groups (len(group.tracks) > 1):
       - If best_track_path is still present in group.tracks, it is preserved.
       - If best_track_path was deleted or is not in group.tracks, it is recalculated
         safely based on the track with the highest quality score.
       - If group.requires_manual_review is True:
         Do NOT assign FileAction.DELETE to remaining tracks! Tracks must remain KEEP or UNSET.
       - group.recalculate_space_saving() is called.
    """
    surviving_groups: List[DuplicateGroup] = []
    for g in groups:
        if len(g.tracks) <= 1:
            g.space_saving_bytes = 0
            for t in g.tracks:
                if t.action == FileAction.DELETE:
                    t.action = FileAction.KEEP
            continue

        track_paths = {t.filepath for t in g.tracks}
        if g.best_track_path not in track_paths:
            best_t = max(g.tracks, key=lambda t: (t.quality_score, t.bitrate, t.filesize))
            g.best_track_path = best_t.filepath
            g.best_track_reason = f"Mejor calidad tras actualización ({best_t.quality_score:.0f} pts)"

            if g.requires_manual_review:
                for t in g.tracks:
                    if t.action == FileAction.DELETE:
                        t.action = FileAction.UNSET
            else:
                best_t.action = FileAction.KEEP

        g.recalculate_space_saving()
        surviving_groups.append(g)

    return surviving_groups


@dataclass
class ScanCoverageReport:
    is_complete: bool = True
    is_approximate: bool = False
    candidate_pairs_generated: int = 0
    candidate_pairs_retained: int = 0
    candidate_pairs_dropped: int = 0
    oversized_buckets: int = 0
    worker_failures: int = 0
    actual_comparisons: int = 0
    scan_status: str = "SUCCESS"  # SUCCESS, CANCELLED, FAILED


@dataclass
class ScanStats:
    total_files_found: int = 0
    is_complete: bool = True
    is_approximate: bool = False
    candidate_pairs_generated: int = 0
    candidate_pairs_retained: int = 0
    candidate_pairs_dropped: int = 0
    oversized_buckets: int = 0
    worker_failures: int = 0
    files_scanned: int = 0
    files_from_cache: int = 0
    files_failed: int = 0
    exact_duplicates_count: int = 0
    acoustic_duplicates_count: int = 0
    possible_duplicates_count: int = 0
    total_groups_count: int = 0
    potential_space_saving: int = 0
    elapsed_seconds: float = 0.0
    is_running: bool = False
    is_paused: bool = False
    current_file: str = ""
    phase: str = "Idle"
    progress_ratio: Optional[float] = None
    comparison_current: int = 0
    comparison_total: int = 0

    @property
    def files_processed(self) -> int:
        return self.files_scanned

    @property
    def exact_hash_groups_count(self) -> int:
        return self.exact_duplicates_count

    @property
    def acoustic_duplicate_groups_count(self) -> int:
        return self.acoustic_duplicates_count

    @property
    def progress_percentage(self) -> float:
        if self.progress_ratio is not None:
            return max(0.0, min(100.0, self.progress_ratio * 100.0))
        if self.total_files_found > 0:
            return max(0.0, min(100.0, (self.files_scanned / self.total_files_found) * 100.0))
        return 0.0

    @property
    def throughput_fps(self) -> float:
        if self.elapsed_seconds > 0:
            if self.files_scanned > 0:
                return self.files_scanned / self.elapsed_seconds
            elif self.total_files_found > 0:
                return self.total_files_found / self.elapsed_seconds
        return 0.0

    @property
    def eta_seconds(self) -> float:
        if self.throughput_fps > 0 and self.total_files_found > self.files_scanned and self.files_scanned > 0:
            return (self.total_files_found - self.files_scanned) / self.throughput_fps
        return 0.0


