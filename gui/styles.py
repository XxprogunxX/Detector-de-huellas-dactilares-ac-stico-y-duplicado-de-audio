"""
Modern UI Design System — Audio Cleaner
Based on the Figma spec with 4-tier dark palette and Cyan accent.
"""

# ─── Color Palette (Figma spec) ─────────────────────────────────────────────
COLORS = {
    # Background tiers
    "bg_darkest":   "#0D0F12",   # Window / deepest background
    "bg_sidebar":   "#14161A",   # Left sidebar
    "bg_main":      "#1C1F25",   # Main panel background
    "bg_card":      "#252830",   # Cards / floating surfaces
    "bg_hover":     "#2E3340",   # Hover state for list items

    # Legacy aliases (keep for compatibility)
    "bg_dark":          "#0D0F12",
    "bg_card_highlight": "#2E3340",
    "bg_surface":       "#252830",
    "border":           "#2E3340",

    # Accent
    "cyan":         "#00E5FF",
    "cyan_dim":     "#0099B8",
    "cyan_bg":      "#003340",

    # Primary (keep for btn_scan etc.)
    "primary":       "#00E5FF",
    "primary_hover": "#00B8CC",

    # Accent purple (kept for filter_bar etc.)
    "accent":       "#7C3AED",
    "accent_hover": "#6D28D9",

    # Semantic
    "success":        "#22C55E",   # MEJOR CALIDAD
    "success_bg":     "#052E16",
    "success_hover":  "#16A34A",
    "warning":        "#F97316",   # FAKE LOSSLESS / ADVERTENCIA
    "warning_bg":     "#431407",
    "warning_hover":  "#EA580C",
    "danger":         "#EF4444",   # TRANSCODIFICADO / ELIMINAR PERMANENTE
    "danger_bg":      "#450A0A",
    "danger_hover":   "#DC2626",
    "info":           "#3B82F6",   # ACÚSTICO / INFO
    "info_bg":        "#1E3A8A",
    "purple":         "#A855F7",   # POSIBLE
    "purple_bg":      "#3B0764",

    # Text
    "text_main":  "#F3F4F6",
    "text_muted": "#9CA3AF",
    "text_dim":   "#6B7280",

    # Badge colors (kept for duplicate_card compatibility)
    "badge_exact_bg":     "#003340",
    "badge_exact_text":   "#00E5FF",
    "badge_acoustic_bg":  "#1E3A8A",
    "badge_acoustic_text": "#93C5FD",
    "badge_possible_bg":  "#3B0764",
    "badge_possible_text": "#D8B4FE",
    "keep_bg":     "#052E16",
    "keep_border": "#22C55E",
    "delete_bg":   "#450A0A",
    "delete_border": "#EF4444",
}

# ─── Global QSS ─────────────────────────────────────────────────────────────
GLOBAL_QSS = f"""
/* ── Window base ─────────────────────────────────────────────── */
QMainWindow, QWidget#main_window {{
    background-color: {COLORS['bg_darkest']};
    color: {COLORS['text_main']};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 11pt;
}}
QWidget {{
    font-family: 'Segoe UI', 'Inter', sans-serif;
    color: {COLORS['text_main']};
}}

/* ── Sidebar ─────────────────────────────────────────────────── */
QWidget#sidebar {{
    background-color: {COLORS['bg_sidebar']};
    border-right: 1px solid {COLORS['border']};
}}

/* ── Main panel ──────────────────────────────────────────────── */
QWidget#main_panel {{
    background-color: {COLORS['bg_main']};
}}

/* ── Scroll Areas ────────────────────────────────────────────── */
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['bg_card']};
    min-height: 24px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['bg_hover']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* ── Generic Frames ──────────────────────────────────────────── */
QFrame {{
    background-color: transparent;
    border-radius: 8px;
}}
QFrame#card {{
    background-color: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
}}
QFrame#transparent {{
    background-color: transparent;
    border: none;
}}

/* ── Labels ──────────────────────────────────────────────────── */
QLabel {{
    color: {COLORS['text_main']};
    background-color: transparent;
}}
QLabel#title {{
    font-size: 16pt;
    font-weight: bold;
}}
QLabel#subtitle {{
    font-size: 13pt;
    font-weight: bold;
}}
QLabel#section_label {{
    font-size: 8pt;
    font-weight: bold;
    color: {COLORS['text_dim']};
    letter-spacing: 1.5px;
}}
QLabel#muted {{
    color: {COLORS['text_muted']};
}}
QLabel#dim {{
    color: {COLORS['text_dim']};
}}
QLabel#small {{
    font-size: 9pt;
}}
QLabel#mono {{
    font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
    font-size: 9pt;
    color: {COLORS['text_muted']};
}}
QLabel#cyan {{
    color: {COLORS['cyan']};
    font-weight: bold;
}}

/* ── Buttons ─────────────────────────────────────────────────── */
QPushButton {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {COLORS['bg_hover']};
    border-color: {COLORS['text_dim']};
}}
QPushButton:pressed {{
    background-color: {COLORS['bg_sidebar']};
}}
QPushButton:disabled {{
    opacity: 0.45;
}}

QPushButton#primary {{
    background-color: {COLORS['cyan']};
    color: #000000;
    border: none;
    font-weight: 700;
}}
QPushButton#primary:hover {{
    background-color: {COLORS['cyan_dim']};
}}

QPushButton#success {{
    background-color: {COLORS['success']};
    color: white;
    border: none;
}}
QPushButton#success:hover {{
    background-color: {COLORS['success_hover']};
}}

QPushButton#danger {{
    background-color: {COLORS['danger']};
    color: white;
    border: none;
}}
QPushButton#danger:hover {{
    background-color: {COLORS['danger_hover']};
}}

QPushButton#warning {{
    background-color: transparent;
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
}}
QPushButton#warning:hover {{
    background-color: {COLORS['bg_hover']};
}}

QPushButton#ghost {{
    background-color: transparent;
    color: {COLORS['text_muted']};
    border: 1px solid {COLORS['border']};
}}
QPushButton#ghost:hover {{
    color: {COLORS['text_main']};
    background-color: {COLORS['bg_card']};
}}

/* Sidebar nav buttons */
QPushButton#nav_item {{
    background-color: transparent;
    color: {COLORS['text_muted']};
    border: none;
    border-radius: 6px;
    text-align: left;
    padding: 10px 12px;
    font-weight: 500;
}}
QPushButton#nav_item:hover {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_main']};
}}
QPushButton#nav_item:checked, QPushButton#nav_item_active {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['cyan']};
    border: none;
    border-left: 3px solid {COLORS['cyan']};
    border-radius: 0px 6px 6px 0px;
    text-align: left;
    padding: 10px 12px 10px 9px;
    font-weight: 700;
}}



/* ── Progress Bar ────────────────────────────────────────────── */
QProgressBar {{
    background-color: {COLORS['bg_card']};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {COLORS['cyan']};
    border-radius: 3px;
}}

/* ── Line Edit / Search ──────────────────────────────────────── */
QLineEdit {{
    background-color: {COLORS['bg_darkest']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 7px 12px;
    font-family: 'Segoe UI';
}}
QLineEdit:focus {{
    border: 1px solid {COLORS['cyan']};
}}

/* ── Combo Box ───────────────────────────────────────────────── */
QComboBox {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 6px 10px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_main']};
    selection-background-color: {COLORS['cyan_bg']};
    border: 1px solid {COLORS['border']};
    outline: none;
}}

/* ── Tab Bar (filter tabs) ───────────────────────────────────── */
QTabBar::tab {{
    background: transparent;
    color: {COLORS['text_muted']};
    padding: 7px 18px;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: {COLORS['cyan']};
    border-bottom: 2px solid {COLORS['cyan']};
}}
QTabBar::tab:hover {{
    color: {COLORS['text_main']};
}}
QTabWidget::pane {{
    border: none;
}}

/* ── Table Widget ─────────────────────────────────────────────── */
QTableWidget, QTableView {{
    background-color: {COLORS['bg_main']};
    color: {COLORS['text_main']};
    gridline-color: {COLORS['border']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    selection-background-color: {COLORS['cyan_bg']};
    selection-color: {COLORS['text_main']};
    font-size: 10pt;
}}
QTableWidget::item, QTableView::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {COLORS['border']};
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {COLORS['cyan_bg']};
    color: {COLORS['cyan']};
}}
QHeaderView::section {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_muted']};
    padding: 8px 10px;
    font-weight: 700;
    font-size: 8.5pt;
    border: none;
    border-bottom: 2px solid {COLORS['border']};
    letter-spacing: 1px;
}}

/* ── Checkbox & Radio ────────────────────────────────────────── */
QCheckBox {{
    color: {COLORS['text_main']};
    spacing: 8px;
    font-size: 10pt;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid {COLORS['border']};
    background-color: {COLORS['bg_darkest']};
}}
QCheckBox::indicator:hover {{
    border-color: {COLORS['cyan_dim']};
}}
QCheckBox::indicator:checked {{
    background-color: {COLORS['cyan']};
    border-color: {COLORS['cyan']};
}}

/* ── SpinBox & Slider ────────────────────────────────────────── */
QSpinBox, QDoubleSpinBox {{
    background-color: {COLORS['bg_darkest']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 5px 10px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {COLORS['cyan']};
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: {COLORS['bg_card']};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {COLORS['cyan']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {COLORS['cyan']};
    border: 2px solid #FFFFFF;
    width: 16px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: #FFFFFF;
}}

/* ── Tooltips ────────────────────────────────────────────────── */
QToolTip {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 4px 8px;
}}

/* ── Message Box ─────────────────────────────────────────────── */
QMessageBox {{
    background-color: {COLORS['bg_card']};
}}
QMessageBox QLabel {{
    color: {COLORS['text_main']};
}}
"""
