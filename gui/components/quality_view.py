"""
Quality Inspector View component — Audio quality auditing, spectral cutoff and fake lossless detection.
"""

import os
from typing import List, Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QAbstractItemView, QButtonGroup
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor
import qtawesome as qta

from core.models import AudioTrack
from core.database import Database
from core.file_manager import open_file_in_explorer
from gui.components.audio_player import AudioPlayer
from gui.styles import COLORS


class QualityStatCard(QFrame):
    def __init__(self, label: str, icon_name: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        top = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon(icon_name, color=color).pixmap(14, 14))
        top.addWidget(icon)

        title = QLabel(label.upper())
        title.setObjectName("section_label")
        top.addWidget(title)
        top.addStretch()
        layout.addLayout(top)

        self.value_lbl = QLabel("—")
        self.value_lbl.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.value_lbl.setStyleSheet(f"color: {color};")
        layout.addWidget(self.value_lbl)

    def set_value(self, text: str):
        self.value_lbl.setText(text)


class QualityView(QWidget):
    scan_requested = pyqtSignal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.player = AudioPlayer.get_instance()
        self.current_folder: str = ""
        self.tracks: List[AudioTrack] = []
        self.filtered_tracks: List[AudioTrack] = []
        self.active_filter = "all"
        
        self.current_page: int = 0
        self.page_size: int = 100

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── Title & Intro ──────────────────────────────────────────
        header_row = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(2)

        lbl_header = QLabel("AUDITORÍA DE CALIDAD Y TRANSCODIFICACIÓN")
        lbl_header.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        lbl_header.setStyleSheet(f"color: {COLORS['text_main']};")
        title_block.addWidget(lbl_header)

        lbl_sub = QLabel("Análisis espectral FFT de alta resolución para detectar pistas upscaled / fake lossless y baja fidelidad.")
        lbl_sub.setObjectName("muted")
        title_block.addWidget(lbl_sub)
        header_row.addLayout(title_block, stretch=1)

        root.addLayout(header_row)

        # ── KPI Cards Row ──────────────────────────────────────────
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)

        self.card_avg = QualityStatCard("Score Promedio", "fa5s.chart-line", COLORS["cyan"])
        self.card_lossless = QualityStatCard("Lossless Real", "fa5s.gem", COLORS["success"])
        self.card_fake = QualityStatCard("Fake Lossless", "fa5s.exclamation-triangle", COLORS["warning"])
        self.card_low = QualityStatCard("Baja Calidad (<192k)", "fa5s.arrow-down", COLORS["danger"])

        kpi_row.addWidget(self.card_avg)
        kpi_row.addWidget(self.card_lossless)
        kpi_row.addWidget(self.card_fake)
        kpi_row.addWidget(self.card_low)
        root.addLayout(kpi_row)

        # ── Filter Tabs & Search Row ───────────────────────────────
        filter_card = QFrame()
        filter_card.setObjectName("transparent")
        filter_layout = QHBoxLayout(filter_card)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(10)

        # Filter group
        self.btn_group = QButtonGroup(self)
        self.btn_all = QPushButton("Todos")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)

        self.btn_fake = QPushButton("⚠️ Transcodificaciones")
        self.btn_fake.setCheckable(True)

        self.btn_lossless = QPushButton("✓ Lossless Auténtico")
        self.btn_lossless.setCheckable(True)

        self.btn_low = QPushButton("⬇️ Baja Calidad")
        self.btn_low.setCheckable(True)

        self.btn_hires = QPushButton("✨ Hi-Res (>48kHz)")
        self.btn_hires.setCheckable(True)

        self.btn_group.addButton(self.btn_all, 0)
        self.btn_group.addButton(self.btn_fake, 1)
        self.btn_group.addButton(self.btn_lossless, 2)
        self.btn_group.addButton(self.btn_low, 3)
        self.btn_group.addButton(self.btn_hires, 4)
        self.btn_group.buttonClicked.connect(self._on_filter_changed)

        filter_layout.addWidget(self.btn_all)
        filter_layout.addWidget(self.btn_fake)
        filter_layout.addWidget(self.btn_lossless)
        filter_layout.addWidget(self.btn_low)
        filter_layout.addWidget(self.btn_hires)

        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filtrar por nombre, artista o formato...")
        self.search_input.setFixedHeight(34)
        
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._apply_filter)
        self.search_input.textChanged.connect(lambda: self.search_timer.start())
        filter_layout.addWidget(self.search_input, stretch=1)

        root.addWidget(filter_card)

        # ── Quality Table ──────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "►", "Título / Archivo", "Formato", "Bitrate Real", "Corte Espectral", "Diagnóstico Espectral", "Score", "Ubicación"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.doubleClicked.connect(self._on_row_double_clicked)

        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        h.resizeSection(0, 42)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        h.resizeSection(2, 85)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        h.resizeSection(3, 110)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        h.resizeSection(4, 120)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        h.resizeSection(5, 230)
        h.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        h.resizeSection(6, 85)
        h.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        h.resizeSection(7, 75)

        root.addWidget(self.table, stretch=1)

        # ── Pagination Bar ─────────────────────────────────────────
        pagination_bar = QFrame()
        pagination_bar.setObjectName("card")
        p_layout = QHBoxLayout(pagination_bar)
        p_layout.setContentsMargins(14, 8, 14, 8)
        p_layout.setSpacing(10)

        self.btn_first_page = QPushButton()
        self.btn_first_page.setIcon(qta.icon("fa5s.angle-double-left", color=COLORS["text_main"]))
        self.btn_first_page.setObjectName("ghost")
        self.btn_first_page.setFixedSize(32, 28)
        self.btn_first_page.setToolTip("Primera página")
        self.btn_first_page.clicked.connect(self._go_first_page)
        p_layout.addWidget(self.btn_first_page)

        self.btn_prev_page = QPushButton()
        self.btn_prev_page.setIcon(qta.icon("fa5s.angle-left", color=COLORS["text_main"]))
        self.btn_prev_page.setObjectName("ghost")
        self.btn_prev_page.setFixedSize(32, 28)
        self.btn_prev_page.setToolTip("Página anterior")
        self.btn_prev_page.clicked.connect(self._go_prev_page)
        p_layout.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Página 1 de 1 (0 pistas)")
        self.lbl_page_info.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_page_info.setStyleSheet(f"color: {COLORS['text_main']};")
        p_layout.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton()
        self.btn_next_page.setIcon(qta.icon("fa5s.angle-right", color=COLORS["text_main"]))
        self.btn_next_page.setObjectName("ghost")
        self.btn_next_page.setFixedSize(32, 28)
        self.btn_next_page.setToolTip("Página siguiente")
        self.btn_next_page.clicked.connect(self._go_next_page)
        p_layout.addWidget(self.btn_next_page)

        self.btn_last_page = QPushButton()
        self.btn_last_page.setIcon(qta.icon("fa5s.angle-double-right", color=COLORS["text_main"]))
        self.btn_last_page.setObjectName("ghost")
        self.btn_last_page.setFixedSize(32, 28)
        self.btn_last_page.setToolTip("Última página")
        self.btn_last_page.clicked.connect(self._go_last_page)
        p_layout.addWidget(self.btn_last_page)

        p_layout.addStretch()

        lbl_psize = QLabel("Mostrar:")
        lbl_psize.setObjectName("muted")
        p_layout.addWidget(lbl_psize)

        self.combo_page_size = QComboBox()
        self.combo_page_size.addItems(["50 pistas", "100 pistas", "250 pistas", "500 pistas"])
        self.combo_page_size.setCurrentIndex(1)  # Default 100
        self.combo_page_size.currentIndexChanged.connect(self._on_page_size_changed)
        self.combo_page_size.setFixedHeight(28)
        p_layout.addWidget(self.combo_page_size)

        root.addWidget(pagination_bar)

    def set_folder(self, folder: str):
        self.current_folder = folder
        self.reload_tracks()

    def reload_tracks(self, custom_tracks: Optional[List[AudioTrack]] = None):
        if custom_tracks is not None:
            self.tracks = custom_tracks
        elif self.current_folder:
            self.tracks = self.db.get_all_tracks(dir_prefix=self.current_folder, include_fingerprints=False)
        else:
            self.tracks = self.db.get_all_tracks(include_fingerprints=False)

        self._update_kpis()
        self.current_page = 0
        self._apply_filter()

    def _update_kpis(self):
        if not self.tracks:
            self.card_avg.set_value("—")
            self.card_lossless.set_value("—")
            self.card_fake.set_value("—")
            self.card_low.set_value("—")
            return

        scores = [t.quality_score for t in self.tracks if t.quality_score > 0]
        avg_score = sum(scores) / max(len(scores), 1)
        self.card_avg.set_value(f"{avg_score:.1f} / 100")

        lossless_count = sum(1 for t in self.tracks if t.is_lossless and t.fake_lossless_confidence <= 50.0)
        fake_count = sum(1 for t in self.tracks if t.fake_lossless_confidence > 50.0)
        low_count = sum(1 for t in self.tracks if not t.is_lossless and t.bitrate > 0 and t.bitrate < 192)

        self.card_lossless.set_value(f"{lossless_count:,}")
        self.card_fake.set_value(f"{fake_count:,}")
        self.card_low.set_value(f"{low_count:,}")

        self.btn_all.setText(f"Todos ({len(self.tracks):,})")
        self.btn_fake.setText(f"⚠️ Transcodificaciones ({fake_count:,})")
        self.btn_lossless.setText(f"✓ Lossless ({lossless_count:,})")
        self.btn_low.setText(f"⬇️ Baja Calidad ({low_count:,})")

    def _on_filter_changed(self, button):
        text = button.text()
        if "Transcodificaciones" in text:
            self.active_filter = "fake"
        elif "Lossless" in text:
            self.active_filter = "lossless"
        elif "Baja Calidad" in text:
            self.active_filter = "low"
        elif "Hi-Res" in text:
            self.active_filter = "hires"
        else:
            self.active_filter = "all"
        self.current_page = 0
        self._apply_filter()

    def _apply_filter(self):
        query = self.search_input.text().strip().lower()

        filtered = []
        for t in self.tracks:
            # Type filter
            if self.active_filter == "fake":
                if t.fake_lossless_confidence <= 50.0:
                    continue
            elif self.active_filter == "lossless":
                if not t.is_lossless or t.fake_lossless_confidence > 50.0:
                    continue
            elif self.active_filter == "low":
                if t.is_lossless or (t.bitrate >= 192 and t.bitrate > 0):
                    continue
            elif self.active_filter == "hires":
                if t.samplerate < 48000 and t.bit_depth <= 16:
                    continue

            # Query filter
            if query:
                match = (
                    query in t.filename.lower() or
                    query in t.title.lower() or
                    query in t.artist.lower() or
                    query in t.format.lower() or
                    query in t.quality_details.lower()
                )
                if not match:
                    continue

            filtered.append(t)

        # Sort: fake lossless and highest issues first
        if self.active_filter == "fake":
            filtered.sort(key=lambda x: x.fake_lossless_confidence, reverse=True)
        else:
            filtered.sort(key=lambda x: x.quality_score, reverse=False)

        self.filtered_tracks = filtered
        self._render_table()

    def _render_table(self):
        total_items = len(self.filtered_tracks)
        total_pages = max(1, (total_items + self.page_size - 1) // self.page_size)
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        start_idx = self.current_page * self.page_size
        end_idx = min(start_idx + self.page_size, total_items)
        page_tracks = self.filtered_tracks[start_idx:end_idx]

        self.table.setUpdatesEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(page_tracks))

        for row, track in enumerate(page_tracks):
            self.table.setRowHeight(row, 38)

            # 0. Play Button
            btn_play = QPushButton()
            btn_play.setIcon(qta.icon("fa5s.play", color=COLORS["cyan"]))
            btn_play.setFixedSize(28, 28)
            btn_play.setObjectName("ghost")
            btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_play.clicked.connect(lambda checked, p=track.filepath: self.player.play(p))
            
            play_container = QWidget()
            play_l = QHBoxLayout(play_container)
            play_l.setContentsMargins(0, 0, 0, 0)
            play_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            play_l.addWidget(btn_play)
            self.table.setCellWidget(row, 0, play_container)

            # 1. Title / File
            title_text = track.title if track.title else track.filename
            item_title = QTableWidgetItem(title_text)
            item_title.setToolTip(track.filepath)
            self.table.setItem(row, 1, item_title)

            # 2. Format
            item_fmt = QTableWidgetItem((track.format or "").upper())
            item_fmt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if track.is_lossless:
                item_fmt.setForeground(QColor(COLORS["success"]))
            self.table.setItem(row, 2, item_fmt)

            # 3. Bitrate
            item_bitrate = QTableWidgetItem(f"{track.bitrate} kbps" if track.bitrate else "—")
            item_bitrate.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, item_bitrate)

            # 4. Spectral Cutoff
            cutoff_text = f"{int(track.spectral_cutoff):,} Hz" if track.spectral_cutoff > 0 else "—"
            item_cutoff = QTableWidgetItem(cutoff_text)
            item_cutoff.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if track.spectral_cutoff and track.spectral_cutoff < 17000:
                item_cutoff.setForeground(QColor(COLORS["warning"]))
            self.table.setItem(row, 4, item_cutoff)

            # 5. Diagnostic (Evidence-based SpectralAssessment Phase C)
            from core.spectral_types import SpectralAssessment
            if track.spectral_assessment == SpectralAssessment.SUSPECTED_TRANSCODE or track.fake_lossless_confidence > 50.0:
                diag = f"⚠️ Posible transcodificación ({track.fake_lossless_confidence:.0f}% consistencia)"
                item_diag = QTableWidgetItem(diag)
                item_diag.setForeground(QColor(COLORS["warning"]))
            elif track.spectral_assessment == SpectralAssessment.NO_LOSSY_EVIDENCE:
                item_diag = QTableWidgetItem("Sin evidencia lossy detectada")
                item_diag.setForeground(QColor(COLORS["success"]))
            elif track.spectral_assessment == SpectralAssessment.UNKNOWN and track.is_lossless:
                item_diag = QTableWidgetItem("Resultado espectral no concluyente")
                item_diag.setForeground(QColor(COLORS["text_muted"]))
            elif track.spectral_assessment == SpectralAssessment.NOT_ANALYZED and track.is_lossless:
                item_diag = QTableWidgetItem("Análisis espectral no realizado")
                item_diag.setForeground(QColor(COLORS["text_muted"]))
            elif track.is_lossless:
                item_diag = QTableWidgetItem("Lossless (FLAC/WAV)")
                item_diag.setForeground(QColor(COLORS["success"]))
            elif track.bitrate and track.bitrate < 192:
                item_diag = QTableWidgetItem(f"Baja tasa de bits ({track.bitrate}k)")
                item_diag.setForeground(QColor(COLORS["danger"]))
            else:
                item_diag = QTableWidgetItem("Fidelidad estándar")
                item_diag.setForeground(QColor(COLORS["text_muted"]))
            self.table.setItem(row, 5, item_diag)

            # 6. Score
            score_text = f"{track.quality_score:.0f}/100" if track.quality_score > 0 else "—"
            item_score = QTableWidgetItem(score_text)
            item_score.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if track.quality_score >= 80:
                item_score.setForeground(QColor(COLORS["success"]))
            elif track.quality_score >= 50:
                item_score.setForeground(QColor(COLORS["warning"]))
            else:
                item_score.setForeground(QColor(COLORS["danger"]))
            self.table.setItem(row, 6, item_score)

            # 7. Action: Open Folder
            btn_folder = QPushButton()
            btn_folder.setIcon(qta.icon("fa5s.folder-open", color=COLORS["text_muted"]))
            btn_folder.setFixedSize(28, 28)
            btn_folder.setObjectName("ghost")
            btn_folder.setToolTip("Abrir ubicación")
            btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_folder.clicked.connect(lambda checked, p=track.filepath: open_file_in_explorer(p))

            folder_container = QWidget()
            folder_l = QHBoxLayout(folder_container)
            folder_l.setContentsMargins(0, 0, 0, 0)
            folder_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            folder_l.addWidget(btn_folder)
            self.table.setCellWidget(row, 7, folder_container)

        self.table.setUpdatesEnabled(True)

        # Update pagination state
        if total_items > 0:
            self.lbl_page_info.setText(
                f"Página {self.current_page + 1:,} de {total_pages:,}  ·  Mostrando {start_idx + 1:,}–{end_idx:,} de {total_items:,} pistas"
            )
        else:
            self.lbl_page_info.setText("Sin pistas coincidentes")

        self.btn_first_page.setEnabled(self.current_page > 0)
        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(self.current_page < total_pages - 1)
        self.btn_last_page.setEnabled(self.current_page < total_pages - 1)

    def _on_row_double_clicked(self, index):
        row = index.row()
        start_idx = self.current_page * self.page_size
        actual_idx = start_idx + row
        if 0 <= actual_idx < len(self.filtered_tracks):
            track = self.filtered_tracks[actual_idx]
            self.player.play(track.filepath)

    def _go_first_page(self):
        if self.current_page != 0:
            self.current_page = 0
            self._render_table()

    def _go_prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._render_table()

    def _go_next_page(self):
        total_pages = max(1, (len(self.filtered_tracks) + self.page_size - 1) // self.page_size)
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._render_table()

    def _go_last_page(self):
        total_pages = max(1, (len(self.filtered_tracks) + self.page_size - 1) // self.page_size)
        if self.current_page != total_pages - 1:
            self.current_page = max(0, total_pages - 1)
            self._render_table()

    def _on_page_size_changed(self, index: int):
        sizes = [50, 100, 250, 500]
        if 0 <= index < len(sizes):
            self.page_size = sizes[index]
            self.current_page = 0
            self._render_table()
