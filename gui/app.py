"""
Main Desktop Application Window — Audio Cleaner (Figma Design).
Layout: Sidebar | Multi-View Stack (Biblioteca, Escaneo, Duplicados, Calidad, Configuración) | Bottom Player Bar
"""

import os
import sys
from typing import List, Optional
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFileDialog, QMessageBox, QApplication,
    QFrame, QSizePolicy, QDialog, QStackedWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from core.models import DuplicateGroup, DuplicateType, ScanStats, FileAction
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
from gui.components.sidebar import Sidebar
from gui.components.stats_bar import StatsBar
from gui.components.bottom_player import BottomPlayerBar
from gui.components.delete_modal import DeleteModal
from gui.components.library_view import LibraryView
from gui.components.quality_view import QualityView
from gui.components.settings_view import SettingsView
from gui.components.scanner_view import ScannerView
from gui.styles import COLORS, GLOBAL_QSS

import qtawesome as qta


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

        self.setWindowTitle("Audio Cleaner — Detector de Duplicados")
        self.resize(1280, 860)
        self.setMinimumSize(1024, 680)

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
        self._load_saved_session(initial_folder)

    def _get_session_path(self) -> str:
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        folder = os.path.join(app_data, "AudioDuplicateDetector")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "last_session.json")

    def _save_current_session(self):
        try:
            import json
            path = self._get_session_path()
            data = {
                "folder": self.current_folder,
                "groups": [g.to_dict() for g in self.all_groups]
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_saved_session(self, initial_folder: Optional[str] = None):
        import json
        saved_folder = ""
        saved_groups = []
        try:
            path = self._get_session_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    saved_folder = data.get("folder", "")
                    raw_groups = data.get("groups", [])
                    saved_groups = [DuplicateGroup.from_dict(g) for g in raw_groups if isinstance(g, dict)]
        except Exception:
            pass

        target_folder = initial_folder or saved_folder
        if saved_groups:
            self.all_groups = saved_groups

        if target_folder and os.path.exists(target_folder):
            self.set_active_folder(target_folder, save_session=False)

        # Smart instant recovery: if no saved groups in json, but DB has tracks for this folder,
        # cluster them instantly from SQLite cache!
        if not self.all_groups and target_folder and os.path.exists(target_folder):
            try:
                cached_tracks = self.db.get_all_tracks(dir_prefix=target_folder, include_fingerprints=True)
                if cached_tracks and len(cached_tracks) > 1:
                    from core.clustering import cluster_duplicates
                    self.all_groups = cluster_duplicates(
                        cached_tracks,
                        similarity_threshold=self.scanner.similarity_threshold
                    )
                    self._save_current_session()
            except Exception:
                pass

        if self.all_groups:
            self.sidebar.set_active_section("Duplicados")
            self.stack.setCurrentIndex(2)
            self._refresh_view()
        else:
            self._show_empty_state()


    # ─────────────────────────────────────────────────────────────────────────
    #  Layout construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_layout(self):
        central = QWidget()
        central.setObjectName("main_window")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Content row: sidebar + main panel ─────────────────────
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)

        # Left sidebar
        self.sidebar = Sidebar()
        self.sidebar.nav_changed.connect(self._on_nav_changed)
        self.sidebar.folder_requested.connect(self._choose_folder)
        content_row.addWidget(self.sidebar)

        # Main Stacked Panel
        self.stack = QStackedWidget()
        self.stack.setObjectName("main_panel")

        # ── Page 0: Library View ───────────────────────────────────
        self.library_view = LibraryView(db=self.db)
        self.library_view.scan_requested.connect(self._start_scan)
        self.library_view.folder_requested.connect(self._choose_folder)
        self.stack.addWidget(self.library_view)

        # ── Page 1: Scanner View ───────────────────────────────────
        self.scanner_view = ScannerView()
        self.scanner_view.start_scan_requested.connect(self._start_scan)
        self.scanner_view.pause_scan_requested.connect(self.scanner.pause)
        self.scanner_view.resume_scan_requested.connect(self.scanner.resume)
        self.scanner_view.cancel_scan_requested.connect(self.scanner.stop)
        self.scanner_view.folder_requested.connect(self._choose_folder)
        self.scanner_view.view_duplicates_requested.connect(
            lambda: self.sidebar.set_active_section("Duplicados")
        )
        self.stack.addWidget(self.scanner_view)

        # ── Page 2: Duplicates View (Main Analysis) ────────────────
        self.duplicates_view = QWidget()
        self.duplicates_view.setObjectName("main_panel")
        dup_layout = QVBoxLayout(self.duplicates_view)
        dup_layout.setContentsMargins(24, 20, 24, 20)
        dup_layout.setSpacing(14)

        # Stats Bar
        self.stats_bar = StatsBar()
        self.stats_bar.auto_recommend_requested.connect(self._handle_auto_recommend)
        self.stats_bar.move_duplicates_requested.connect(self._handle_move_duplicates)
        self.stats_bar.delete_duplicates_requested.connect(self._handle_delete_duplicates)
        dup_layout.addWidget(self.stats_bar)

        # Filter bar
        self.filter_bar = FilterBar(
            on_search_changed=self._apply_search,
            on_filter_changed=self._apply_filter,
            on_sort_changed=self._apply_sort,
            on_auto_recommend=self._handle_auto_recommend,
            on_move_duplicates=self._handle_move_duplicates,
            on_delete_duplicates=self._handle_delete_duplicates,
        )
        dup_layout.addWidget(self.filter_bar)

        # Scrollable results area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet(f"background-color: {COLORS['bg_main']};")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(0, 0, 8, 0)
        self.scroll_layout.setSpacing(8)

        self.scroll_area.setWidget(self.scroll_content)
        dup_layout.addWidget(self.scroll_area, stretch=1)
        self.stack.addWidget(self.duplicates_view)

        # ── Page 3: Quality View ───────────────────────────────────
        self.quality_view = QualityView(db=self.db)
        self.quality_view.scan_requested.connect(self._start_scan)
        self.stack.addWidget(self.quality_view)

        # ── Page 4: Settings View ──────────────────────────────────
        self.settings_view = SettingsView(db=self.db)
        self.settings_view.settings_saved.connect(self._on_settings_saved)
        self.stack.addWidget(self.settings_view)

        content_row.addWidget(self.stack, stretch=1)

        row_container = QWidget()
        row_container.setLayout(content_row)
        root.addWidget(row_container, stretch=1)

        # ── Bottom Player Bar (fixed height at bottom) ─────────────
        self.bottom_player = BottomPlayerBar()
        root.addWidget(self.bottom_player)

        # Set default view to Duplicados
        self.stack.setCurrentIndex(2)
        self._show_empty_state()

    # ─────────────────────────────────────────────────────────────────────────
    #  Navigation & folder
    # ─────────────────────────────────────────────────────────────────────────

    def _on_nav_changed(self, section: str):
        if section == "Biblioteca":
            self.stack.setCurrentIndex(0)
            self.library_view.set_folder(self.current_folder)
        elif section == "Escaneo":
            self.stack.setCurrentIndex(1)
            self.scanner_view.set_folder(self.current_folder)
        elif section == "Duplicados":
            self.stack.setCurrentIndex(2)
            self._refresh_view()
        elif section == "Calidad":
            self.stack.setCurrentIndex(3)
            self.quality_view.set_folder(self.current_folder)
        elif section == "Configuración":
            self.stack.setCurrentIndex(4)
            self.settings_view.refresh_db_stats()

    def set_active_folder(self, folder: str, save_session: bool = True):
        self.current_folder = folder
        self.sidebar.set_folder(folder)
        self.sidebar.storage_bar.update_from_folder(folder)
        self.scanner_view.set_folder(folder)
        self.library_view.set_folder(folder)
        self.quality_view.set_folder(folder)
        if save_session:
            self._save_current_session()

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar Carpeta de Música", self.current_folder
        )
        if folder:
            self.set_active_folder(folder)

    def _on_settings_saved(self, config: dict):
        if "similarity_threshold" in config:
            self.scanner.similarity_threshold = config["similarity_threshold"]
        if "threads" in config:
            self.scanner.max_workers = config["threads"]

    # ─────────────────────────────────────────────────────────────────────────
    #  Scanning
    # ─────────────────────────────────────────────────────────────────────────

    def _start_scan(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            reply = QMessageBox.question(
                self, "Carpeta requerida",
                "No hay una carpeta de música seleccionada. ¿Deseas seleccionar una carpeta ahora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._choose_folder()
                if not self.current_folder or not os.path.isdir(self.current_folder):
                    return
            else:
                return

        self.stats_bar.reset()
        self.sidebar.set_active_section("Escaneo")
        self.stack.setCurrentIndex(1)
        self.scanner_view.start_scanning_ui()

        self.worker = ScannerWorker(self.scanner, self.current_folder)
        self.worker.progress_updated.connect(self.scanner_view.update_stats)
        self.worker.scan_finished.connect(self._on_scan_finished)
        self.worker.start()

    def _on_scan_finished(self, groups: List[DuplicateGroup]):
        self.all_groups = groups
        self.scanner_view.finish_scanning_ui(len(groups))
        self._save_current_session()

        # Update all views data
        self.library_view.reload_tracks()
        self.quality_view.reload_tracks()
        self.sidebar.storage_bar.update_from_folder(self.current_folder)
        self.settings_view.refresh_db_stats()

        # Switch to Duplicados if duplicate groups were found
        if groups:
            self.sidebar.set_active_section("Duplicados")
            self.stack.setCurrentIndex(2)
            self._refresh_view()
        else:
            self._refresh_view()

    def closeEvent(self, event):
        self._save_current_session()
        super().closeEvent(event)


    # ─────────────────────────────────────────────────────────────────────────
    #  Empty & results states
    # ─────────────────────────────────────────────────────────────────────────

    def _show_empty_state(self, message: str = ""):
        self._clear_results_area()

        empty_frame = QFrame()
        empty_frame.setObjectName("transparent")
        layout = QVBoxLayout(empty_frame)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0, 60, 0, 0)
        layout.setSpacing(20)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(
            qta.icon("fa5s.wave-square", color=COLORS["text_dim"]).pixmap(72, 72)
        )
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        if not message:
            message = "No se han encontrado duplicados"
        msg_lbl = QLabel(message)
        msg_lbl.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {COLORS['text_dim']};")
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg_lbl)

        sub_lbl = QLabel(
            "Selecciona una carpeta en la barra lateral o pulsa 'Iniciar escaneo'\n"
            "para detectar archivos duplicados y evaluar calidad."
        )
        sub_lbl.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10pt;")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub_lbl)

        btn_scan = QPushButton("  Iniciar escaneo de biblioteca")
        btn_scan.setObjectName("primary")
        btn_scan.setIcon(qta.icon("fa5s.search", color="#000000"))
        btn_scan.setFixedSize(260, 40)
        btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_scan.clicked.connect(self._start_scan)
        layout.addWidget(btn_scan, alignment=Qt.AlignmentFlag.AlignCenter)

        self.scroll_layout.addWidget(empty_frame)

    def _clear_results_area(self):
        """Efficiently clear all child widgets from the scroll area."""
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    # ─────────────────────────────────────────────────────────────────────────
    #  Filter / sort / search
    # ─────────────────────────────────────────────────────────────────────────

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
        """Filters, sorts, and renders the cards."""
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
                q = self.current_search_query.lower()
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

        if self.current_sort_mode == "Mayor Ahorro de Espacio":
            filtered.sort(key=lambda g: g.space_saving_bytes, reverse=True)
        elif self.current_sort_mode == "Mayor Similitud":
            filtered.sort(key=lambda g: g.average_similarity, reverse=True)
        elif self.current_sort_mode == "Nombre / Artista":
            filtered.sort(key=lambda g: g.tracks[0].display_title.lower() if g.tracks else "")
        elif self.current_sort_mode == "Más Archivos por Grupo":
            filtered.sort(key=lambda g: len(g.tracks), reverse=True)

        self.filtered_groups = filtered

        # Update global stats
        total_files = sum(len(g.tracks) for g in self.all_groups)
        total_space = sum(g.space_saving_bytes for g in self.all_groups)
        self.stats_bar.update_stats(len(self.all_groups), total_files, total_space)

        self._current_page = 0
        self._render_job_id += 1
        self._render_cards(job_id=self._render_job_id)

    def _render_cards(self, job_id: int):
        if job_id != self._render_job_id:
            return

        if self._current_page == 0:
            self._clear_results_area()

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

        if end_idx < len(self.filtered_groups):
            self.btn_load_more = QPushButton("Cargar más resultados...")
            self.btn_load_more.setObjectName("ghost")
            self.btn_load_more.setFixedHeight(40)
            self.btn_load_more.clicked.connect(lambda: self._load_next_page(job_id))
            self.scroll_layout.addWidget(self.btn_load_more)

    def _load_next_page(self, job_id: int):
        if hasattr(self, "btn_load_more") and self.btn_load_more:
            self.btn_load_more.deleteLater()
            self.btn_load_more = None

        self._current_page += 1
        self._render_cards(job_id)

    def _on_group_action_changed(self, group: DuplicateGroup):
        pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_auto_recommend(self):
        if not self.filtered_groups:
            return
        mod = auto_apply_recommendations(self.filtered_groups)
        self._refresh_view()
        QMessageBox.information(
            self, "Auto-Selección",
            f"Se han auto-seleccionado las recomendaciones de calidad en {mod} grupos."
        )

    def _handle_move_duplicates(self):
        target_dir = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta destino para duplicados"
        )
        if not target_dir:
            return
        success, failed, logs = move_marked_duplicates(self.filtered_groups, target_dir)
        msg = f"Archivos movidos: {success}"
        if failed:
            msg += f"\nErrores al mover: {failed}"
        QMessageBox.information(self, "Mover Completado", msg)
        self._refresh_view()

    def _handle_delete_duplicates(self):
        marked = [
            t for g in self.filtered_groups for t in g.tracks
            if t.action == FileAction.DELETE
        ]
        if not marked:
            QMessageBox.information(
                self, "Sin archivos seleccionados",
                "Marca al menos un archivo como ELIMINAR antes de continuar."
            )
            return

        modal = DeleteModal(self.filtered_groups, parent=self)
        if modal.exec() == QDialog.DialogCode.Accepted:
            success, failed, logs = modal.execute_action()
            msg = f"Archivos procesados: {success}"
            if failed:
                msg += f"\nErrores: {failed}"
            QMessageBox.information(self, "Acción completada", msg)
            self._refresh_view()

    def closeEvent(self, event):
        """Properly clean up resources on window close."""
        self._save_current_session()
        # Stop any running scan
        if self.worker and self.worker.isRunning():
            self.scanner.stop()
            self.worker.wait(2000)
        # Close the persistent SQLite connection gracefully
        try:
            self.db.close()
        except Exception:
            pass
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_gui(initial_folder=None):
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "mycompany.audiocleaner.v1"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_QSS)

    icon_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_icon.png"
    )
    if hasattr(sys, "_MEIPASS"):
        meipass_icon = os.path.join(sys._MEIPASS, "app_icon.png")
        if os.path.exists(meipass_icon):
            icon_path = meipass_icon

    if os.path.exists(icon_path):
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))

    window = AudioDuplicateDetectorApp(initial_folder)
    if os.path.exists(icon_path):
        from PyQt6.QtGui import QIcon
        window.setWindowIcon(QIcon(icon_path))

    window.show()
    sys.exit(app.exec())
