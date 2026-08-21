"""
Embedded Audio Player component using Pygame mixer for preview and comparison.
"""

import os
import pygame
from typing import Optional, Callable
from PyQt6.QtCore import QObject, pyqtSignal, QTimer


class AudioPlayer(QObject):
    _instance = None
    
    # Signal emitted when playback state changes: (filepath, is_playing)
    playback_changed = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self.is_initialized = False
        self.current_playing_file: Optional[str] = None
        self.is_playing = False
        self._init_mixer()
        
        # Polling timer to detect when a song naturally finishes
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._check_playback_status)
        self.poll_timer.start(500)
        self.current_playing_file: Optional[str] = None
        self.is_playing = False
        self._init_mixer()

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
                self.playback_changed.emit(filepath, False)
                return

            pygame.mixer.music.stop()
            if self.current_playing_file and self.current_playing_file != filepath:
                # Notify that previous track stopped
                self.playback_changed.emit(self.current_playing_file, False)
                
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            self.current_playing_file = filepath
            self.is_playing = True
            self.playback_changed.emit(filepath, True)
        except Exception:
            self.is_playing = False
            self.current_playing_file = None

    def pause(self):
        """Pauses playback."""
        if self.is_initialized and self.is_playing:
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
        prev_file = self.current_playing_file
        self.current_playing_file = None
        if prev_file:
            self.playback_changed.emit(prev_file, False)

    def _check_playback_status(self):
        """Polls pygame mixer to see if it stopped playing naturally (e.g. song ended)."""
        if self.is_initialized and self.is_playing:
            if not pygame.mixer.music.get_busy():
                # Music stopped naturally
                self.is_playing = False
                prev_file = self.current_playing_file
                self.current_playing_file = None
                if prev_file:
                    self.playback_changed.emit(prev_file, False)
