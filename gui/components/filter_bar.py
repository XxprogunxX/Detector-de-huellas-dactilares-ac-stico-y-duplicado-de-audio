"""
Filter, Search, Sort and Batch Action Bar component for PyQt6.
"""

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QComboBox, QLabel, QButtonGroup
)
from PyQt6.QtCore import Qt, QTimer
import qtawesome as qta


class FilterBar(QFrame):
    def __init__(
        self,
        parent=None,
        on_filter_changed=None,
        on_sort_changed=None,
        on_search_changed=None,
        on_auto_recommend=None,
        on_move_duplicates=None,
        on_delete_duplicates=None,
    ):
        super().__init__(parent)
        self.setObjectName("transparent")
        
        self.on_filter_changed = on_filter_changed
        self.on_sort_changed = on_sort_changed
        self.on_search_changed = on_search_changed
        self.on_auto_recommend = on_auto_recommend
        self.on_move_duplicates = on_move_duplicates
        self.on_delete_duplicates = on_delete_duplicates

        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Row 1: Filter tabs and Search
        row1 = QFrame()
        row1.setObjectName("transparent")
        r1_layout = QHBoxLayout(row1)
        r1_layout.setContentsMargins(14, 12, 14, 6)
        r1_layout.setSpacing(10)

        # Segmented Filter (ButtonGroup)
        self.filter_group = QButtonGroup(self)
        self.btn_all = QPushButton("Todos")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        self.btn_exact = QPushButton("Exactos")
        self.btn_exact.setCheckable(True)
        self.btn_acoustic = QPushButton("Acústicos")
        self.btn_acoustic.setCheckable(True)
        self.btn_possible = QPushButton("Posibles")
        self.btn_possible.setCheckable(True)

        self.filter_group.addButton(self.btn_all, 0)
        self.filter_group.addButton(self.btn_exact, 1)
        self.filter_group.addButton(self.btn_acoustic, 2)
        self.filter_group.addButton(self.btn_possible, 3)
        self.filter_group.buttonClicked.connect(self._handle_filter)

        r1_layout.addWidget(self.btn_all)
        r1_layout.addWidget(self.btn_exact)
        r1_layout.addWidget(self.btn_acoustic)
        r1_layout.addWidget(self.btn_possible)

        # Search Entry
        self.entry_search = QLineEdit()
        self.entry_search.setPlaceholderText("Buscar por título, artista, archivo...")
        self.entry_search.setFixedHeight(32)
        
        # Debounce Timer for Search
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._fire_search)
        self.entry_search.textChanged.connect(lambda: self.search_timer.start())
        
        r1_layout.addWidget(self.entry_search, stretch=1)

        # Sort Combo
        self.combo_sort = QComboBox()
        self.combo_sort.addItems([
            "Mayor Ahorro de Espacio",
            "Mayor Similitud",
            "Nombre / Artista",
            "Más Archivos por Grupo"
        ])
        self.combo_sort.setFixedHeight(32)
        self.combo_sort.setFixedWidth(190)
        self.combo_sort.currentTextChanged.connect(self._handle_sort)
        r1_layout.addWidget(self.combo_sort)

        main_layout.addWidget(row1)

        # Row 2: Batch action buttons
        row2 = QFrame()
        row2.setObjectName("transparent")
        r2_layout = QHBoxLayout(row2)
        r2_layout.setContentsMargins(14, 0, 14, 12)
        r2_layout.setSpacing(8)

        self.btn_auto = QPushButton(" Auto-Conservar Mejor Calidad")
        self.btn_auto.setIcon(qta.icon("fa5s.magic", color="white"))
        self.btn_auto.setObjectName("accent")
        self.btn_auto.setFixedHeight(30)
        self.btn_auto.clicked.connect(self._trigger_auto)
        r2_layout.addWidget(self.btn_auto)

        self.btn_move = QPushButton(" Mover Marcados a Carpeta...")
        self.btn_move.setIcon(qta.icon("fa5s.folder", color="white"))
        self.btn_move.setObjectName("primary")
        self.btn_move.setFixedHeight(30)
        self.btn_move.clicked.connect(self._trigger_move)
        r2_layout.addWidget(self.btn_move)

        self.btn_delete = QPushButton(" Eliminar Marcados...")
        self.btn_delete.setIcon(qta.icon("fa5s.trash", color="white"))
        self.btn_delete.setObjectName("danger")
        self.btn_delete.setFixedHeight(30)
        self.btn_delete.clicked.connect(self._trigger_delete)
        r2_layout.addWidget(self.btn_delete)

        r2_layout.addStretch()

        self.lbl_filter_count = QLabel("0 grupos mostrados")
        self.lbl_filter_count.setObjectName("dim")
        r2_layout.addWidget(self.lbl_filter_count)

        main_layout.addWidget(row2)

    def update_counts(self, counts: dict, total_displayed: int):
        """Updates tab label counters."""
        all_c = counts.get("all", 0)
        exact_c = counts.get("exact", 0)
        acoust_c = counts.get("acoustic", 0)
        poss_c = counts.get("possible", 0)

        # Update button texts
        self.btn_all.setText(f"Todos ({all_c})")
        self.btn_exact.setText(f"Exactos ({exact_c})")
        self.btn_acoustic.setText(f"Acústicos ({acoust_c})")
        self.btn_possible.setText(f"Posibles ({poss_c})")
        
        self.lbl_filter_count.setText(f"{total_displayed} grupo(s) mostrado(s)")

    def _handle_filter(self, button):
        if self.on_filter_changed:
            val = button.text()
            if "Exactos" in val:
                f_type = "exact"
            elif "Acústicos" in val:
                f_type = "acoustic"
            elif "Posibles" in val:
                f_type = "possible"
            else:
                f_type = "all"
            self.on_filter_changed(f_type)

    def _fire_search(self):
        if self.on_search_changed:
            self.on_search_changed(self.entry_search.text().strip().lower())

    def _handle_sort(self, val: str):
        if self.on_sort_changed:
            self.on_sort_changed(val)

    def _trigger_auto(self):
        if self.on_auto_recommend:
            self.on_auto_recommend()

    def _trigger_move(self):
        if self.on_move_duplicates:
            self.on_move_duplicates()

    def _trigger_delete(self):
        if self.on_delete_duplicates:
            self.on_delete_duplicates()
