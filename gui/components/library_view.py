"""
Library View component — Explore and manage indexed audio tracks in the active library.
"""

import os
from typing import List, Optional, Callable
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor
import qtawesome as qta

from core.models import AudioTrack
from core.database import Database
from core.file_manager import open_file_in_explorer
from gui.components.audio_player import AudioPlayer
from gui.styles import COLORS


class LibraryView(QWidget):
    scan_requested = pyqtSignal()
    folder_requested = pyqtSignal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.player = AudioPlayer.get_instance()
        self.current_folder: str = ""
        self.tracks: List[AudioTrack] = []
        self.filtered_tracks: List[AudioTrack] = []

        self.current_page: int = 0
        self.page_size: int = 100

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── Header Banner ──────────────────────────────────────────
        header_card = QFrame()
        header_card.setObjectName("card")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(16)

        # Info block
        info_block = QVBoxLayout()
        info_block.setSpacing(4)
        
        self.lbl_title = QLabel("BIBLIOTECA DE MÚSICA")
        self.lbl_title.setObjectName("section_label")
        info_block.addWidget(self.lbl_title)

        self.lbl_folder_path = QLabel("Sin carpeta seleccionada")
        self.lbl_folder_path.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self.lbl_folder_path.setStyleSheet(f"color: {COLORS['cyan']};")
        info_block.addWidget(self.lbl_folder_path)

        self.lbl_stats = QLabel("0 pistas indexadas · 0 MB totales")
        self.lbl_stats.setObjectName("muted")
        info_block.addWidget(self.lbl_stats)

        header_layout.addLayout(info_block, stretch=1)

        # Buttons block
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_change_folder = QPushButton(" Cambiar Carpeta")
        self.btn_change_folder.setObjectName("ghost")
        self.btn_change_folder.setIcon(qta.icon("fa5s.folder-open", color=COLORS["text_muted"]))
        self.btn_change_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_folder.clicked.connect(self.folder_requested.emit)
        btn_layout.addWidget(self.btn_change_folder)

        self.btn_scan_now = QPushButton(" Escanear / Actualizar")
        self.btn_scan_now.setObjectName("primary")
        self.btn_scan_now.setIcon(qta.icon("fa5s.sync", color="#000000"))
        self.btn_scan_now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan_now.clicked.connect(self.scan_requested.emit)
        btn_layout.addWidget(self.btn_scan_now)

        header_layout.addLayout(btn_layout)
        root.addWidget(header_card)

        # ── Formats summary chips ──────────────────────────────────
        self.formats_container = QFrame()
        self.formats_container.setObjectName("transparent")
        self.formats_layout = QHBoxLayout(self.formats_container)
        self.formats_layout.setContentsMargins(0, 0, 0, 0)
        self.formats_layout.setSpacing(10)
        root.addWidget(self.formats_container)

        # ── Search & Filter Controls ───────────────────────────────
        controls_row = QHBoxLayout()
        controls_row.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar por título, artista, álbum o nombre de archivo...")
        self.search_input.setFixedHeight(34)
        
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._apply_search)
        self.search_input.textChanged.connect(lambda: self.search_timer.start())
        controls_row.addWidget(self.search_input, stretch=1)

        self.combo_format = QComboBox()
        self.combo_format.addItem("Todos los Formatos", "ALL")
        self.combo_format.setFixedHeight(34)
        self.combo_format.currentTextChanged.connect(lambda: self._apply_search())
        controls_row.addWidget(self.combo_format)

        self.combo_sort = QComboBox()
        self.combo_sort.addItems([
            "Título / Nombre",
            "Artista / Álbum",
            "Mayor Calidad (Score)",
            "Mayor Bitrate",
            "Mayor Tamaño de Archivo",
            "Mayor Duración"
        ])
        self.combo_sort.setFixedHeight(34)
        self.combo_sort.currentTextChanged.connect(lambda: self._apply_search())
        controls_row.addWidget(self.combo_sort)

        root.addLayout(controls_row)

        # ── Table View ─────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "►", "Título / Archivo", "Artista", "Álbum", "Formato", "Bitrate / Frecuencia", "Calidad", "Acciones"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.doubleClicked.connect(self._on_row_double_clicked)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 42)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(2, 140)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(3, 140)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(4, 85)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(5, 140)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(6, 95)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(7, 75)

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
        if folder:
            self.lbl_folder_path.setText(folder)
            self.reload_tracks()
        else:
            self.lbl_folder_path.setText("Todas las pistas indexadas (Sin filtro de carpeta)")
            self.reload_tracks()

    def reload_tracks(self, custom_tracks: Optional[List[AudioTrack]] = None):
        if custom_tracks is not None:
            self.tracks = custom_tracks
        elif self.current_folder:
            self.tracks = self.db.get_all_tracks(dir_prefix=self.current_folder, include_fingerprints=False)
        else:
            self.tracks = self.db.get_all_tracks(include_fingerprints=False)

        total_files = len(self.tracks)
        total_bytes = sum(t.filesize for t in self.tracks)
        size_mb = total_bytes / (1024 * 1024)
        size_str = f"{size_mb / 1024:.2f} GB" if size_mb >= 1024 else f"{size_mb:.1f} MB"
        self.lbl_stats.setText(f"{total_files:,} pistas indexadas  ·  {size_str} totales")

        self._update_format_chips()
        self.current_page = 0
        self._apply_search()

    def _update_format_chips(self):
        # Clear existing chips
        while self.formats_layout.count():
            item = self.formats_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # Count formats
        format_counts = {}
        for t in self.tracks:
            fmt = (t.format or "OTRO").upper()
            format_counts[fmt] = format_counts.get(fmt, 0) + 1

        # Populate combo
        current_fmt = self.combo_format.currentData()
        self.combo_format.blockSignals(True)
        self.combo_format.clear()
        self.combo_format.addItem("Todos los Formatos", "ALL")
        for fmt, cnt in sorted(format_counts.items(), key=lambda x: x[1], reverse=True):
            self.combo_format.addItem(f"{fmt} ({cnt:,})", fmt)
        self.combo_format.blockSignals(False)

        # Build chips
        for fmt, cnt in sorted(format_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            chip = QFrame()
            chip.setObjectName("card")
            chip_l = QHBoxLayout(chip)
            chip_l.setContentsMargins(10, 6, 10, 6)
            chip_l.setSpacing(6)

            dot = QLabel("●")
            dot.setStyleSheet(f"color: {COLORS['cyan']}; font-size: 8pt;")
            lbl = QLabel(f"<b>{fmt}</b>: {cnt:,}")
            lbl.setFont(QFont("Segoe UI", 8))
            chip_l.addWidget(dot)
            chip_l.addWidget(lbl)
            self.formats_layout.addWidget(chip)

        self.formats_layout.addStretch()

    def _apply_search(self):
        query = self.search_input.text().strip().lower()
        selected_fmt = self.combo_format.currentData() or "ALL"

        filtered = []
        for t in self.tracks:
            if selected_fmt != "ALL" and (t.format or "").upper() != selected_fmt:
                continue

            if query:
                match = (
                    query in t.filename.lower() or
                    query in t.title.lower() or
                    query in t.artist.lower() or
                    query in t.album.lower() or
                    query in t.format.lower()
                )
                if not match:
                    continue

            filtered.append(t)

        sort_mode = self.combo_sort.currentText()
        if sort_mode == "Título / Nombre":
            filtered.sort(key=lambda x: x.display_title.lower())
        elif sort_mode == "Artista / Álbum":
            filtered.sort(key=lambda x: (x.artist.lower(), x.album.lower()))
        elif sort_mode == "Mayor Calidad (Score)":
            filtered.sort(key=lambda x: x.quality_score, reverse=True)
        elif sort_mode == "Mayor Bitrate":
            filtered.sort(key=lambda x: x.bitrate, reverse=True)
        elif sort_mode == "Mayor Tamaño de Archivo":
            filtered.sort(key=lambda x: x.filesize, reverse=True)
        elif sort_mode == "Mayor Duración":
            filtered.sort(key=lambda x: x.duration, reverse=True)

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

            # 2. Artist
            item_artist = QTableWidgetItem(track.artist or "—")
            self.table.setItem(row, 2, item_artist)

            # 3. Album
            item_album = QTableWidgetItem(track.album or "—")
            self.table.setItem(row, 3, item_album)

            # 4. Format
            item_fmt = QTableWidgetItem((track.format or "").upper())
            item_fmt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if track.is_lossless:
                item_fmt.setForeground(QColor(COLORS["success"]))
            self.table.setItem(row, 4, item_fmt)

            # 5. Bitrate / Specs
            specs = f"{track.bitrate} kbps · {track.samplerate // 1000}kHz"
            if track.bit_depth and track.bit_depth > 16:
                specs += f" ({track.bit_depth}-bit)"
            item_specs = QTableWidgetItem(specs)
            self.table.setItem(row, 5, item_specs)

            # 6. Quality Score
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
