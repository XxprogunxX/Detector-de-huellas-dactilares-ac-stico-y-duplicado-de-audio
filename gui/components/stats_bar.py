"""
Stats Bar component — top summary row with KPI cards.
Audio Cleaner Figma design.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
import qtawesome as qta

from gui.styles import COLORS


class StatCard(QFrame):
    def __init__(self, label: str, icon_name: str, accent: str,
                 large: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._accent = accent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon(icon_name, color=accent).pixmap(14, 14))
        top.addWidget(icon_lbl)

        lbl = QLabel(label.upper())
        lbl.setObjectName("section_label")
        top.addWidget(lbl)
        top.addStretch()
        layout.addLayout(top)

        self.value_lbl = QLabel("—")
        font_size = 22 if large else 17
        self.value_lbl.setFont(QFont("Segoe UI", font_size, QFont.Weight.Bold))
        self.value_lbl.setStyleSheet(f"color: {accent};")
        layout.addWidget(self.value_lbl)

        if large:
            self.setStyleSheet(
                f"QFrame#card {{ background-color: {COLORS['cyan_bg']};"
                f"border: 1px solid {COLORS['cyan_dim']}; border-radius: 8px; }}"
            )

    def set_value(self, text: str):
        self.value_lbl.setText(text)


class StatsBar(QWidget):
    auto_recommend_requested = pyqtSignal()
    move_duplicates_requested = pyqtSignal()
    delete_duplicates_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transparent")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # KPI cards
        self.card_groups = StatCard(
            "Grupos Duplicados", "fa5s.copy", COLORS["text_main"]
        )
        self.card_files = StatCard(
            "Archivos Analizados", "fa5s.music", COLORS["text_muted"]
        )
        self.card_space = StatCard(
            "Espacio Recuperable", "fa5s.hdd", COLORS["cyan"], large=True
        )

        root.addWidget(self.card_groups, stretch=1)
        root.addWidget(self.card_files, stretch=1)
        root.addWidget(self.card_space, stretch=2)

        # Action buttons column
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)

        self.btn_auto = QPushButton(" Auto-seleccionar")
        self.btn_auto.setObjectName("ghost")
        self.btn_auto.setIcon(qta.icon("fa5s.magic", color=COLORS["text_muted"]))
        self.btn_auto.setFixedHeight(32)
        self.btn_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto.clicked.connect(self.auto_recommend_requested.emit)

        self.btn_move = QPushButton(" Exportar / Mover")
        self.btn_move.setObjectName("ghost")
        self.btn_move.setIcon(qta.icon("fa5s.folder-open", color=COLORS["text_muted"]))
        self.btn_move.setFixedHeight(32)
        self.btn_move.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_move.clicked.connect(self.move_duplicates_requested.emit)

        self.btn_delete = QPushButton(" Eliminar seleccionados")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.setIcon(qta.icon("fa5s.trash-alt", color="white"))
        self.btn_delete.setFixedHeight(32)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.clicked.connect(self.delete_duplicates_requested.emit)

        btn_col.addWidget(self.btn_auto)
        btn_col.addWidget(self.btn_move)
        btn_col.addWidget(self.btn_delete)

        root.addLayout(btn_col)

    def update_stats(self, groups_count: int, files_count: int, space_bytes: float):
        self.card_groups.set_value(f"{groups_count:,}")
        self.card_files.set_value(f"{files_count:,}")

        space_mb = space_bytes / (1024 * 1024)
        if space_mb >= 1024:
            self.card_space.set_value(f"{space_mb / 1024:.1f} GB")
        else:
            self.card_space.set_value(f"{space_mb:.0f} MB")

    def reset(self):
        self.card_groups.set_value("—")
        self.card_files.set_value("—")
        self.card_space.set_value("—")
