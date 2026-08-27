"""
Scanner View component — Dedicated interactive dashboard for audio library scanning.
"""

import os
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
import qtawesome as qta

from core.models import ScanStats
from gui.styles import COLORS


class ScanMetricCard(QFrame):
    def __init__(self, title: str, icon_name: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon(icon_name, color=color).pixmap(15, 15))
        top.addWidget(icon)

        t_lbl = QLabel(title.upper())
        t_lbl.setObjectName("section_label")
        top.addWidget(t_lbl)
        top.addStretch()
        layout.addLayout(top)

        self.val_lbl = QLabel("—")
        self.val_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        self.val_lbl.setStyleSheet(f"color: {color};")
        layout.addWidget(self.val_lbl)

    def set_value(self, text: str):
        self.val_lbl.setText(text)


class ScannerView(QWidget):
    start_scan_requested = pyqtSignal()
    pause_scan_requested = pyqtSignal()
    resume_scan_requested = pyqtSignal()
    cancel_scan_requested = pyqtSignal()
    folder_requested = pyqtSignal()
    view_duplicates_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_scanning = False
        self.is_paused = False
        self.current_folder = ""

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # ── Header ─────────────────────────────────────────────────
        title_block = QVBoxLayout()
        title_block.setSpacing(2)

        lbl_header = QLabel("PANEL DE ESCANEO DE AUDIO")
        lbl_header.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        lbl_header.setStyleSheet(f"color: {COLORS['text_main']};")
        title_block.addWidget(lbl_header)

        self.lbl_folder = QLabel("Carpeta activa: Sin seleccionar")
        self.lbl_folder.setObjectName("muted")
        title_block.addWidget(self.lbl_folder)

        root.addLayout(title_block)

        # ── Idle State Container ───────────────────────────────────
        self.idle_frame = QFrame()
        self.idle_frame.setObjectName("card")
        idle_layout = QVBoxLayout(self.idle_frame)
        idle_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_layout.setContentsMargins(40, 60, 40, 60)
        idle_layout.setSpacing(18)

        icon_idle = QLabel()
        icon_idle.setPixmap(qta.icon("fa5s.search", color=COLORS["cyan"]).pixmap(64, 64))
        icon_idle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_layout.addWidget(icon_idle)

        lbl_idle_t = QLabel("Preparado para Analizar Biblioteca")
        lbl_idle_t.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        lbl_idle_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_layout.addWidget(lbl_idle_t)

        lbl_idle_sub = QLabel(
            "El motor generará huellas acústicas Chromaprint, hashes PCM y espectrogramas\n"
            "para detectar duplicados exactos, remasterizaciones y pistas transcodificadas."
        )
        lbl_idle_sub.setObjectName("muted")
        lbl_idle_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_layout.addWidget(lbl_idle_sub)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_idle_folder = QPushButton(" Cambiar Carpeta")
        self.btn_idle_folder.setObjectName("ghost")
        self.btn_idle_folder.setIcon(qta.icon("fa5s.folder-open", color=COLORS["text_muted"]))
        self.btn_idle_folder.setFixedSize(160, 40)
        self.btn_idle_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_idle_folder.clicked.connect(self.folder_requested.emit)
        btn_row.addWidget(self.btn_idle_folder)

        self.btn_idle_start = QPushButton(" Iniciar Escaneo")
        self.btn_idle_start.setObjectName("primary")
        self.btn_idle_start.setIcon(qta.icon("fa5s.play", color="#000000"))
        self.btn_idle_start.setFixedSize(180, 40)
        self.btn_idle_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_idle_start.clicked.connect(self.start_scan_requested.emit)
        btn_row.addWidget(self.btn_idle_start)

        idle_layout.addLayout(btn_row)
        root.addWidget(self.idle_frame, stretch=1)

        # ── Active Scan Container ──────────────────────────────────
        self.active_frame = QFrame()
        self.active_frame.setObjectName("transparent")
        active_layout = QVBoxLayout(self.active_frame)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setSpacing(14)

        # Progress Card
        prog_card = QFrame()
        prog_card.setObjectName("card")
        prog_card_layout = QVBoxLayout(prog_card)
        prog_card_layout.setContentsMargins(20, 16, 20, 16)
        prog_card_layout.setSpacing(12)

        # Phase status & action buttons
        phase_row = QHBoxLayout()
        self.lbl_phase = QLabel("FASE: Analizando archivos de audio...")
        self.lbl_phase.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.lbl_phase.setStyleSheet(f"color: {COLORS['cyan']};")
        phase_row.addWidget(self.lbl_phase, stretch=1)

        self.btn_pause = QPushButton(" Pausar")
        self.btn_pause.setObjectName("ghost")
        self.btn_pause.setIcon(qta.icon("fa5s.pause", color=COLORS["text_muted"]))
        self.btn_pause.setFixedSize(110, 32)
        self.btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause.clicked.connect(self._toggle_pause)
        phase_row.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton(" Cancelar")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setIcon(qta.icon("fa5s.times", color="white"))
        self.btn_cancel.setFixedSize(110, 32)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancel_scan_requested.emit)
        phase_row.addWidget(self.btn_cancel)

        prog_card_layout.addLayout(phase_row)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        prog_card_layout.addWidget(self.progress_bar)

        # Progress labels
        pct_row = QHBoxLayout()
        self.lbl_pct = QLabel("0% completado")
        self.lbl_pct.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        
        self.lbl_eta = QLabel("Velocidad: —  ·  Tiempo restante: —")
        self.lbl_eta.setObjectName("dim")
        self.lbl_eta.setAlignment(Qt.AlignmentFlag.AlignRight)

        pct_row.addWidget(self.lbl_pct)
        pct_row.addStretch()
        pct_row.addWidget(self.lbl_eta)
        prog_card_layout.addLayout(pct_row)

        # Current file
        self.lbl_current_file = QLabel("Iniciando motor de audio...")
        self.lbl_current_file.setObjectName("mono")
        self.lbl_current_file.setWordWrap(True)
        prog_card_layout.addWidget(self.lbl_current_file)

        active_layout.addWidget(prog_card)

        # Metrics Row
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(10)

        self.card_files = ScanMetricCard("Archivos Procesados", "fa5s.music", COLORS["text_main"])
        self.card_cache = ScanMetricCard("Caché Hits", "fa5s.bolt", COLORS["cyan"])
        self.card_exact = ScanMetricCard("Duplicados Exactos", "fa5s.copy", COLORS["success"])
        self.card_acoustic = ScanMetricCard("Duplicados Acústicos", "fa5s.wave-square", COLORS["info"])

        metrics_row.addWidget(self.card_files)
        metrics_row.addWidget(self.card_cache)
        metrics_row.addWidget(self.card_exact)
        metrics_row.addWidget(self.card_acoustic)
        active_layout.addLayout(metrics_row)

        # Finished banner (hidden until complete)
        self.finish_card = QFrame()
        self.finish_card.setObjectName("card")
        self.finish_card.setStyleSheet(f"background-color: {COLORS['cyan_bg']}; border: 1px solid {COLORS['cyan_dim']};")
        f_layout = QHBoxLayout(self.finish_card)
        f_layout.setContentsMargins(18, 14, 18, 14)

        f_info = QVBoxLayout()
        self.lbl_finish_title = QLabel("¡Escaneo completado!")
        self.lbl_finish_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.lbl_finish_title.setStyleSheet(f"color: {COLORS['cyan']};")
        f_info.addWidget(self.lbl_finish_title)

        self.lbl_finish_sub = QLabel("Se han procesado todos los archivos de la biblioteca.")
        self.lbl_finish_sub.setObjectName("dim")
        f_info.addWidget(self.lbl_finish_sub)
        f_layout.addLayout(f_info, stretch=1)

        btn_view_dups = QPushButton(" Ver Resultados de Duplicados ")
        btn_view_dups.setObjectName("primary")
        btn_view_dups.setIcon(qta.icon("fa5s.arrow-right", color="#000000"))
        btn_view_dups.setFixedHeight(36)
        btn_view_dups.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_view_dups.clicked.connect(self.view_duplicates_requested.emit)
        f_layout.addWidget(btn_view_dups)

        self.finish_card.hide()
        active_layout.addWidget(self.finish_card)

        active_layout.addStretch()
        self.active_frame.hide()
        root.addWidget(self.active_frame, stretch=1)

    def set_folder(self, folder: str):
        self.current_folder = folder
        if folder:
            self.lbl_folder.setText(f"Carpeta activa: {folder}")
        else:
            self.lbl_folder.setText("Carpeta activa: Sin seleccionar")

    def start_scanning_ui(self):
        self.is_scanning = True
        self.is_paused = False
        self.idle_frame.hide()
        self.finish_card.hide()
        self.active_frame.show()
        self.btn_pause.setText(" Pausar")
        self.btn_pause.setIcon(qta.icon("fa5s.pause", color=COLORS["text_muted"]))
        self.lbl_phase.setText("FASE: Analizando archivos de audio...")
        self.progress_bar.setValue(0)

    def update_stats(self, stats: ScanStats):
        self.card_files.set_value(f"{stats.files_processed:,} / {stats.total_files_found:,}")
        self.card_cache.set_value(f"{stats.files_from_cache:,}")
        self.card_exact.set_value(f"{stats.exact_hash_groups_count:,}")
        self.card_acoustic.set_value(f"{stats.acoustic_duplicate_groups_count:,}")

        pct = int(stats.progress_percentage)
        self.progress_bar.setValue(pct)

        if stats.comparison_total > 0 and stats.progress_ratio is not None:
            self.lbl_pct.setText(f"{pct}% de comparación acústica ({stats.comparison_current:,} de {stats.comparison_total:,} pares)")
        elif "Descubriendo" in stats.phase or "Indexando" in stats.phase or "Cargando" in stats.phase or "Agrupando" in stats.phase:
            self.lbl_pct.setText(f"{stats.phase}")
        else:
            self.lbl_pct.setText(f"{pct}% completado ({stats.files_processed:,} de {stats.total_files_found:,} archivos)")

        phase_names = {
            "DISCOVERING": "Descubriendo archivos en disco...",
            "READING_CACHE": "Consultando base de datos SQLite...",
            "EXTRACTING_METADATA": "Extrayendo etiquetas y metadatos...",
            "FINGERPRINTING": "Generando huellas acústicas y FFT...",
            "CLUSTERING": "Agrupando y comparando duplicados...",
            "EVALUATING_QUALITY": "Evaluando calidad de audio...",
            "COMPLETED": "Escaneo finalizado",
            "CANCELLED": "Escaneo cancelado"
        }
        p_text = phase_names.get(stats.phase, stats.phase)
        self.lbl_phase.setText(f"FASE: {p_text}")

        if stats.current_file:
            if "Descubriendo" in stats.phase:
                self.lbl_current_file.setText(f"Explorando carpeta: {stats.current_file}")
            elif "Comparando" in stats.phase or "Agrupando" in stats.phase or "Indexando" in stats.phase:
                self.lbl_current_file.setText(f"Procesando: {stats.current_file}")
            else:
                self.lbl_current_file.setText(f"Archivo actual: {os.path.basename(stats.current_file)}")

        if "Descubriendo" in stats.phase:
            eta_text = f"Descubrimiento: {stats.throughput_fps:,.0f} arch/s"
        else:
            eta_text = f"Velocidad: {stats.throughput_fps:,.1f} arch/s"
        if stats.eta_seconds > 0:
            m = int(stats.eta_seconds // 60)
            s = int(stats.eta_seconds % 60)
            eta_text += f"  ·  ETA: {m:02d}:{s:02d}"
        self.lbl_eta.setText(eta_text)


    def finish_scanning_ui(self, groups_count: int):
        self.is_scanning = False
        self.progress_bar.setValue(100)
        self.lbl_pct.setText(f"100% completado · {groups_count} grupos de duplicados detectados")
        self.lbl_phase.setText("FASE: Escaneo completado")
        self.lbl_current_file.setText(f"Análisis finalizado. Se encontraron {groups_count} grupos de duplicados.")
        self.finish_card.show()


    def _toggle_pause(self):
        if not self.is_paused:
            self.is_paused = True
            self.btn_pause.setText(" Reanudar")
            self.btn_pause.setIcon(qta.icon("fa5s.play", color=COLORS["cyan"]))
            self.lbl_phase.setText("FASE: En pausa")
            self.pause_scan_requested.emit()
        else:
            self.is_paused = False
            self.btn_pause.setText(" Pausar")
            self.btn_pause.setIcon(qta.icon("fa5s.pause", color=COLORS["text_muted"]))
            self.lbl_phase.setText("FASE: Reanudando...")
            self.resume_scan_requested.emit()
