"""
Bottom Player Bar — fixed audio playback strip at the bottom of the window.
Audio Cleaner Figma design.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QPainter, QColor, QPen
import qtawesome as qta
import math

from gui.styles import COLORS
from gui.components.audio_player import AudioPlayer


class MiniWaveform(QWidget):
    """Animated mini waveform visualizer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 36)
        self._bars = [0.2] * 20
        self._tick = 0
        self._active = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)

    def set_active(self, active: bool):
        self._active = active
        if active:
            self._timer.start(80)
        else:
            self._timer.stop()
            self._bars = [0.2] * 20
            self.update()

    def _animate(self):
        self._tick += 1
        for i in range(len(self._bars)):
            self._bars[i] = max(0.1, min(1.0,
                abs(math.sin(self._tick * 0.18 + i * 0.5)) * 0.8 + 0.15
            ))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar_w = 4
        gap = 2
        total_w = len(self._bars) * (bar_w + gap) - gap
        x_start = (self.width() - total_w) // 2
        center_y = self.height() // 2

        for i, h_ratio in enumerate(self._bars):
            bar_h = max(2, int(h_ratio * (self.height() - 6)))
            x = x_start + i * (bar_w + gap)
            y = center_y - bar_h // 2
            alpha = int(200 * h_ratio + 55)
            color = QColor(COLORS["cyan"])
            color.setAlpha(alpha if self._active else 80)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, y, bar_w, bar_h, 2, 2)


class BottomPlayerBar(QFrame):
    """Fixed bottom player bar — Figma design."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transparent")
        self.setFixedHeight(64)
        self.setStyleSheet(
            f"background-color: {COLORS['bg_darkest']};"
            f"border-top: 1px solid {COLORS['border']};"
        )

        self._player = AudioPlayer.get_instance()
        self._player.playback_changed.connect(self._on_playback_changed)
        self._current_file: str = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 0, 20, 0)
        root.setSpacing(16)

        # ── Track info (left) ──────────────────────────────────────
        info_widget = QWidget()
        info_widget.setStyleSheet("background: transparent;")
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        self.lbl_track = QLabel("Sin reproducción")
        self.lbl_track.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_track.setStyleSheet(f"color: {COLORS['text_main']};")

        self.lbl_artist = QLabel("—")
        self.lbl_artist.setObjectName("muted")
        self.lbl_artist.setFont(QFont("Segoe UI", 8))

        info_layout.addWidget(self.lbl_track)
        info_layout.addWidget(self.lbl_artist)

        root.addWidget(info_widget, stretch=2)

        # ── Playback controls (center) ─────────────────────────────
        ctrl_widget = QWidget()
        ctrl_widget.setStyleSheet("background: transparent;")
        ctrl_layout = QHBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(8)
        ctrl_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_play = QPushButton()
        self.btn_play.setObjectName("ghost")
        self.btn_play.setFixedSize(36, 36)
        self.btn_play.setIcon(qta.icon("fa5s.play", color=COLORS["cyan"]))
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setToolTip("Reproducir / Pausar")
        self.btn_play.clicked.connect(self._toggle_play)

        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName("ghost")
        self.btn_stop.setFixedSize(30, 30)
        self.btn_stop.setIcon(qta.icon("fa5s.stop", color=COLORS["text_muted"]))
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setToolTip("Detener")
        self.btn_stop.clicked.connect(self._player.stop)

        ctrl_layout.addWidget(self.btn_stop)
        ctrl_layout.addWidget(self.btn_play)

        root.addWidget(ctrl_widget, stretch=1)

        # ── Waveform (right) ───────────────────────────────────────
        right_widget = QWidget()
        right_widget.setStyleSheet("background: transparent;")
        right_layout = QHBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.waveform = MiniWaveform()
        right_layout.addWidget(self.waveform)

        # Status dot
        self.lbl_status = QLabel("●  INACTIVO")
        self.lbl_status.setFont(QFont("JetBrains Mono", 7))
        self.lbl_status.setStyleSheet(f"color: {COLORS['text_dim']};")
        right_layout.addWidget(self.lbl_status)

        root.addWidget(right_widget, stretch=2)

    def _on_playback_changed(self, filepath: str, is_playing: bool):
        if is_playing:
            import os
            self._current_file = filepath
            name = os.path.basename(filepath)
            # Try to extract artist - track from filename
            if " - " in name:
                parts = name.rsplit(" - ", 1)
                self.lbl_artist.setText(parts[0])
                self.lbl_track.setText(parts[1].rsplit(".", 1)[0])
            else:
                self.lbl_track.setText(name.rsplit(".", 1)[0])
                self.lbl_artist.setText("—")

            self.btn_play.setIcon(qta.icon("fa5s.pause", color=COLORS["cyan"]))
            self.lbl_status.setText("●  REPRODUCIENDO")
            self.lbl_status.setStyleSheet(f"color: {COLORS['cyan']};")
            self.waveform.set_active(True)
        else:
            self.btn_play.setIcon(qta.icon("fa5s.play", color=COLORS["cyan"]))
            self.lbl_status.setText("●  INACTIVO")
            self.lbl_status.setStyleSheet(f"color: {COLORS['text_dim']};")
            self.waveform.set_active(False)

    def _toggle_play(self):
        if self._player.is_playing and self._current_file:
            self._player.pause()
        elif self._current_file:
            self._player.unpause()

    def play_file(self, filepath: str):
        """Called from outside to start playback of a track."""
        self._player.play(filepath)
