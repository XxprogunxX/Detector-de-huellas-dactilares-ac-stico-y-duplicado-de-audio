"""
Data models for the Audio Duplicate & Acoustic Fingerprinting Detector.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
import os


class DuplicateType(str, Enum):
    EXACT_HASH = "EXACT_HASH"                # 100% Bit-for-bit identical file hash
    EXACT_AUDIO = "EXACT_AUDIO"              # 100% Identical decoded PCM audio stream
    ACOUSTIC_DUPLICATE = "ACOUSTIC_DUPLICATE"# >= 95% Acoustic similarity (same recording)
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"# 80% - 94.9% or duration variance (remaster, edit, live)
    NO_MATCH = "NO_MATCH"                    # Distinct audio


class FileAction(str, Enum):
    UNSET = "UNSET"
    KEEP = "KEEP"
    DELETE = "DELETE"


@dataclass
class AudioTrack:
    filepath: str
    filesize: int = 0
    mtime: float = 0.0
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
    is_fake_lossless: bool = False
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
            "is_fake_lossless": self.is_fake_lossless,
            "quality_score": self.quality_score,
            "quality_details": self.quality_details,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "action": self.action.value,
        }


@dataclass
class ComparisonResult:
    track_a_path: str
    track_b_path: str
    similarity: float
    duplicate_type: DuplicateType
    duration_diff: float
    reason: str


@dataclass
class DuplicateGroup:
    group_id: str
    primary_type: DuplicateType
    tracks: List[AudioTrack] = field(default_factory=list)
    best_track_path: str = ""
    best_track_reason: str = ""
    average_similarity: float = 100.0
    space_saving_bytes: int = 0

    def recalculate_space_saving(self) -> int:
        if len(self.tracks) <= 1:
            self.space_saving_bytes = 0
            return 0
        total_bytes = sum(t.filesize for t in self.tracks)
        # Find best track or track marked KEEP
        keep_tracks = [t for t in self.tracks if t.action == FileAction.KEEP]
        if keep_tracks:
            kept_bytes = keep_tracks[0].filesize
        else:
            best = next((t for t in self.tracks if t.filepath == self.best_track_path), self.tracks[0])
            kept_bytes = best.filesize
        self.space_saving_bytes = max(0, total_bytes - kept_bytes)
        return self.space_saving_bytes


@dataclass
class ScanStats:
    total_files_found: int = 0
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
