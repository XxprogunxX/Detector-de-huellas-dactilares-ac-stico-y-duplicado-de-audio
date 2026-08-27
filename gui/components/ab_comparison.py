"""
A/B Comparison View — Side-by-side spectral analysis panel (Figma Phase B).

Shows two tracks with:
  - Spectral cutoff visualization (colored bar chart by frequency band)
  - Technical specs table (format, bitrate, samplerate, bit_depth, quality score)
  - Playback controls per track
  - Fake lossless warning with cutoff frequency annotation
"""

import os
import math
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import (
    QFont, QPainter, QColor, QLinearGradient, QPen, QBrush
)
import qtawesome as qta

from gui.styles import COLORS
from core.models import AudioTrack
from gui.components.audio_player import AudioPlayer


# ─── Spectral Bar Chart widget ──────────────────────────────────────────────

class SpectralBarsWidget(QWidget):
    """
    Renders a simulated spectral view using the spectral_cutoff value.
    Draws frequency bands from 20Hz to 22kHz in 20 bars.
    Bars above the cutoff are grayed out (truncated by lossy encoding).
    """
    BANDS_HZ = [
        20, 50, 100, 200, 400, 800, 1200, 1600, 2000, 2500,
        3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 18000, 20000
    ]

    def __init__(
        self,
        cutoff_hz: float,
        is_fake: bool = False,
        label: str = "",
        parent=None
    ):
        super().__init__(parent)
        self.cutoff_hz = cutoff_hz
        self.is_fake = is_fake
        self.label = label
        self._anim_tick = 0
        self._heights: list[float] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._generate_heights()

    def _generate_heights(self):
        """Generate naturalistic-looking height profile."""
        import random
        random.seed(int(self.cutoff_hz) % 9999)
        self._heights = []
        for hz in self.BANDS_HZ:
            if hz <= self.cutoff_hz:
                # Active band: amplitude decreasing slightly toward high freq
                ratio = 0.6 + 0.4 * (1.0 - hz / max(self.cutoff_hz, 1))
                h = max(0.25, ratio + random.uniform(-0.12, 0.12))
            else:
                # Truncated band: nearly flat noise floor
                h = random.uniform(0.02, 0.08)
            self._heights.append(min(1.0, h))

    def _tick(self):
        self._anim_tick += 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height() - 22   # Leave room for Hz labels
        n = len(self.BANDS_HZ)
        bar_w = max(4, (w - (n + 1) * 2) // n)
        gap = 2
        x_start = (w - n * (bar_w + gap) + gap) // 2

        for i, hz in enumerate(self.BANDS_HZ):
            bar_h_ratio = self._heights[i]
            # Subtle animation: only on active bands
            if hz <= self.cutoff_hz:
                wobble = math.sin(self._anim_tick * 0.08 + i * 0.4) * 0.04
                bar_h_ratio = min(1.0, max(0.05, bar_h_ratio + wobble))

            bar_h = int(bar_h_ratio * h)
            x = x_start + i * (bar_w + gap)
            y = h - bar_h

            if hz <= self.cutoff_hz:
                # Active: gradient from cyan → blue
                if self.is_fake:
                    color_top = QColor(COLORS["warning"])
                    color_bot = QColor(COLORS["warning"])
                    color_bot.setAlpha(120)
                else:
                    color_top = QColor(COLORS["cyan"])
                    color_bot = QColor(COLORS["info"])
                    color_bot.setAlpha(140)

                grad = QLinearGradient(0, y, 0, y + bar_h)
                grad.setColorAt(0, color_top)
                grad.setColorAt(1, color_bot)
                painter.setBrush(QBrush(grad))
            else:
                # Truncated: dim gray
                color = QColor(COLORS["border"])
                color.setAlpha(90)
                painter.setBrush(QBrush(color))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, y, bar_w, bar_h, 2, 2)

        # Cutoff vertical line (dashed, red if fake)
        cutoff_x = self._hz_to_x(self.cutoff_hz, x_start, bar_w, gap, n)
        if 0 < cutoff_x < w:
            line_color = QColor(COLORS["danger"] if self.is_fake else COLORS["cyan"])
            line_color.setAlpha(180)
            pen = QPen(line_color, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(cutoff_x, 0, cutoff_x, h + 4)

            # Cutoff annotation
            painter.setPen(line_color)
            painter.setFont(QFont("JetBrains Mono", 7))
            khz = self.cutoff_hz / 1000
            tag = f"{khz:.1f}kHz"
            painter.drawText(cutoff_x + 3, 12, tag)

        # Frequency axis labels
        painter.setPen(QColor(COLORS["text_dim"]))
        painter.setFont(QFont("Segoe UI", 7))
        label_positions = {0: "20", 7: "1k", 13: "8k", 17: "16k", 19: "20k"}
        for idx, lbl in label_positions.items():
            x = x_start + idx * (bar_w + gap) + bar_w // 2
            painter.drawText(x - 8, h + 18, lbl)

    def _hz_to_x(self, hz, x_start, bar_w, gap, n):
        """Map a frequency to an approximate x coordinate."""
        for i, band in enumerate(self.BANDS_HZ):
            if band >= hz:
                return x_start + i * (bar_w + gap) + bar_w // 2
        return x_start + (n - 1) * (bar_w + gap) + bar_w // 2


# ─── Track Panel ──────────────────────────────────────────────────────────────

class TrackPanel(QFrame):
    """One side of the A/B comparison."""

    def __init__(self, track: AudioTrack, label: str, is_best: bool = False, parent=None):
        super().__init__(parent)
        self.track = track
        self._player = AudioPlayer.get_instance()
        self._player.playback_changed.connect(self._on_playback_changed)

        border = COLORS["cyan"] if is_best else COLORS["border"]
        self.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_card']};"
            f"border: 1px solid {border}; border-radius: 10px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Label badge ────────────────────────────────────────────
        top_row = QHBoxLayout()
        badge_color = COLORS["cyan"] if is_best else COLORS["text_muted"]
        badge = QLabel(f" {label} ")
        badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        badge.setStyleSheet(
            f"color: {badge_color}; border: 1px solid {badge_color};"
            f"border-radius: 4px; padding: 1px 6px; letter-spacing: 1px;"
        )
        top_row.addWidget(badge)

        if is_best:
            star = QLabel("  ★ MEJOR CALIDAD")
            star.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            star.setStyleSheet(f"color: {COLORS['success']}; border: none;")
            top_row.addWidget(star)

        top_row.addStretch()
        layout.addLayout(top_row)

        # ── Track title ────────────────────────────────────────────
        title = QLabel(track.display_title)
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {COLORS['text_main']}; border: none;")
        title.setWordWrap(True)
        layout.addWidget(title)

        path_lbl = QLabel(
            os.path.basename(os.path.dirname(track.filepath)) + "/" + track.filename
        )
        path_lbl.setFont(QFont("JetBrains Mono", 8))
        path_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        # ── Spectral bars ──────────────────────────────────────────
        is_fake = track.fake_lossless_confidence > 50.0
        panel_label = "FAKE LOSSLESS" if is_fake else ("LOSSLESS" if track.is_lossless else "LOSSY")
        cutoff = track.spectral_cutoff if track.spectral_cutoff > 0 else 20000.0

        spectral = SpectralBarsWidget(
            cutoff_hz=cutoff,
            is_fake=is_fake,
            label=panel_label,
        )
        layout.addWidget(spectral)

        # Fake warning badge
        if is_fake:
            warn_lbl = QLabel(
                f"⚠  Fake Lossless — Corte espectral a "
                f"{cutoff / 1000:.1f} kHz  ({track.fake_lossless_confidence:.0f}% confianza)"
            )
            warn_lbl.setFont(QFont("Segoe UI", 9))
            warn_lbl.setStyleSheet(
                f"color: {COLORS['warning']}; background: {COLORS['warning_bg']};"
                f"border: 1px solid {COLORS['warning']}; border-radius: 6px; padding: 6px 10px;"
            )
            warn_lbl.setWordWrap(True)
            layout.addWidget(warn_lbl)
        elif track.is_lossless:
            ok_lbl = QLabel("✓  Lossless auténtico — Señal completa hasta 20+ kHz")
            ok_lbl.setFont(QFont("Segoe UI", 9))
            ok_lbl.setStyleSheet(
                f"color: {COLORS['success']}; background: {COLORS['success_bg']};"
                f"border: 1px solid {COLORS['success']}; border-radius: 6px; padding: 6px 10px;"
            )
            layout.addWidget(ok_lbl)

        # ── Specs table ────────────────────────────────────────────
        specs_frame = QFrame()
        specs_frame.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_sidebar']}; border-radius: 6px; "
            f"border: 1px solid {COLORS['border']}; }}"
        )
        specs_layout = QVBoxLayout(specs_frame)
        specs_layout.setContentsMargins(10, 8, 10, 8)
        specs_layout.setSpacing(4)

        specs = [
            ("Formato",     track.format or "—"),
            ("Bitrate",     f"{track.bitrate} kbps" if track.bitrate else "—"),
            ("Sample rate", f"{track.samplerate} Hz" if track.samplerate else "—"),
            ("Bit depth",   f"{track.bit_depth}-bit" if track.bit_depth else "—"),
            ("Duración",    track.formatted_duration),
            ("Tamaño",      track.formatted_size),
            ("Calidad",     f"{track.quality_score:.0f} / 100" if track.quality_score else "—"),
        ]
        for key, val in specs:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            k = QLabel(key)
            k.setFont(QFont("Segoe UI", 8))
            k.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
            v = QLabel(val)
            v.setFont(QFont("JetBrains Mono", 8, QFont.Weight.Bold))
            v.setStyleSheet(f"color: {COLORS['text_main']}; border: none;")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(k)
            row.addStretch()
            row.addWidget(v)
            specs_layout.addLayout(row)

        layout.addWidget(specs_frame)

        # ── Play button ────────────────────────────────────────────
        self.btn_play = QPushButton("  Escuchar")
        self.btn_play.setObjectName("ghost")
        self.btn_play.setIcon(qta.icon("fa5s.play", color=COLORS["cyan"]))
        self.btn_play.setFixedHeight(36)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.clicked.connect(self._toggle_play)
        layout.addWidget(self.btn_play)

    def _toggle_play(self):
        self._player.play(self.track.filepath)

    def _on_playback_changed(self, filepath: str, is_playing: bool):
        if filepath == self.track.filepath:
            if is_playing:
                self.btn_play.setText("  Reproduciendo...")
                self.btn_play.setIcon(qta.icon("fa5s.volume-up", color=COLORS["cyan"]))
            else:
                self.btn_play.setText("  Escuchar")
                self.btn_play.setIcon(qta.icon("fa5s.play", color=COLORS["cyan"]))


# ─── AB Comparison Dialog ────────────────────────────────────────────────────

class ABComparisonDialog(QDialog):
    """
    Full-screen-ish dialog showing two tracks side-by-side with:
    - Spectral bar charts (animated, cyan vs. orange for fake)
    - Specs tables
    - Sync playback controls
    """

    def __init__(
        self,
        track_a: AudioTrack,
        track_b: AudioTrack,
        best_path: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent)
        self.setWindowTitle("Comparación A/B — Audio Cleaner")
        self.setMinimumSize(860, 660)
        self.resize(960, 700)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_main']}; }}")

        self._player = AudioPlayer.get_instance()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # ── Header ────────────────────────────────────────────────
        hdr = QHBoxLayout()

        title = QLabel("Comparación A/B")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {COLORS['text_main']};")
        hdr.addWidget(title)

        sub = QLabel(" — Análisis espectral comparado")
        sub.setFont(QFont("Segoe UI", 10))
        sub.setStyleSheet(f"color: {COLORS['text_muted']};")
        hdr.addWidget(sub)
        hdr.addStretch()

        btn_close = QPushButton(" Cerrar")
        btn_close.setObjectName("ghost")
        btn_close.setFixedHeight(34)
        btn_close.setIcon(qta.icon("fa5s.times", color=COLORS["text_muted"]))
        btn_close.clicked.connect(self.accept)
        hdr.addWidget(btn_close)

        layout.addLayout(hdr)

        # Thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            f"background-color: {COLORS['border']}; max-height: 1px; border: none;"
        )
        layout.addWidget(sep)

        # ── Legend ─────────────────────────────────────────────────
        legend_row = QHBoxLayout()
        for color, text in [
            (COLORS["cyan"],    "■  Espectro activo (lossless)"),
            (COLORS["warning"], "■  Espectro activo (fake lossless)"),
            (COLORS["border"],  "■  Banda truncada (por encima del corte)"),
        ]:
            lbl = QLabel(text)
            lbl.setFont(QFont("Segoe UI", 8))
            lbl.setStyleSheet(f"color: {color};")
            legend_row.addWidget(lbl)
        legend_row.addStretch()
        layout.addLayout(legend_row)

        # ── Panels ─────────────────────────────────────────────────
        panels_row = QHBoxLayout()
        panels_row.setSpacing(16)

        is_a_best = (best_path == track_a.filepath) if best_path else False
        is_b_best = (best_path == track_b.filepath) if best_path else False

        scroll_a = QScrollArea()
        scroll_a.setWidgetResizable(True)
        scroll_a.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel_a = TrackPanel(track_a, "PISTA A", is_best=is_a_best)
        scroll_a.setWidget(panel_a)
        panels_row.addWidget(scroll_a)

        scroll_b = QScrollArea()
        scroll_b.setWidgetResizable(True)
        scroll_b.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel_b = TrackPanel(track_b, "PISTA B", is_best=is_b_best)
        scroll_b.setWidget(panel_b)
        panels_row.addWidget(scroll_b)

        layout.addLayout(panels_row, stretch=1)

        # ── Bottom stop-all button ─────────────────────────────────
        btn_stop = QPushButton("  Detener reproducción")
        btn_stop.setObjectName("ghost")
        btn_stop.setFixedHeight(36)
        btn_stop.setIcon(qta.icon("fa5s.stop", color=COLORS["text_muted"]))
        btn_stop.clicked.connect(self._player.stop)
        layout.addWidget(btn_stop, alignment=Qt.AlignmentFlag.AlignRight)
