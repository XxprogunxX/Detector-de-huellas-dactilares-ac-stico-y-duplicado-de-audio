"""
Scan Progress and Live Statistics component for PyQt6 GUI.
"""

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar
)
from PyQt6.QtCore import Qt, pyqtSignal
from core.models import ScanStats
from gui.styles import COLORS
import qtawesome as qta


class ScanProgressWidget(QFrame):
    # Signals for parent communication
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transparent")  # Uses the generic background
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Top row: Status message and Action buttons
        top_frame = QFrame()
        top_frame.setObjectName("transparent")
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(16, 14, 16, 8)

        self.lbl_status = QLabel("Listo para escanear.")
        self.lbl_status.setObjectName("subtitle")
        top_layout.addWidget(self.lbl_status, stretch=1)

        self.btn_pause = QPushButton(" Pausar")
        self.btn_pause.setIcon(qta.icon("fa5s.pause", color="white"))
        self.btn_pause.setObjectName("warning")
        self.btn_pause.setFixedSize(110, 30)
        self.btn_pause.clicked.connect(self._toggle_pause)
        top_layout.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton(" Detener")
        self.btn_cancel.setIcon(qta.icon("fa5s.stop", color="white"))
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setFixedSize(110, 30)
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)
        top_layout.addWidget(self.btn_cancel)

        main_layout.addWidget(top_frame)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Current file indicator
        self.lbl_current_file = QLabel("")
        self.lbl_current_file.setObjectName("dim")
        self.lbl_current_file.setWordWrap(True)
        main_layout.addWidget(self.lbl_current_file)

        # Stats Cards Grid
        self.stats_frame = QFrame()
        self.stats_frame.setObjectName("transparent")
        stats_layout = QHBoxLayout(self.stats_frame)
        stats_layout.setContentsMargins(16, 0, 16, 14)
        stats_layout.setSpacing(8)

        self.card_scanned, self.val_scanned = self._create_stat_card("Archivos", "0 / 0", COLORS["primary"])
        stats_layout.addWidget(self.card_scanned, stretch=1)

        self.card_exact, self.val_exact = self._create_stat_card("Exactos", "0", COLORS["primary"])
        stats_layout.addWidget(self.card_exact, stretch=1)

        self.card_acoustic, self.val_acoustic = self._create_stat_card("Acústicos", "0", COLORS["success"])
        stats_layout.addWidget(self.card_acoustic, stretch=1)

        self.card_possible, self.val_possible = self._create_stat_card("Posibles", "0", COLORS["warning"])
        stats_layout.addWidget(self.card_possible, stretch=1)

        self.card_savings, self.val_savings = self._create_stat_card("Espacio Ahorrable", "0 MB", COLORS["accent"])
        stats_layout.addWidget(self.card_savings, stretch=1)

        main_layout.addWidget(self.stats_frame)

    def _create_stat_card(self, label_text: str, value_text: str, accent_color: str):
        card = QFrame()
        card.setObjectName("surface")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_title = QLabel(label_text)
        lbl_title.setObjectName("muted")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_title)

        lbl_val = QLabel(value_text)
        lbl_val.setObjectName("title")
        lbl_val.setStyleSheet(f"color: {accent_color};")
        lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_val)

        return card, lbl_val

    def update_stats(self, stats: ScanStats):
        """Updates GUI widgets with real-time stats."""
        # Progress value
        if stats.total_files_found > 0:
            pct = stats.files_scanned / stats.total_files_found
            self.progress_bar.setValue(int(pct * 100))
            pct_str = f"{int(pct * 100)}%"
        else:
            self.progress_bar.setValue(0)
            pct_str = "0%"

        self.lbl_status.setText(f"{stats.phase} ({pct_str})")
        if stats.current_file:
            self.lbl_current_file.setText(f"Procesando: {stats.current_file}")
        else:
            self.lbl_current_file.setText("")

        self.val_scanned.setText(f"{stats.files_scanned:,} / {stats.total_files_found:,}")
        self.val_exact.setText(f"{stats.exact_duplicates_count:,}")
        self.val_acoustic.setText(f"{stats.acoustic_duplicates_count:,}")
        self.val_possible.setText(f"{stats.possible_duplicates_count:,}")

        # Savings in MB / GB
        savings_mb = stats.potential_space_saving / (1024 * 1024)
        if savings_mb >= 1024:
            savings_str = f"{savings_mb / 1024:.2f} GB"
        else:
            savings_str = f"{savings_mb:.1f} MB"
        self.val_savings.setText(savings_str)

        # Update button states
        if stats.is_paused:
            self.btn_pause.setText(" Reanudar")
            self.btn_pause.setIcon(qta.icon("fa5s.play", color="white"))
            self.btn_pause.setObjectName("success")
            self.btn_pause.setStyleSheet("") # Force re-evaluation of QSS
        else:
            self.btn_pause.setText(" Pausar")
            self.btn_pause.setIcon(qta.icon("fa5s.pause", color="white"))
            self.btn_pause.setObjectName("warning")
            self.btn_pause.setStyleSheet("")

        self.btn_pause.setEnabled(stats.is_running)
        self.btn_cancel.setEnabled(stats.is_running)

    def _toggle_pause(self):
        if "Pausar" in self.btn_pause.text():
            self.pause_requested.emit()
        else:
            self.resume_requested.emit()
