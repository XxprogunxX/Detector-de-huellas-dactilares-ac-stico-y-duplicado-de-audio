"""
Interactive Duplicate Group Card with side-by-side file comparison and action controls for PyQt6.
"""

import os
from typing import Callable, Optional
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PyQt6.QtCore import Qt

from core.models import DuplicateGroup, AudioTrack, DuplicateType, FileAction
from core.file_manager import open_file_in_explorer
from gui.components.audio_player import AudioPlayer
from gui.styles import COLORS
import qtawesome as qta


class DuplicateGroupCard(QFrame):
    def __init__(
        self,
        group: DuplicateGroup,
        on_action_changed: Optional[Callable] = None,
        parent=None
    ):
        super().__init__(parent)
        self.group = group
        self.on_action_changed = on_action_changed
        self.player = AudioPlayer.get_instance()
        self.track_rows = []
        self._track_widgets = {}
        
        # Connect to global player signals
        self.player.playback_changed.connect(self._on_playback_changed)

        # Card base styling
        self.setObjectName("transparent")  # Parent styling
        self.setStyleSheet(f"""
            DuplicateGroupCard {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """)
        
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # 1. Header Banner
        header = QFrame()
        header.setObjectName("surface")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(10, 8, 10, 8)

        lbl_gid = QLabel(f"Grupo: {self.group.group_id}")
        lbl_gid.setObjectName("subtitle")
        h_layout.addWidget(lbl_gid)

        # Duplicate Type Badge
        badge_bg, badge_fg, badge_text = self._get_badge_style(self.group.primary_type)
        lbl_badge = QLabel(f" {badge_text} ")
        lbl_badge.setStyleSheet(f"""
            background-color: {badge_bg};
            color: {badge_fg};
            font-weight: bold;
            font-size: 9pt;
            border-radius: 4px;
            padding: 2px 6px;
        """)
        h_layout.addWidget(lbl_badge)

        # Similarity
        lbl_sim = QLabel(f"Similitud: {self.group.average_similarity:.1f}%")
        lbl_sim.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold; font-size: 9pt;")
        h_layout.addWidget(lbl_sim)
        
        h_layout.addStretch()

        # Space saving
        savings_mb = self.group.space_saving_bytes / (1024 * 1024)
        lbl_savings = QLabel(f"Ahorro: {savings_mb:.1f} MB")
        lbl_savings.setStyleSheet(f"color: {COLORS['success']}; font-weight: bold; font-size: 9pt;")
        h_layout.addWidget(lbl_savings)

        main_layout.addWidget(header)

        # 2. Recommendation Banner
        recom_frame = QFrame()
        recom_frame.setStyleSheet(f"background-color: {COLORS['bg_card_highlight']}; border-radius: 6px;")
        r_layout = QVBoxLayout(recom_frame)
        r_layout.setContentsMargins(10, 6, 10, 6)
        
        best_filename = os.path.basename(self.group.best_track_path)
        lbl_recom = QLabel(f"RECOMENDACIÓN: Conservar '{best_filename}' — {self.group.best_track_reason}")
        lbl_recom.setWordWrap(True)
        lbl_recom.setStyleSheet(f"color: {COLORS['warning']}; font-size: 9pt;")
        r_layout.addWidget(lbl_recom)
        
        main_layout.addWidget(recom_frame)

        # 3. Track Rows
        max_tracks_to_show = 5
        tracks_to_render = self.group.tracks[:max_tracks_to_show]
        
        for track in tracks_to_render:
            row = self._create_track_row(track)
            main_layout.addWidget(row)
            self.track_rows.append(row)
            
        remaining_tracks = len(self.group.tracks) - max_tracks_to_show
        if remaining_tracks > 0:
            lbl_more = QLabel(f"+ {remaining_tracks} archivo(s) duplicado(s) adicional(es) en este grupo ocultos para mejorar el rendimiento...")
            lbl_more.setObjectName("muted")
            main_layout.addWidget(lbl_more)

    def _get_badge_style(self, dtype: DuplicateType):
        if dtype == DuplicateType.EXACT_HASH:
            return COLORS["badge_exact_bg"], COLORS["badge_exact_text"], "DUPLICADO EXACTO"
        elif dtype == DuplicateType.EXACT_AUDIO:
            return COLORS["badge_exact_bg"], COLORS["badge_exact_text"], "AUDIO PCM EXACTO"
        elif dtype == DuplicateType.ACOUSTIC_DUPLICATE:
            return COLORS["badge_acoustic_bg"], COLORS["badge_acoustic_text"], "DUPLICADO ACÚSTICO"
        else:
            return COLORS["badge_possible_bg"], COLORS["badge_possible_text"], "POSIBLE DUPLICADO"

    def _create_track_row(self, track: AudioTrack) -> QFrame:
        row_frame = QFrame()
        row_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
            }}
        """)
        row_layout = QVBoxLayout(row_frame)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(4)

        # Top section
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        btn_action = QPushButton("CONSERVAR" if track.action == FileAction.KEEP else "ELIMINAR")
        btn_action.setObjectName("success" if track.action == FileAction.KEEP else "danger")
        btn_action.setFixedSize(100, 26)
        # Using lambda with default arguments to capture the correct references
        btn_action.clicked.connect(lambda checked, t=track, btn=btn_action: self._toggle_track_action(t, btn))
        top_layout.addWidget(btn_action)

        lbl_title = QLabel(track.display_title)
        lbl_title.setStyleSheet("font-weight: bold;")
        top_layout.addWidget(lbl_title, stretch=1)
        
        row_layout.addLayout(top_layout)

        # Middle section (Specs and controls)
        mid_layout = QHBoxLayout()
        mid_layout.setContentsMargins(0, 0, 0, 0)
        
        specs_text = (
            f"{track.format}  |  "
            f"{track.bitrate} kbps  |  "
            f"{track.formatted_duration}  |  "
            f"{track.formatted_size}  |  "
            f"{track.samplerate}Hz ({track.bit_depth}-bit)"
        )
        if track.is_fake_lossless:
            specs_text += "  |  Falso FLAC (Corte espectral)"
        elif track.is_lossless:
            specs_text += "  |  Lossless Real"
            
        lbl_specs = QLabel(specs_text)
        lbl_specs.setObjectName("small")
        lbl_specs.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
        mid_layout.addWidget(lbl_specs, stretch=1)

        btn_play = QPushButton(" Escuchar")
        btn_play.setIcon(qta.icon("fa5s.play", color=COLORS["text_main"]))
        btn_play.setFixedHeight(24)
        btn_play.setStyleSheet(f"background-color: {COLORS['bg_card_highlight']}; border: none;")
        btn_play.clicked.connect(lambda checked, p=track.filepath: self._handle_play(p))
        mid_layout.addWidget(btn_play)

        self._track_widgets[track.filepath] = {"row": row_frame, "btn": btn_play}

        btn_folder = QPushButton(" Ubicación")
        btn_folder.setIcon(qta.icon("fa5s.external-link-alt", color=COLORS["text_main"]))
        btn_folder.setFixedHeight(24)
        btn_folder.setStyleSheet(f"background-color: {COLORS['bg_card_highlight']}; border: none;")
        btn_folder.clicked.connect(lambda checked, p=track.filepath, r=row_frame, b=btn_folder: self._handle_open_folder(p, r, b))
        mid_layout.addWidget(btn_folder)
        
        row_layout.addLayout(mid_layout)

        # Bottom section (Path)
        lbl_path = QLabel(f"{track.filepath}")
        lbl_path.setObjectName("dim")
        lbl_path.setStyleSheet("border: none;")
        row_layout.addWidget(lbl_path)

        return row_frame

    def _reset_all_rows_styling(self):
        for row in self.track_rows:
            row.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLORS['bg_surface']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 8px;
                }}
            """)

    def _on_playback_changed(self, filepath: str, is_playing: bool):
        if filepath in self._track_widgets:
            widgets = self._track_widgets[filepath]
            row_frame = widgets["row"]
            btn_play = widgets["btn"]
            
            if is_playing:
                row_frame.setStyleSheet(f"""
                    QFrame {{
                        background-color: {COLORS['bg_surface']};
                        border: 2px solid {COLORS['accent']};
                        border-radius: 8px;
                    }}
                """)
                btn_play.setText(" Reproduciendo...")
                btn_play.setIcon(qta.icon("fa5s.volume-up", color=COLORS["accent"]))
            else:
                self._reset_single_row_styling(row_frame)
                btn_play.setText(" Escuchar")
                btn_play.setIcon(qta.icon("fa5s.play", color=COLORS["text_main"]))

    def _handle_play(self, filepath: str):
        self.player.play(filepath)

    def _handle_open_folder(self, filepath: str, row_frame: QFrame, btn_folder: QPushButton):
        open_file_in_explorer(filepath)
        self._reset_all_rows_styling()
        row_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_surface']};
                border: 2px solid {COLORS['primary']};
                border-radius: 8px;
            }}
        """)
        original_text = btn_folder.text()
        btn_folder.setText(" Abierto")
        btn_folder.setIcon(qta.icon("fa5s.check", color=COLORS["primary"]))
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: btn_folder.setText(original_text))
        QTimer.singleShot(2000, lambda: btn_folder.setIcon(qta.icon("fa5s.external-link-alt", color=COLORS["text_main"])))
        QTimer.singleShot(2000, lambda: self._reset_single_row_styling(row_frame))

    def _reset_single_row_styling(self, row_frame: QFrame):
        row_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
            }}
        """)

    def _toggle_track_action(self, track: AudioTrack, btn: QPushButton):
        if track.action == FileAction.KEEP:
            track.action = FileAction.DELETE
            btn.setText("ELIMINAR")
            btn.setObjectName("danger")
        else:
            track.action = FileAction.KEEP
            btn.setText("CONSERVAR")
            btn.setObjectName("success")
            
        # Re-apply stylesheet to force color change from objectName
        btn.setStyleSheet("")

        self.group.recalculate_space_saving()
        if self.on_action_changed:
            self.on_action_changed(self.group)
