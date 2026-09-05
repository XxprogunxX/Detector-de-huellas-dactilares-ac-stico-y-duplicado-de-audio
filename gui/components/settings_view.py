"""
Settings & Preferences View component — Engine configuration, thresholds and database maintenance.
"""

import os
from typing import Optional, Callable
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QSpinBox, QSlider, QComboBox, QFrame,
    QScrollArea, QMessageBox, QFileDialog, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
import qtawesome as qta

from core.database import Database
from gui.styles import COLORS


class SettingsView(QWidget):
    settings_saved = pyqtSignal(dict)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db

        self._build_ui()
        self.refresh_db_stats()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # Title
        title_block = QVBoxLayout()
        title_block.setSpacing(2)

        lbl_header = QLabel("CONFIGURACIÓN Y PREFERENCIAS")
        lbl_header.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        lbl_header.setStyleSheet(f"color: {COLORS['text_main']};")
        title_block.addWidget(lbl_header)

        lbl_sub = QLabel("Ajusta los algoritmos de detección, hilos de escaneo y gestiona la base de datos de huellas.")
        lbl_sub.setObjectName("muted")
        title_block.addWidget(lbl_sub)
        root.addLayout(title_block)

        # Scroll area for settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("transparent")

        container = QWidget()
        container.setObjectName("transparent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)

        # ── Card 1: Scan & Detection Engine ────────────────────────
        card_scan = QFrame()
        card_scan.setObjectName("card")
        c1_layout = QVBoxLayout(card_scan)
        c1_layout.setContentsMargins(18, 16, 18, 16)
        c1_layout.setSpacing(14)

        c1_title = QLabel("MOTOR DE ESCANEO Y DETECCIÓN ACÚSTICA")
        c1_title.setObjectName("section_label")
        c1_layout.addWidget(c1_title)

        grid1 = QGridLayout()
        grid1.setVerticalSpacing(12)
        grid1.setHorizontalSpacing(16)

        # Formats
        lbl_formats = QLabel("Formatos a analizar:")
        lbl_formats.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        grid1.addWidget(lbl_formats, 0, 0, Qt.AlignmentFlag.AlignTop)

        formats_box = QWidget()
        f_layout = QHBoxLayout(formats_box)
        f_layout.setContentsMargins(0, 0, 0, 0)
        f_layout.setSpacing(12)

        self.chk_mp3 = QCheckBox("MP3")
        self.chk_mp3.setChecked(True)
        self.chk_flac = QCheckBox("FLAC")
        self.chk_flac.setChecked(True)
        self.chk_wav = QCheckBox("WAV")
        self.chk_wav.setChecked(True)
        self.chk_m4a = QCheckBox("M4A / AAC")
        self.chk_m4a.setChecked(True)
        self.chk_ogg = QCheckBox("OGG / OPUS")
        self.chk_ogg.setChecked(True)
        self.chk_wma = QCheckBox("WMA")
        self.chk_wma.setChecked(True)

        f_layout.addWidget(self.chk_mp3)
        f_layout.addWidget(self.chk_flac)
        f_layout.addWidget(self.chk_wav)
        f_layout.addWidget(self.chk_m4a)
        f_layout.addWidget(self.chk_ogg)
        f_layout.addWidget(self.chk_wma)
        f_layout.addStretch()
        grid1.addWidget(formats_box, 0, 1)

        # Similarity threshold slider
        lbl_sim = QLabel("Umbral de Similitud Acústica:")
        lbl_sim.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        grid1.addWidget(lbl_sim, 1, 0)

        sim_box = QHBoxLayout()
        self.slider_sim = QSlider(Qt.Orientation.Horizontal)
        self.slider_sim.setRange(70, 98)
        self.slider_sim.setValue(85)
        self.slider_sim.setFixedWidth(240)

        self.lbl_sim_val = QLabel("85%")
        self.lbl_sim_val.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_sim_val.setStyleSheet(f"color: {COLORS['cyan']};")
        self.lbl_sim_val.setFixedWidth(40)

        self.slider_sim.valueChanged.connect(lambda v: self.lbl_sim_val.setText(f"{v}%"))
        sim_box.addWidget(self.slider_sim)
        sim_box.addWidget(self.lbl_sim_val)
        sim_box.addStretch()
        grid1.addLayout(sim_box, 1, 1)

        # Min duration
        lbl_dur = QLabel("Duración mínima de pista:")
        lbl_dur.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        grid1.addWidget(lbl_dur, 2, 0)

        dur_box = QHBoxLayout()
        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(0, 120)
        self.spin_duration.setValue(5)
        self.spin_duration.setSuffix(" seg")
        self.spin_duration.setFixedWidth(100)
        dur_box.addWidget(self.spin_duration)
        
        lbl_dur_hint = QLabel("(Ignora jingles o efectos de sonido ultra cortos)")
        lbl_dur_hint.setObjectName("dim")
        dur_box.addWidget(lbl_dur_hint)
        dur_box.addStretch()
        grid1.addLayout(dur_box, 2, 1)

        # Workers / CPU threads
        lbl_threads = QLabel("Hilos de procesamiento:")
        lbl_threads.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        grid1.addWidget(lbl_threads, 3, 0)

        threads_box = QHBoxLayout()
        self.spin_threads = QSpinBox()
        import multiprocessing
        max_cpu = multiprocessing.cpu_count()
        self.spin_threads.setRange(1, max(1, max_cpu * 2))
        self.spin_threads.setValue(min(4, max_cpu))
        self.spin_threads.setFixedWidth(100)
        threads_box.addWidget(self.spin_threads)
        
        lbl_threads_hint = QLabel(f"(CPU detectado: {max_cpu} núcleos)")
        lbl_threads_hint.setObjectName("dim")
        threads_box.addWidget(lbl_threads_hint)
        threads_box.addStretch()
        grid1.addLayout(threads_box, 3, 1)

        # Deep spectral analysis
        lbl_spectral = QLabel("Análisis espectral FFT:")
        lbl_spectral.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        grid1.addWidget(lbl_spectral, 4, 0)

        self.chk_spectral = QCheckBox("Activar detección de Fake Lossless / Transcodificaciones (FFT espectral)")
        self.chk_spectral.setChecked(True)
        grid1.addWidget(self.chk_spectral, 4, 1)

        # Load persisted detection settings
        try:
            from core.config import load_detection_config
            init_cfg = load_detection_config()
            self.slider_sim.setValue(int(init_cfg.possible_threshold))
            self.lbl_sim_val.setText(f"{int(init_cfg.possible_threshold)}%")
            self.spin_duration.setValue(int(init_cfg.min_duration))
            if init_cfg.max_workers:
                self.spin_threads.setValue(init_cfg.max_workers)
            self.chk_spectral.setChecked(init_cfg.spectral_analysis)
        except Exception:
            pass

        c1_layout.addLayout(grid1)
        layout.addWidget(card_scan)

        # ── Card 2: Database & Storage ─────────────────────────────
        card_db = QFrame()
        card_db.setObjectName("card")
        c2_layout = QVBoxLayout(card_db)
        c2_layout.setContentsMargins(18, 16, 18, 16)
        c2_layout.setSpacing(14)

        c2_title = QLabel("BASE DE DATOS Y CACHÉ DE HUELLAS (SQLITE)")
        c2_title.setObjectName("section_label")
        c2_layout.addWidget(c2_title)

        grid2 = QGridLayout()
        grid2.setVerticalSpacing(10)
        grid2.setHorizontalSpacing(16)

        # DB location
        grid2.addWidget(QLabel("Ubicación de archivo:"), 0, 0)
        self.lbl_db_path = QLabel(self.db.db_path)
        self.lbl_db_path.setObjectName("dim")
        self.lbl_db_path.setWordWrap(True)
        grid2.addWidget(self.lbl_db_path, 0, 1)

        # DB stats
        grid2.addWidget(QLabel("Estado de caché:"), 1, 0)
        self.lbl_db_stats = QLabel("Calculando...")
        self.lbl_db_stats.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_db_stats.setStyleSheet(f"color: {COLORS['cyan']};")
        grid2.addWidget(self.lbl_db_stats, 1, 1)

        c2_layout.addLayout(grid2)

        # Maintenance actions
        btn_db_row = QHBoxLayout()
        btn_db_row.setSpacing(10)

        btn_vacuum = QPushButton(" Optimizar Base de Datos (VACUUM)")
        btn_vacuum.setObjectName("ghost")
        btn_vacuum.setIcon(qta.icon("fa5s.compress-arrows-alt", color=COLORS["text_muted"]))
        btn_vacuum.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_vacuum.clicked.connect(self._handle_vacuum)
        btn_db_row.addWidget(btn_vacuum)

        btn_clear = QPushButton(" Limpiar Caché Completo")
        btn_clear.setObjectName("danger")
        btn_clear.setIcon(qta.icon("fa5s.trash-alt", color="white"))
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.clicked.connect(self._handle_clear_db)
        btn_db_row.addWidget(btn_clear)

        btn_db_row.addStretch()
        c2_layout.addLayout(btn_db_row)

        layout.addWidget(card_db)

        # ── Card 3: Auto Selection Policy ──────────────────────────
        card_policy = QFrame()
        card_policy.setObjectName("card")
        c3_layout = QVBoxLayout(card_policy)
        c3_layout.setContentsMargins(18, 16, 18, 16)
        c3_layout.setSpacing(14)

        c3_title = QLabel("POLÍTICA DE AUTO-SELECCIÓN DE DUPLICADOS")
        c3_title.setObjectName("section_label")
        c3_layout.addWidget(c3_title)

        grid3 = QGridLayout()
        grid3.setVerticalSpacing(10)
        grid3.setHorizontalSpacing(16)

        grid3.addWidget(QLabel("Criterio preferente al conservar:"), 0, 0)
        self.combo_policy = QComboBox()
        self.combo_policy.addItems([
            "Mayor Calidad de Audio (Lossless Auténtico > Bitrate Alto)",
            "Mayor Tasa de Bits (Bitrate kbps)",
            "Mayor Frecuencia de Muestreo (Hi-Res)",
            "Archivo Más Reciente en Disco"
        ])
        self.combo_policy.setFixedHeight(32)
        grid3.addWidget(self.combo_policy, 0, 1)

        c3_layout.addLayout(grid3)
        layout.addWidget(card_policy)

        layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

        # ── Bottom Actions ─────────────────────────────────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        btn_reset = QPushButton(" Restablecer Valores por Defecto")
        btn_reset.setObjectName("ghost")
        btn_reset.clicked.connect(self._reset_defaults)
        bottom_row.addWidget(btn_reset)

        bottom_row.addStretch()

        btn_save = QPushButton(" Guardar Configuración")
        btn_save.setObjectName("primary")
        btn_save.setIcon(qta.icon("fa5s.save", color="#000000"))
        btn_save.setFixedSize(180, 36)
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(self._save_settings)
        bottom_row.addWidget(btn_save)

        root.addLayout(bottom_row)

    def refresh_db_stats(self):
        try:
            tracks_cnt = self.db.get_total_tracks_count()
            size_bytes = self.db.get_database_size_bytes()
            size_mb = size_bytes / (1024 * 1024)
            self.lbl_db_stats.setText(f"{tracks_cnt:,} pistas indexadas  ·  {size_mb:.2f} MB en disco")
        except Exception:
            self.lbl_db_stats.setText("No disponible")

    def _handle_vacuum(self):
        try:
            self.db.vacuum_database()
            self.refresh_db_stats()
            QMessageBox.information(self, "Optimización Completada", "La base de datos ha sido compactada y optimizada con éxito.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo optimizar la base de datos: {e}")

    def _handle_clear_db(self):
        reply = QMessageBox.question(
            self, "Confirmar Limpieza",
            "¿Estás seguro de que deseas eliminar todas las huellas y caché de la base de datos?\n"
            "El próximo escaneo requerirá reanalizar todos los archivos.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.db.clear_database()
                self.refresh_db_stats()
                QMessageBox.information(self, "Caché Limpiada", "Se han eliminado todos los datos en caché de la base de datos.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error al limpiar: {e}")

    def _reset_defaults(self):
        from core.config import DetectionConfig
        cfg = DetectionConfig()
        self.slider_sim.setValue(int(cfg.possible_threshold))
        self.lbl_sim_val.setText(f"{int(cfg.possible_threshold)}%")
        self.spin_duration.setValue(int(cfg.min_duration))
        self.chk_mp3.setChecked(True)
        self.chk_flac.setChecked(True)
        self.chk_wav.setChecked(True)
        self.chk_m4a.setChecked(True)
        self.chk_ogg.setChecked(True)
        self.chk_wma.setChecked(True)
        self.chk_spectral.setChecked(cfg.spectral_analysis)
        self.combo_policy.setCurrentIndex(0)
        QMessageBox.information(self, "Restablecido", "Se han restablecido los valores por defecto.")

    def _save_settings(self):
        config = {
            "similarity_threshold": self.slider_sim.value() / 100.0,
            "min_duration": self.spin_duration.value(),
            "threads": self.spin_threads.value(),
            "spectral_fft": self.chk_spectral.isChecked(),
            "policy": self.combo_policy.currentText()
        }
        self.settings_saved.emit(config)
        QMessageBox.information(self, "Guardado", "La configuración ha sido guardada correctamente.")
