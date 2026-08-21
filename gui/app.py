"""
Main Desktop Application Window using PyQt6.
"""

import os
import sys
from typing import List, Optional
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFileDialog, QMessageBox, QApplication,
    QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
import qtawesome as qta

from core.models import DuplicateGroup, DuplicateType, ScanStats
from core.scanner import AudioScanner
from core.database import Database
from core.file_manager import (
    auto_apply_recommendations,
    move_marked_duplicates,
    delete_marked_duplicates_permanently
)
from gui.components.scan_progress import ScanProgressWidget
from gui.components.filter_bar import FilterBar
from gui.components.duplicate_card import DuplicateGroupCard
from gui.components.audio_player import AudioPlayer
from gui.styles import COLORS, GLOBAL_QSS


class ScannerWorker(QThread):
    """Background thread for running the scan without blocking the GUI."""
    progress_updated = pyqtSignal(ScanStats)
    scan_finished = pyqtSignal(list)
    
    def __init__(self, scanner: AudioScanner, folder: str):
        super().__init__()
        self.scanner = scanner
        self.folder = folder
        
    def run(self):
        groups = self.scanner.scan_directory(
            self.folder,
            progress_callback=self.progress_updated.emit
        )
        self.scan_finished.emit(groups)


class AudioDuplicateDetectorApp(QMainWindow):
    def __init__(self, initial_folder: Optional[str] = None):
        super().__init__()

        self.setWindowTitle("🎵 Analizador de Duplicados de Música Acústico")
        self.resize(1100, 820)
        self.setMinimumSize(950, 650)

        self.db = Database()
        self.scanner = AudioScanner(db=self.db)
        self.player = AudioPlayer.get_instance()
        self.worker = None

        self.current_folder: str = initial_folder or ""
        self.all_groups: List[DuplicateGroup] = []
        self.filtered_groups: List[DuplicateGroup] = []
        
        self.current_filter_type: str = "all"
        self.current_search_query: str = ""
        self.current_sort_mode: str = "Mayor Ahorro de Espacio"

        self.PAGE_SIZE: int = 50
        self._current_page: int = 0
        self._render_job_id: int = 0

        self._build_layout()

    def _build_layout(self):
        central_widget = QWidget()
        central_widget.setObjectName("main_window")
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Top Navigation & Folder Selection Bar
        top_bar = QFrame()
        top_bar.setStyleSheet(f"background-color: {COLORS['bg_card']};")
        top_bar.setFixedHeight(70)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 0, 20, 0)

        title_lbl = QLabel("Audio Fingerprint Duplicate Detector")
        title_lbl.setObjectName("title")
        top_layout.addWidget(title_lbl)
        
        top_layout.addStretch()

        self.lbl_selected_folder = QLabel(self.current_folder or "Ninguna carpeta seleccionada")
        self.lbl_selected_folder.setObjectName("small")
        self.lbl_selected_folder.setStyleSheet(f"color: {COLORS['text_muted']};")
        top_layout.addWidget(self.lbl_selected_folder)

        self.btn_select = QPushButton(" Seleccionar Carpeta")
        self.btn_select.setIcon(qta.icon("fa5s.folder-open", color=COLORS["text_main"]))
        self.btn_select.clicked.connect(self._choose_folder)
        top_layout.addWidget(self.btn_select)

        self.btn_scan = QPushButton(" Iniciar Escaneo")
        self.btn_scan.setIcon(qta.icon("fa5s.rocket", color="white"))
        self.btn_scan.setObjectName("primary")
        self.btn_scan.clicked.connect(self._start_scan)
        top_layout.addWidget(self.btn_scan)

        main_layout.addWidget(top_bar)

        # 2. Main Content Container
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(12)

        # 3. Progress Widget
        self.progress_widget = ScanProgressWidget()
        self.progress_widget.pause_requested.connect(self.scanner.pause)
        self.progress_widget.resume_requested.connect(self.scanner.resume)
        self.progress_widget.cancel_requested.connect(self.scanner.stop)
        self.progress_widget.hide()
        content_layout.addWidget(self.progress_widget)

        # 4. Filter & Batch Action Bar
        self.filter_bar = FilterBar(
            on_search_changed=self._apply_search,
            on_filter_changed=self._apply_filter,
            on_sort_changed=self._apply_sort,
            on_auto_recommend=self._handle_auto_recommend,
            on_move_duplicates=self._handle_move_duplicates,
            on_delete_duplicates=self._handle_delete_duplicates
        )
        content_layout.addWidget(self.filter_bar)

        # 5. Scrollable Results Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(0, 0, 10, 0)
        self.scroll_layout.setSpacing(10)
        
        self.scroll_area.setWidget(self.scroll_content)
        content_layout.addWidget(self.scroll_area, stretch=1)
        
        main_layout.addWidget(content_container, stretch=1)

        self._show_empty_state("Selecciona una carpeta y pulsa 'Iniciar Escaneo' para comenzar.")

    def _show_empty_state(self, message: str):
        self._clear_results_area()

        empty_frame = QFrame()
        empty_frame.setObjectName("transparent")
        layout = QVBoxLayout(empty_frame)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0, 80, 0, 0)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon("fa5s.search", color=COLORS["text_dim"]).pixmap(64, 64))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        text_lbl = QLabel(message)
        text_lbl.setObjectName("subtitle")
        text_lbl.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")
        text_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text_lbl)

        self.scroll_layout.addWidget(empty_frame)

    def _clear_results_area(self):
        """Efficiently clear all child widgets from the scroll area."""
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta de Música", self.current_folder)
        if folder:
            self.current_folder = folder
            disp = folder if len(folder) < 35 else "..." + folder[-32:]
            self.lbl_selected_folder.setText(disp)

    def _start_scan(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(self, "Carpeta requerida", "Por favor selecciona una carpeta válida con archivos de música.")
            return

        self.btn_scan.setEnabled(False)
        self.btn_select.setEnabled(False)
        
        self.progress_widget.show()
        self._show_empty_state("Analizando biblioteca de música... Por favor espera.")

        self.worker = ScannerWorker(self.scanner, self.current_folder)
        self.worker.progress_updated.connect(self.progress_widget.update_stats)
        self.worker.scan_finished.connect(self._on_scan_finished)
        self.worker.start()

    def _on_scan_finished(self, groups: List[DuplicateGroup]):
        self.progress_widget.hide()
        self.btn_scan.setEnabled(True)
        self.btn_select.setEnabled(True)
        self.all_groups = groups
        self._refresh_view()

    def _apply_filter(self, filter_type: str):
        self.current_filter_type = filter_type
        self._refresh_view()

    def _apply_sort(self, sort_mode: str):
        self.current_sort_mode = sort_mode
        self._refresh_view()

    def _apply_search(self, query: str):
        self.current_search_query = query
        self._refresh_view()

    def _refresh_view(self):
        """Filters, sorts, and renders the cards using PyQt's highly optimized scroll area."""
        filtered = []
        for g in self.all_groups:
            if self.current_filter_type == "exact":
                if g.primary_type not in (DuplicateType.EXACT_HASH, DuplicateType.EXACT_AUDIO):
                    continue
            elif self.current_filter_type == "acoustic":
                if g.primary_type != DuplicateType.ACOUSTIC_DUPLICATE:
                    continue
            elif self.current_filter_type == "possible":
                if g.primary_type != DuplicateType.POSSIBLE_DUPLICATE:
                    continue

            if self.current_search_query:
                q = self.current_search_query
                match = any(
                    q in t.filename.lower() or
                    q in t.title.lower() or
                    q in t.artist.lower() or
                    q in t.album.lower() or
                    q in t.format.lower()
                    for t in g.tracks
                )
                if not match:
                    continue

            filtered.append(g)

        # Sort
        if self.current_sort_mode == "Mayor Ahorro de Espacio":
            filtered.sort(key=lambda g: g.space_saving_bytes, reverse=True)
        elif self.current_sort_mode == "Mayor Similitud":
            filtered.sort(key=lambda g: g.average_similarity, reverse=True)
        elif self.current_sort_mode == "Nombre / Artista":
            filtered.sort(key=lambda g: g.tracks[0].display_title.lower() if g.tracks else "")
        elif self.current_sort_mode == "Más Archivos por Grupo":
            filtered.sort(key=lambda g: len(g.tracks), reverse=True)

        self.filtered_groups = filtered
        self._current_page = 0
        self._render_job_id += 1
        self._render_cards(job_id=self._render_job_id)

    def _render_cards(self, job_id: int):
        if job_id != self._render_job_id:
            return
            
        if self._current_page == 0:
            self._clear_results_area()
            
            # Calculate stats for the FilterBar (only on first page)
            counts = {"all": 0, "exact": 0, "acoustic": 0, "possible": 0}
            for g in self.all_groups:
                counts["all"] += 1
                if g.primary_type in (DuplicateType.EXACT_HASH, DuplicateType.EXACT_AUDIO):
                    counts["exact"] += 1
                elif g.primary_type == DuplicateType.ACOUSTIC_DUPLICATE:
                    counts["acoustic"] += 1
                elif g.primary_type == DuplicateType.POSSIBLE_DUPLICATE:
                    counts["possible"] += 1
            
            self.filter_bar.update_counts(counts, len(self.filtered_groups))

            if not self.filtered_groups:
                self._show_empty_state("No se encontraron resultados para los filtros actuales.")
                return

        start_idx = self._current_page * self.PAGE_SIZE
        end_idx = start_idx + self.PAGE_SIZE
        chunk = self.filtered_groups[start_idx:end_idx]

        for group in chunk:
            card = DuplicateGroupCard(
                group, 
                on_action_changed=self._on_group_action_changed
            )
            self.scroll_layout.addWidget(card)
            
        # Add "Load More" button if there are more results
        if end_idx < len(self.filtered_groups):
            self.btn_load_more = QPushButton("Cargar más resultados...")
            self.btn_load_more.setObjectName("primary")
            self.btn_load_more.setFixedHeight(40)
            self.btn_load_more.clicked.connect(lambda: self._load_next_page(job_id))
            self.scroll_layout.addWidget(self.btn_load_more)

    def _load_next_page(self, job_id: int):
        if hasattr(self, 'btn_load_more') and self.btn_load_more:
            self.btn_load_more.deleteLater()
            self.btn_load_more = None
            
        self._current_page += 1
        self._render_cards(job_id)

    def _on_group_action_changed(self, group: DuplicateGroup):
        # Optional hook to update global stats if needed
        pass

    def _handle_auto_recommend(self):
        if not self.filtered_groups:
            return
        mod = auto_apply_recommendations(self.filtered_groups)
        self._refresh_view()
        QMessageBox.information(self, "Auto-Selección", f"Se han auto-seleccionado las recomendaciones de calidad en {mod} grupos.")

    def _handle_move_duplicates(self):
        target_dir = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta destino para duplicados")
        if not target_dir:
            return
            
        success, failed, logs = move_marked_duplicates(self.filtered_groups, target_dir)
        msg = f"Archivos movidos: {success}"
        if failed:
            msg += f"\nErrores al mover: {failed}"
            
        QMessageBox.information(self, "Mover Completado", msg)
        self._refresh_view()

    def _handle_delete_duplicates(self):
        reply = QMessageBox.question(
            self,
            "Confirmar Eliminación",
            "¿Estás seguro de que deseas ELIMINAR PERMANENTEMENTE los archivos marcados? Esta acción no se puede deshacer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success, failed, logs = delete_marked_duplicates_permanently(self.filtered_groups)
            msg = f"Archivos eliminados: {success}"
            if failed:
                msg += f"\nErrores al eliminar: {failed}"
                
            QMessageBox.information(self, "Eliminación Completada", msg)
            self._refresh_view()


def run_gui(initial_folder=None):
    # Set Windows App User Model ID so the taskbar displays the custom icon properly
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mycompany.audioduplicatedetector.v1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_QSS)

    # Set Application Icon
    icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_icon.png")
    if hasattr(sys, "_MEIPASS"):
        meipass_icon = os.path.join(sys._MEIPASS, "app_icon.png")
        if os.path.exists(meipass_icon):
            icon_path = meipass_icon

    if os.path.exists(icon_path):
        from PyQt6.QtGui import QIcon
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    window = AudioDuplicateDetectorApp(initial_folder)
    if os.path.exists(icon_path):
        from PyQt6.QtGui import QIcon
        window.setWindowIcon(QIcon(icon_path))

    window.show()
    sys.exit(app.exec())
