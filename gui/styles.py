"""
Modern UI design system, color palette, typography and Qt Style Sheets (QSS).
"""

# Color Palette (Dark Theme / Glassmorphism / Vibrant Accents)
COLORS = {
    "bg_dark": "#0D1117",
    "bg_card": "#161B22",
    "bg_card_highlight": "#1F242C",
    "bg_surface": "#21262D",
    "border": "#30363D",
    
    "primary": "#3B82F6",         # Vibrant Blue
    "primary_hover": "#2563EB",
    "accent": "#8B5CF6",          # Vibrant Purple
    "accent_hover": "#7C3AED",
    
    "success": "#10B981",         # Emerald Green
    "success_hover": "#059669",
    "warning": "#F59E0B",         # Amber
    "warning_hover": "#D97706",
    "danger": "#EF4444",          # Rose Red
    "danger_hover": "#DC2626",
    
    "text_main": "#F3F4F6",
    "text_muted": "#9CA3AF",
    "text_dim": "#6B7280",
    
    # Badge colors for duplicate categories
    "badge_exact_bg": "#1E3A8A",
    "badge_exact_text": "#93C5FD",
    "badge_acoustic_bg": "#064E3B",
    "badge_acoustic_text": "#6EE7B7",
    "badge_possible_bg": "#78350F",
    "badge_possible_text": "#FCD34D",
    
    # Status badges
    "keep_bg": "#064E3B",
    "keep_border": "#10B981",
    "delete_bg": "#7F1D1D",
    "delete_border": "#EF4444",
}

# Global stylesheet for PyQt6
GLOBAL_QSS = f"""
QMainWindow, QWidget#main_window {{
    background-color: {COLORS['bg_dark']};
    color: {COLORS['text_main']};
    font-family: 'Segoe UI';
    font-size: 11pt;
}}

/* Scroll Areas */
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QScrollBar:vertical {{
    background: {COLORS['bg_dark']};
    width: 14px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['bg_surface']};
    min-height: 20px;
    border-radius: 7px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['border']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* Generic Frames */
QFrame {{
    background-color: {COLORS['bg_card']};
    border-radius: 8px;
}}
QFrame#surface {{
    background-color: {COLORS['bg_surface']};
}}
QFrame#transparent {{
    background-color: transparent;
}}

/* Labels */
QLabel {{
    color: {COLORS['text_main']};
    font-family: 'Segoe UI';
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
QLabel#muted {{
    color: {COLORS['text_muted']};
}}
QLabel#dim {{
    color: {COLORS['text_dim']};
}}
QLabel#small {{
    font-size: 9pt;
}}

/* Buttons */
QPushButton {{
    background-color: {COLORS['bg_surface']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 6px 14px;
    font-family: 'Segoe UI';
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {COLORS['border']};
}}
QPushButton:pressed {{
    background-color: {COLORS['bg_card_highlight']};
}}

QPushButton#primary {{
    background-color: {COLORS['primary']};
    color: white;
    border: none;
}}
QPushButton#primary:hover {{
    background-color: {COLORS['primary_hover']};
}}

QPushButton#accent {{
    background-color: {COLORS['accent']};
    color: white;
    border: none;
}}
QPushButton#accent:hover {{
    background-color: {COLORS['accent_hover']};
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
    background-color: {COLORS['warning']};
    color: white;
    border: none;
}}
QPushButton#warning:hover {{
    background-color: {COLORS['warning_hover']};
}}

/* Combo Box */
QComboBox {{
    background-color: {COLORS['bg_surface']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 5px 10px;
}}
QComboBox::drop-down {{
    border: none;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['bg_surface']};
    color: {COLORS['text_main']};
    selection-background-color: {COLORS['primary']};
    border: 1px solid {COLORS['border']};
}}

/* Line Edit / Search */
QLineEdit {{
    background-color: {COLORS['bg_dark']};
    color: {COLORS['text_main']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 6px 12px;
}}
QLineEdit:focus {{
    border: 1px solid {COLORS['primary']};
}}

/* Progress Bar */
QProgressBar {{
    background-color: {COLORS['bg_dark']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    text-align: center;
    color: white;
}}
QProgressBar::chunk {{
    background-color: {COLORS['primary']};
    border-radius: 3px;
}}
"""
