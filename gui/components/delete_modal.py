"""
Delete Modal — Secure deletion confirmation overlay (Figma Phase B).

3 options:
  1. Mover a carpeta de backup
  2. Mover a la Papelera del sistema
  3. Eliminar permanentemente (danger, requires confirmation)

Also includes: CSV report checkbox.
"""

import os
import csv
import tempfile
from typing import List, Callable, Optional
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QButtonGroup, QRadioButton,
    QCheckBox, QFileDialog, QGraphicsOpacityEffect
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPainter
import qtawesome as qta

from gui.styles import COLORS
from core.models import DuplicateGroup, FileAction


class _OptionCard(QFrame):
    """A selectable option card with radio button."""

    clicked_signal = pyqtSignal()

    def __init__(
        self,
        icon: str,
        icon_color: str,
        title: str,
        description: str,
        danger: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self._danger = danger
        self._selected = False
        self._update_style(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        self.radio = QRadioButton()
        self.radio.setStyleSheet(f"""
            QRadioButton::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {COLORS['border']};
                border-radius: 9px;
                background: {COLORS['bg_darkest']};
            }}
            QRadioButton::indicator:checked {{
                background: {icon_color};
                border-color: {icon_color};
            }}
        """)
        layout.addWidget(self.radio)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon(icon, color=icon_color).pixmap(20, 20))
        layout.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {'#EF4444' if danger else COLORS['text_main']};")
        text_col.addWidget(title_lbl)

        desc_lbl = QLabel(description)
        desc_lbl.setFont(QFont("Segoe UI", 8))
        desc_lbl.setStyleSheet(f"color: {COLORS['text_muted']};")
        text_col.addWidget(desc_lbl)

        layout.addLayout(text_col, stretch=1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _update_style(self, selected: bool):
        if selected:
            border_col = COLORS["danger"] if self._danger else COLORS["cyan"]
            bg = COLORS["danger_bg"] if self._danger else COLORS["cyan_bg"]
        else:
            border_col = COLORS["border"]
            bg = COLORS["bg_card"]
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {border_col};"
            f"border-radius: 8px; }}"
        )

    def set_selected(self, selected: bool):
        self._selected = selected
        self.radio.setChecked(selected)
        self._update_style(selected)

    def mousePressEvent(self, event):
        self.radio.setChecked(True)
        self.clicked_signal.emit()
        super().mousePressEvent(event)


class DeleteModal(QDialog):
    """
    Modal overlay for safe file deletion — 3 options + CSV checkbox.

    Usage:
        modal = DeleteModal(groups, parent=window)
        if modal.exec() == QDialog.DialogCode.Accepted:
            modal.execute_action()
    """

    def __init__(self, groups: List[DuplicateGroup], parent=None):
        super().__init__(parent)
        self.groups = groups
        self._selected_mode = "trash"   # "backup" | "trash" | "permanent"
        self._backup_folder: Optional[str] = None
        self._export_csv = False

        # Count files to delete
        self._files_to_delete = [
            t for g in groups for t in g.tracks
            if t.action == FileAction.DELETE
        ]

        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(520, 560)

        self._build_ui()

    def _build_ui(self):
        # Outer layout (for the semi-transparent overlay)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_card']};"
            f"border: 1px solid {COLORS['border']}; border-radius: 12px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(20)

        # ── Header ────────────────────────────────────────────────
        header_row = QHBoxLayout()

        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon("fa5s.trash-alt", color=COLORS["danger"]).pixmap(24, 24))
        header_row.addWidget(icon_lbl)

        header_text = QVBoxLayout()
        header_text.setSpacing(2)

        title = QLabel("Eliminar archivos seleccionados")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {COLORS['text_main']};")
        header_text.addWidget(title)

        n = len(self._files_to_delete)
        sub = QLabel(f"{n} archivo{'s' if n != 1 else ''} marcado{'s' if n != 1 else ''} para eliminar")
        sub.setFont(QFont("Segoe UI", 9))
        sub.setStyleSheet(f"color: {COLORS['text_muted']};")
        header_text.addWidget(sub)

        header_row.addLayout(header_text)
        header_row.addStretch()

        btn_close = QPushButton()
        btn_close.setIcon(qta.icon("fa5s.times", color=COLORS["text_muted"]))
        btn_close.setObjectName("ghost")
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
        header_row.addWidget(btn_close)

        card_layout.addLayout(header_row)

        # Thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {COLORS['border']}; max-height: 1px; border: none;")
        card_layout.addWidget(sep)

        # ── Option label ──────────────────────────────────────────
        opt_lbl = QLabel("SELECCIONA EL MÉTODO DE ELIMINACIÓN")
        opt_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        opt_lbl.setStyleSheet(f"color: {COLORS['text_dim']}; letter-spacing: 1px;")
        card_layout.addWidget(opt_lbl)

        # ── Options ───────────────────────────────────────────────
        self._opt_backup = _OptionCard(
            "fa5s.box",
            COLORS["info"],
            "Mover a carpeta de backup",
            "Los archivos se moverán a una carpeta segura elegida por ti."
        )
        self._opt_trash = _OptionCard(
            "fa5s.recycle",
            COLORS["warning"],
            "Mover a la Papelera del sistema",
            "Los archivos irán a la Papelera. Puedes restaurarlos después."
        )
        self._opt_trash.set_selected(True)   # Default
        self._opt_permanent = _OptionCard(
            "fa5s.exclamation-triangle",
            COLORS["danger"],
            "Eliminar permanentemente",
            "Acción irreversible. Los archivos no se podrán recuperar.",
            danger=True
        )

        options = [self._opt_backup, self._opt_trash, self._opt_permanent]
        for opt in options:
            card_layout.addWidget(opt)

        self._opt_backup.clicked_signal.connect(lambda: self._select("backup"))
        self._opt_trash.clicked_signal.connect(lambda: self._select("trash"))
        self._opt_permanent.clicked_signal.connect(lambda: self._select("permanent"))

        # Backup folder selector (hidden by default)
        self._backup_row = QFrame()
        self._backup_row.setObjectName("transparent")
        self._backup_row.hide()
        backup_layout = QHBoxLayout(self._backup_row)
        backup_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_backup_path = QLabel("Sin seleccionar")
        self.lbl_backup_path.setFont(QFont("JetBrains Mono", 8))
        self.lbl_backup_path.setStyleSheet(f"color: {COLORS['text_muted']};")
        backup_layout.addWidget(self.lbl_backup_path, stretch=1)

        btn_pick = QPushButton(" Elegir carpeta")
        btn_pick.setObjectName("ghost")
        btn_pick.setIcon(qta.icon("fa5s.folder-open", color=COLORS["text_muted"]))
        btn_pick.setFixedHeight(30)
        btn_pick.clicked.connect(self._pick_backup_folder)
        backup_layout.addWidget(btn_pick)

        card_layout.addWidget(self._backup_row)

        # ── CSV Checkbox ──────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background-color: {COLORS['border']}; max-height: 1px; border: none;")
        card_layout.addWidget(sep2)

        self.chk_csv = QCheckBox("  Generar reporte CSV después de la eliminación")
        self.chk_csv.setStyleSheet(f"""
            QCheckBox {{ color: {COLORS['text_muted']}; font-size: 9pt; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 2px solid {COLORS['border']};
                border-radius: 4px;
                background: {COLORS['bg_darkest']};
            }}
            QCheckBox::indicator:checked {{
                background: {COLORS['cyan']};
                border-color: {COLORS['cyan']};
            }}
        """)
        card_layout.addWidget(self.chk_csv)

        # ── Action buttons ────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("ghost")
        btn_cancel.setFixedHeight(38)
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_row.addStretch()

        self.btn_confirm = QPushButton("  Confirmar eliminación")
        self.btn_confirm.setObjectName("danger")
        self.btn_confirm.setIcon(qta.icon("fa5s.check", color="white"))
        self.btn_confirm.setFixedHeight(38)
        self.btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(self.btn_confirm)

        card_layout.addLayout(btn_row)

        outer.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)

    # ─────────────────────────────────────────────────────────────
    def _select(self, mode: str):
        self._selected_mode = mode
        self._opt_backup.set_selected(mode == "backup")
        self._opt_trash.set_selected(mode == "trash")
        self._opt_permanent.set_selected(mode == "permanent")

        # Show backup folder picker only when "backup" is selected
        self._backup_row.setVisible(mode == "backup")

        # Update confirm button label
        labels = {
            "backup":    "  Mover a backup",
            "trash":     "  Mover a Papelera",
            "permanent": "  Eliminar permanentemente",
        }
        self.btn_confirm.setText(labels[mode])

    def _pick_backup_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de backup")
        if folder:
            self._backup_folder = folder
            short = folder if len(folder) < 40 else "…" + folder[-37:]
            self.lbl_backup_path.setText(short)

    def _on_confirm(self):
        if self._selected_mode == "backup" and not self._backup_folder:
            self.lbl_backup_path.setStyleSheet(f"color: {COLORS['danger']};")
            self.lbl_backup_path.setText("⚠  Selecciona una carpeta primero")
            return
        self._export_csv = self.chk_csv.isChecked()
        self.accept()

    # ── Public API ────────────────────────────────────────────────
    def execute_action(self) -> tuple[int, int, list[str]]:
        """
        Execute the selected deletion mode on marked files.
        Returns (success_count, failed_count, log_lines).
        """
        from core.file_manager import (
            move_marked_duplicates,
            delete_marked_duplicates_permanently,
        )
        import send2trash

        success = 0
        failed = 0
        logs: list[str] = []

        if self._selected_mode == "backup" and self._backup_folder:
            success, failed, logs = move_marked_duplicates(self.groups, self._backup_folder)

        elif self._selected_mode == "trash":
            for t in self._files_to_delete:
                try:
                    send2trash.send2trash(t.filepath)
                    logs.append(f"[OK] Papelera ← {t.filepath}")
                    success += 1
                except Exception as e:
                    logs.append(f"[ERR] {t.filepath}: {e}")
                    failed += 1

        elif self._selected_mode == "permanent":
            success, failed, logs = delete_marked_duplicates_permanently(self.groups)

        if self._export_csv:
            self._write_csv(logs)

        return success, failed, logs

    def _write_csv(self, logs: list[str]):
        try:
            csv_path = os.path.join(
                os.path.expanduser("~"), "Desktop", "audio_cleaner_report.csv"
            )
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Acción", "Archivo"])
                for line in logs:
                    tag = line[:5].strip("[] ")
                    rest = line[6:] if len(line) > 6 else line
                    writer.writerow([tag, rest])
        except Exception:
            pass
