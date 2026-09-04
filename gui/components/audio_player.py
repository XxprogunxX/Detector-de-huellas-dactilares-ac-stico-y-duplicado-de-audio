"""
Embedded Audio Player component using Pygame mixer for preview and comparison.
"""

import os
import pygame
from typing import Optional, Callable, Dict, Any
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from core.metadata_extractor import extract_metadata


class AudioPlayer(QObject):
    _instance = None
    
    # Signal emitted when playback state changes: (filepath, is_playing)
    playback_changed = pyqtSignal(str, bool)
    # Signal emitted with (current_seconds, total_duration_seconds)
    position_updated = pyqtSignal(float, float)
    # Signal emitted when metadata for current track is loaded: (metadata_dict)
    metadata_loaded = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.is_initialized = False
        self.current_playing_file: Optional[str] = None
        self.is_playing = False
        self.current_duration: float = 0.0
        self.current_meta: Dict[str, Any] = {}
        self._start_offset: float = 0.0
        
        self._init_mixer()
        
        # Position and state polling timer
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_status)
        self.poll_timer.start(100)

    @classmethod
    def get_instance(cls) -> "AudioPlayer":
        if cls._instance is None:
            cls._instance = AudioPlayer()
        return cls._instance

    def _init_mixer(self):
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            self.is_initialized = True
        except Exception:
            self.is_initialized = False

    def play(self, filepath: str, on_state_change: Optional[Callable] = None):
        """Plays the specified audio file, stopping any currently playing track."""
        if not self.is_initialized:
            self._init_mixer()

        if not os.path.exists(filepath):
            return

        try:
            # If same file is playing, pause/unpause toggle
            if self.current_playing_file == filepath and self.is_playing:
                self.pause()
                return

            if self.current_playing_file and self.current_playing_file != filepath:
                pygame.mixer.music.stop()
                self.playback_changed.emit(self.current_playing_file, False)

            # Extract technical metadata and duration
            meta = extract_metadata(filepath)
            self.current_meta = meta
            self.current_duration = max(1.0, float(meta.get("duration", 0.0)))
            self._start_offset = 0.0

            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            self.current_playing_file = filepath
            self.is_playing = True

            self.metadata_loaded.emit(meta)
            self.playback_changed.emit(filepath, True)
            self.position_updated.emit(0.0, self.current_duration)
        except Exception as e:
            self.is_playing = False
            self.current_playing_file = None

    def seek(self, target_seconds: float):
        """Seeks playback to a specific timestamp in seconds."""
        if not self.is_initialized or not self.current_playing_file:
            return

        target_seconds = max(0.0, min(self.current_duration - 0.5, target_seconds))
        try:
            pygame.mixer.music.play(start=target_seconds)
            self._start_offset = target_seconds
            self.is_playing = True
            self.playback_changed.emit(self.current_playing_file, True)
            self.position_updated.emit(self._start_offset, self.current_duration)
        except Exception:
            pass

    def seek_relative(self, delta_seconds: float):
        """Skips forward or backward by delta_seconds."""
        current = self.get_position()
        self.seek(current + delta_seconds)

    def get_position(self) -> float:
        """Returns the current playback position in seconds."""
        if not self.is_initialized or not self.current_playing_file:
            return 0.0
        try:
            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms >= 0:
                return min(self.current_duration, self._start_offset + (pos_ms / 1000.0))
        except Exception:
            pass
        return self._start_offset

    def pause(self):
        """Pauses playback."""
        if self.is_initialized and self.is_playing:
            # Update offset to current elapsed before pausing
            self._start_offset = self.get_position()
            pygame.mixer.music.pause()
            self.is_playing = False
            if self.current_playing_file:
                self.playback_changed.emit(self.current_playing_file, False)

    def unpause(self):
        """Unpauses playback."""
        if self.is_initialized and not self.is_playing and self.current_playing_file:
            pygame.mixer.music.unpause()
            self.is_playing = True
            self.playback_changed.emit(self.current_playing_file, True)

    def stop(self):
        """Stops playback."""
        if self.is_initialized:
            pygame.mixer.music.stop()
        self.is_playing = False
        self._start_offset = 0.0
        prev_file = self.current_playing_file
        self.current_playing_file = None
        if prev_file:
            self.playback_changed.emit(prev_file, False)
            self.position_updated.emit(0.0, 0.0)

    def _poll_status(self):
        """Periodic status update to emit position and detect track completion."""
        if self.is_initialized and self.current_playing_file:
            if self.is_playing:
                if not pygame.mixer.music.get_busy():
                    # Song ended
                    self.is_playing = False
                    prev_file = self.current_playing_file
                    self.current_playing_file = None
                    self._start_offset = 0.0
                    if prev_file:
                        self.playback_changed.emit(prev_file, False)
                        self.position_updated.emit(0.0, 0.0)
                else:
                    curr_pos = self.get_position()
                    self.position_updated.emit(curr_pos, self.current_duration)

