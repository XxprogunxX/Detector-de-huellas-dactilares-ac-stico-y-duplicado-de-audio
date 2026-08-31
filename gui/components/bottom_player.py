"""
Bottom Player Bar — Ultra-sleek Preescucha & Audio Scrubbing Bar.
Matches the modern dark Figma aesthetic with interactive waveform seeking.
"""

import os
import math
import hashlib
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRectF
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush, QMouseEvent
import qtawesome as qta

from gui.styles import COLORS
from gui.components.audio_player import AudioPlayer


def _format_time(seconds: float) -> str:
    """Formats seconds into mm:ss."""
    s = max(0, int(seconds))
    m = s // 60
    s = s % 60
    return f"{m:02d}:{s:02d}"


class InteractiveWaveformSeeker(QWidget):
    """
    Interactive Waveform Seekbar:
    - Renders vertical equalizer/waveform bars across its entire width.
    - Bars before the current playback position glow in cyan (#00E5FF).
    - Remaining bars are drawn in dark slate (#273549).
    - Supports full mouse clicking and dragging to seek to any position.
    """
    seek_requested = pyqtSignal(float)  # Emits target time in seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self._current_pos: float = 0.0
        self._total_duration: float = 0.0
        self._is_dragging: bool = False
        self._hover_x: Optional[float] = None
        self._bars_cache: List[float] = []
        self._cached_file: str = ""

    def set_track_info(self, filepath: str, duration: float):
        """Initializes waveform envelope from track and sets duration."""
        self._total_duration = max(1.0, duration)
        if filepath != self._cached_file:
            self._cached_file = filepath
            self._generate_waveform_profile(filepath)
        self.update()

    def set_position(self, current_pos: float, total_duration: float):
        """Updates real-time playback position without interrupting dragging."""
        if not self._is_dragging:
            self._current_pos = current_pos
            if total_duration > 0:
                self._total_duration = total_duration
            self.update()

    def _generate_waveform_profile(self, filepath: str):
        """Generates deterministic naturalistic waveform bars for the track."""
        seed = int(hashlib.md5(filepath.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
        import random
        rng = random.Random(seed)

        # Generate 120 baseline height ratios [0.15 .. 0.95]
        bars = []
        for i in range(120):
            # Smooth envelope curve with peaks and valleys
            envelope = 0.5 + 0.35 * math.sin(i * 0.12) + 0.15 * math.cos(i * 0.31)
            jitter = rng.uniform(-0.2, 0.2)
            height = max(0.15, min(0.95, envelope + jitter))
            bars.append(height)
        self._bars_cache = bars

    def _get_progress_ratio(self) -> float:
        if self._total_duration <= 0:
            return 0.0
        return max(0.0, min(1.0, self._current_pos / self._total_duration))

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._total_duration > 0:
            self._is_dragging = True
            ratio = max(0.0, min(1.0, event.position().x() / max(1.0, self.width())))
            target_time = ratio * self._total_duration
            self._current_pos = target_time
            self.seek_requested.emit(target_time)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        self._hover_x = event.position().x()
        if self._is_dragging and self._total_duration > 0:
            ratio = max(0.0, min(1.0, event.position().x() / max(1.0, self.width())))
            target_time = ratio * self._total_duration
            self._current_pos = target_time
            self.seek_requested.emit(target_time)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self._is_dragging = False
            ratio = max(0.0, min(1.0, event.position().x() / max(1.0, self.width())))
            target_time = ratio * self._total_duration
            self._current_pos = target_time
            self.seek_requested.emit(target_time)
            self.update()

    def leaveEvent(self, event):
        self._hover_x = None
        self._is_dragging = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        bar_w = 3.5
        gap = 2.5
        step = bar_w + gap
        num_bars = max(10, int(w // step))

        # Background track container
        bg_rect = QRectF(0, (h - 24) / 2, w, 24)
        painter.setBrush(QColor(15, 23, 42, 100))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bg_rect, 4, 4)

        if not self._bars_cache:
            self._generate_waveform_profile("default_idle")

        progress_ratio = self._get_progress_ratio()
        cutoff_x = progress_ratio * w

        center_y = h / 2.0
        max_bar_height = h - 8.0

        for i in range(num_bars):
            x = i * step + gap
            if x + bar_w > w:
                break

            # Interpolate height from cache
            idx = int((i / max(1, num_bars)) * len(self._bars_cache)) % len(self._bars_cache)
            bar_height = max(4.0, self._bars_cache[idx] * max_bar_height)
            y = center_y - bar_height / 2.0

            is_played = (x + bar_w / 2.0) <= cutoff_x

            if is_played:
                # Active Glowing Cyan
                painter.setBrush(QColor(0, 229, 255))
            else:
                # Dark Slate Inactive
                painter.setBrush(QColor(42, 53, 72))

            painter.drawRoundedRect(QRectF(x, y, bar_w, bar_height), 1.5, 1.5)


class BottomPlayerBar(QFrame):
    """
    Sleek Fixed Bottom Player Bar:
    - Circular Play/Pause button (#00E5FF) + Skip Backward/Forward.
    - PREESCUCHA: CANAL A header tag.
    - Clean Artist - Title typography.
    - Detailed audio specifications (Format, Bit depth, Sample rate, Channels).
    - Realtime interactive waveform seeking bar (click & drag to scrub anywhere).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(76)
        self.setStyleSheet(f"""
            BottomPlayerBar {{
                background-color: #0A0F18;
                border-top: 1px solid {COLORS['border']};
            }}
        """)

        self._player = AudioPlayer.get_instance()
        self._player.playback_changed.connect(self._on_playback_changed)
        self._player.position_updated.connect(self._on_position_updated)
        self._player.metadata_loaded.connect(self._on_metadata_loaded)
        self._current_file: str = ""

        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 10, 24, 10)
        root.setSpacing(18)

        # ── 1. Circular Control Deck (Left) ────────────────────────
        deck_layout = QHBoxLayout()
        deck_layout.setSpacing(10)
        deck_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Back 10s
        self.btn_back = QPushButton()
        self.btn_back.setFixedSize(34, 34)
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setIcon(qta.icon("fa5s.undo", color=COLORS["text_muted"]))
        self.btn_back.setToolTip("Retroceder 10s")
        self.btn_back.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 17px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_card_highlight']};
                border-color: {COLORS['cyan_dim']};
            }}
        """)
        self.btn_back.clicked.connect(lambda: self._player.seek_relative(-10.0))
        deck_layout.addWidget(self.btn_back)

        # Central Play/Pause Circular Cyan Button
        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(44, 44)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setIcon(qta.icon("fa5s.play", color="#0A0F18"))
        self.btn_play.setToolTip("Reproducir / Pausar")
        self.btn_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['cyan']};
                border: none;
                border-radius: 22px;
            }}
            QPushButton:hover {{
                background-color: #33ECFF;
            }}
            QPushButton:pressed {{
                background-color: #00B8D4;
            }}
        """)
        self.btn_play.clicked.connect(self._toggle_play)
        deck_layout.addWidget(self.btn_play)

        # Forward 10s
        self.btn_fwd = QPushButton()
        self.btn_fwd.setFixedSize(34, 34)
        self.btn_fwd.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fwd.setIcon(qta.icon("fa5s.redo", color=COLORS["text_muted"]))
        self.btn_fwd.setToolTip("Adelantar 10s")
        self.btn_fwd.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 17px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_card_highlight']};
                border-color: {COLORS['cyan_dim']};
            }}
        """)
        self.btn_fwd.clicked.connect(lambda: self._player.seek_relative(10.0))
        deck_layout.addWidget(self.btn_fwd)

        root.addLayout(deck_layout)

        # ── 2. Track Info & Tech Specs (Middle-Left) ───────────────
        info_widget = QWidget()
        info_widget.setStyleSheet("background: transparent;")
        info_widget.setMinimumWidth(260)
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(4, 0, 8, 0)
        info_layout.setSpacing(2)
        info_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Channel Tag
        self.lbl_channel = QLabel("PREESCUCHA: CANAL A")
        self.lbl_channel.setFont(QFont("JetBrains Mono", 8, QFont.Weight.Bold))
        self.lbl_channel.setStyleSheet(f"color: {COLORS['cyan']}; letter-spacing: 1px;")

        # Title
        self.lbl_title = QLabel("Sin reproducción activa")
        self.lbl_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_title.setStyleSheet("color: #F8FAFC;")

        # Technical specs
        self.lbl_specs = QLabel("Seleccione una pista para reproducir")
        self.lbl_specs.setFont(QFont("JetBrains Mono", 8))
        self.lbl_specs.setStyleSheet(f"color: {COLORS['text_muted']};")

        info_layout.addWidget(self.lbl_channel)
        info_layout.addWidget(self.lbl_title)
        info_layout.addWidget(self.lbl_specs)

        root.addWidget(info_widget)

        # ── 3. Interactive Waveform & Scrubbing Area (Middle-Right) ──
        wave_container = QWidget()
        wave_container.setStyleSheet("background: transparent;")
        wave_layout = QHBoxLayout(wave_container)
        wave_layout.setContentsMargins(0, 0, 0, 0)
        wave_layout.setSpacing(12)
        wave_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Current timestamp (e.g. 01:42)
        self.lbl_current_time = QLabel("00:00")
        self.lbl_current_time.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Medium))
        self.lbl_current_time.setStyleSheet(f"color: {COLORS['text_main']};")
        self.lbl_current_time.setFixedWidth(46)
        self.lbl_current_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        wave_layout.addWidget(self.lbl_current_time)

        # Waveform Seekbar
        self.waveform_seeker = InteractiveWaveformSeeker()
        self.waveform_seeker.seek_requested.connect(self._player.seek)
        wave_layout.addWidget(self.waveform_seeker, stretch=1)

        # Total duration timestamp (e.g. 06:23)
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Medium))
        self.lbl_total_time.setStyleSheet(f"color: {COLORS['text_muted']};")
        self.lbl_total_time.setFixedWidth(46)
        self.lbl_total_time.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        wave_layout.addWidget(self.lbl_total_time)

        root.addWidget(wave_container, stretch=1)

    def _on_playback_changed(self, filepath: str, is_playing: bool):
        if is_playing:
            self._current_file = filepath
            self.btn_play.setIcon(qta.icon("fa5s.pause", color="#0A0F18"))
            self.btn_play.setToolTip("Pausar")
            self._update_track_labels(filepath)
        else:
            self.btn_play.setIcon(qta.icon("fa5s.play", color="#0A0F18"))
            self.btn_play.setToolTip("Reproducir")

    def _on_position_updated(self, current_pos: float, total_dur: float):
        self.lbl_current_time.setText(_format_time(current_pos))
        self.lbl_total_time.setText(_format_time(total_dur))
        self.waveform_seeker.set_position(current_pos, total_dur)

    def _on_metadata_loaded(self, meta: dict):
        fmt = meta.get("format", "AUDIO")
        bit_depth = meta.get("bit_depth", 16)
        sample_rate_khz = meta.get("samplerate", 44100) / 1000.0
        channels = "Stereo" if meta.get("channels", 2) == 2 else "Mono"
        bitrate = meta.get("bitrate", 0)

        if fmt in ("FLAC", "WAV", "ALAC", "AIFF"):
            specs_str = f"{fmt}  |  {bit_depth}-bit  |  {sample_rate_khz:g} kHz  |  {channels}"
        else:
            specs_str = f"{fmt}  |  {bitrate} kbps  |  {sample_rate_khz:g} kHz  |  {channels}"

        self.lbl_specs.setText(specs_str)

        # Title & Artist
        artist = meta.get("artist", "").strip()
        title = meta.get("title", "").strip()
        if artist and title:
            self.lbl_title.setText(f"{artist} – {title}")
        elif title:
            self.lbl_title.setText(title)
        elif self._current_file:
            self._update_track_labels(self._current_file)

        # Update waveform with duration
        dur = meta.get("duration", 0.0)
        self.waveform_seeker.set_track_info(self._current_file, dur)

    def _update_track_labels(self, filepath: str):
        filename = os.path.basename(filepath)
        name_no_ext = os.path.splitext(filename)[0]

        # Clean underscores to spaces
        clean_name = name_no_ext.replace("_", " ")

        if " - " in clean_name:
            parts = clean_name.split(" - ", 1)
            self.lbl_title.setText(f"{parts[0].strip()} – {parts[1].strip()}")
        else:
            self.lbl_title.setText(clean_name)

    def _toggle_play(self):
        if self._player.is_playing:
            self._player.pause()
        elif self._current_file:
            self._player.unpause()

    def play_file(self, filepath: str, channel_label: str = "PREESCUCHA: CANAL A"):
        """Starts playback and customizes channel label if comparing."""
        self.lbl_channel.setText(channel_label)
        self._player.play(filepath)
