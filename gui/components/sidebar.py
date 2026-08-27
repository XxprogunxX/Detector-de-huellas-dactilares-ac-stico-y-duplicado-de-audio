"""
Sidebar navigation component — Audio Cleaner Figma design.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QProgressBar, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
import qtawesome as qta

from gui.styles import COLORS


class StorageBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transparent")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self.lbl_title = QLabel("Biblioteca")
        self.lbl_title.setObjectName("muted")
        self.lbl_title.setFont(QFont("Segoe UI", 8))

        self.lbl_size = QLabel("— / —")
        self.lbl_size.setObjectName("dim")
        self.lbl_size.setFont(QFont("Segoe UI", 8))
        self.lbl_size.setAlignment(Qt.AlignmentFlag.AlignRight)

        top.addWidget(self.lbl_title)
        top.addStretch()
        top.addWidget(self.lbl_size)
        layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)

    def set_values(self, used_bytes: float, total_bytes: float, label_prefix: str = "Disco"):
        if total_bytes <= 0:
            self.bar.setValue(0)
            self.lbl_size.setText("— / —")
            return
        
        pct = min(100, max(0, int((used_bytes / total_bytes) * 100)))
        self.bar.setValue(pct)
        
        def _fmt(b):
            gb = b / (1024 ** 3)
            if gb >= 1000:
                return f"{gb / 1024:.1f} TB"
            return f"{gb:.1f} GB"

        self.lbl_title.setText(label_prefix)
        self.lbl_size.setText(f"{_fmt(used_bytes)} / {_fmt(total_bytes)}")

    def update_from_folder(self, folder_path: str, music_bytes: float = 0):
        import shutil
        import os
        if folder_path and os.path.exists(folder_path):
            try:
                usage = shutil.disk_usage(folder_path)
                self.set_values(usage.used, usage.total, label_prefix="Almacenamiento")
            except Exception:
                self.set_values(0, 0)
        else:
            self.set_values(0, 0)


class NavButton(QPushButton):
    def __init__(self, icon_name: str, label: str, active: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("nav_item")
        self._label = label
        self._icon_name = icon_name
        self.setText(f"  {label}")
        self.setFixedHeight(40)
        self.setCheckable(True)
        self.setChecked(active)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._on_toggled)
        self._update_icon()

    def _on_toggled(self, checked: bool):
        self._update_icon()

    def set_active(self, active: bool):
        self.setChecked(active)
        self._update_icon()

    def _update_icon(self):
        color = COLORS["cyan"] if self.isChecked() else COLORS["text_muted"]
        self.setIcon(qta.icon(self._icon_name, color=color))



class Sidebar(QWidget):
    nav_changed = pyqtSignal(str)    # emits section name
    folder_requested = pyqtSignal()  # user wants to change folder

    SECTIONS = [
        ("fa5s.music",          "Biblioteca"),
        ("fa5s.search",         "Escaneo"),
        ("fa5s.copy",           "Duplicados"),
        ("fa5s.star",           "Calidad"),
        ("fa5s.cog",            "Configuración"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(220)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Logo ──────────────────────────────────────────────────
        logo_frame = QFrame()
        logo_frame.setObjectName("transparent")
        logo_frame.setFixedHeight(64)
        logo_layout = QHBoxLayout(logo_frame)
        logo_layout.setContentsMargins(16, 0, 16, 0)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon("fa5s.fingerprint", color=COLORS["cyan"]).pixmap(22, 22))
        logo_layout.addWidget(icon_lbl)

        app_name = QLabel("AUDIO CLEANER")
        app_name.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        app_name.setStyleSheet(f"color: {COLORS['text_main']}; letter-spacing: 1px;")
        logo_layout.addWidget(app_name)
        logo_layout.addStretch()

        root.addWidget(logo_frame)

        # Thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {COLORS['border']}; max-height: 1px;")
        root.addWidget(sep)

        root.addSpacing(8)

        # ── Section label ──────────────────────────────────────────
        nav_label = QLabel("  NAVEGACIÓN")
        nav_label.setObjectName("section_label")
        nav_label.setFixedHeight(24)
        root.addWidget(nav_label)

        # ── Nav buttons ────────────────────────────────────────────
        from PyQt6.QtWidgets import QButtonGroup
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)
        self._nav_buttons: dict[str, NavButton] = {}
        self._current_section = "Duplicados"

        for icon_name, label in self.SECTIONS:
            active = (label == self._current_section)
            btn = NavButton(icon_name, label, active=active)
            btn.clicked.connect(lambda checked, s=label: self._on_nav_click(s))
            self.btn_group.addButton(btn)
            self._nav_buttons[label] = btn
            root.addWidget(btn)

        # ── Spacer pushing folder & storage to bottom ──────────────
        root.addStretch()

        # Thin separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background-color: {COLORS['border']}; max-height: 1px; margin: 0 12px;")
        root.addWidget(sep2)

        root.addSpacing(8)

        # ── Folder selector (at bottom above storage bar) ──────────
        folder_label = QLabel("  CARPETA ACTIVA")
        folder_label.setObjectName("section_label")
        folder_label.setFixedHeight(24)
        root.addWidget(folder_label)

        self.lbl_folder = QLabel("Sin seleccionar")
        self.lbl_folder.setObjectName("dim")
        self.lbl_folder.setFont(QFont("Segoe UI", 8))
        self.lbl_folder.setWordWrap(True)
        self.lbl_folder.setContentsMargins(16, 0, 16, 0)
        root.addWidget(self.lbl_folder)

        btn_folder = QPushButton("  Cambiar carpeta")
        btn_folder.setObjectName("ghost")
        btn_folder.setIcon(qta.icon("fa5s.folder-open", color=COLORS["text_muted"]))
        btn_folder.setFixedHeight(34)
        btn_folder.setContentsMargins(12, 0, 12, 0)
        btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_folder.clicked.connect(self.folder_requested.emit)
        root.addWidget(btn_folder)

        root.addSpacing(8)

        # ── Storage bar ────────────────────────────────────────────
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet(f"background-color: {COLORS['border']}; max-height: 1px;")
        root.addWidget(sep3)

        self.storage_bar = StorageBar()
        root.addWidget(self.storage_bar)


        # ── Version tag ────────────────────────────────────────────
        version_frame = QFrame()
        version_frame.setObjectName("transparent")
        version_layout = QHBoxLayout(version_frame)
        version_layout.setContentsMargins(16, 6, 16, 8)

        v_lbl = QLabel("v1.0  ·  Windows")
        v_lbl.setObjectName("dim")
        v_lbl.setFont(QFont("Segoe UI", 7))
        version_layout.addWidget(v_lbl)
        version_layout.addStretch()

        root.addWidget(version_frame)

    def _on_nav_click(self, section: str):
        self._current_section = section
        if section in self._nav_buttons:
            self._nav_buttons[section].setChecked(True)
        self.nav_changed.emit(section)

    def set_active_section(self, section: str):
        if section in self._nav_buttons:
            self._current_section = section
            self._nav_buttons[section].setChecked(True)
            self._nav_buttons[section]._update_icon()

    def set_folder(self, path: str):
        if path:
            display = path if len(path) < 28 else "…" + path[-25:]
            self.lbl_folder.setText(display)
        else:
            self.lbl_folder.setText("Sin seleccionar")

