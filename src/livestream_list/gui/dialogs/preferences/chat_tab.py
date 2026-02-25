"""Chat tab for Preferences dialog."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ....core.models import StreamPlatform

if TYPE_CHECKING:
    from .dialog import PreferencesDialog

logger = logging.getLogger(__name__)


class ChatTab(QScrollArea):
    """Chat settings tab."""

    def __init__(self, dialog: PreferencesDialog, parent: QWidget | None = None):
        super().__init__(parent)
        self.dialog = dialog
        self.app = dialog.app
        self._setup_ui()

    @property
    def _loading(self) -> bool:
        return self.dialog._loading

    def _setup_ui(self) -> None:
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Chat group
        chat_group = QGroupBox("Chat")
        chat_layout = QFormLayout(chat_group)

        self.chat_auto_cb = QCheckBox("Auto-open when launching stream")
        self.chat_auto_cb.setChecked(self.app.settings.chat.auto_open)
        self.chat_auto_cb.stateChanged.connect(self._on_chat_changed)
        chat_layout.addRow(self.chat_auto_cb)

        layout.addWidget(chat_group)

        # Chat Client group
        client_group = QGroupBox("Chat Client")
        client_layout = QFormLayout(client_group)

        # Chat client type dropdown
        self.chat_client_combo = QComboBox()
        self.chat_client_combo.addItem("Browser", "browser")
        self.chat_client_combo.addItem("Built-in", "builtin")

        current_mode = self.app.settings.chat.mode
        for i in range(self.chat_client_combo.count()):
            if self.chat_client_combo.itemData(i) == current_mode:
                self.chat_client_combo.setCurrentIndex(i)
                break

        self.chat_client_combo.currentIndexChanged.connect(self._on_chat_client_changed)
        client_layout.addRow("Client:", self.chat_client_combo)

        # Browser selection (shown when Browser client is selected)
        self.browser_combo = QComboBox()
        self.browser_combo.addItem("System Default", "default")
        self.browser_combo.addItem("Chrome", "chrome")
        self.browser_combo.addItem("Chromium", "chromium")
        self.browser_combo.addItem("Firefox", "firefox")
        self.browser_combo.addItem("Edge", "edge")

        current_browser = self.app.settings.chat.browser
        for i in range(self.browser_combo.count()):
            if self.browser_combo.itemData(i) == current_browser:
                self.browser_combo.setCurrentIndex(i)
                break

        self.browser_combo.currentIndexChanged.connect(self._on_chat_changed)
        self.browser_label = QLabel("Browser:")
        client_layout.addRow(self.browser_label, self.browser_combo)

        # Open in new window checkbox (for browser client)
        self.new_window_cb = QCheckBox("Open in new window")
        self.new_window_cb.setChecked(self.app.settings.chat.new_window)
        self.new_window_cb.stateChanged.connect(self._on_chat_changed)
        client_layout.addRow(self.new_window_cb)

        layout.addWidget(client_group)

        # Built-in chat settings group (shown when Built-in is selected)
        self.builtin_group = QGroupBox("Built-in Chat Settings")
        builtin_layout = QFormLayout(self.builtin_group)

        self.chat_font_spin = QSpinBox()
        self.chat_font_spin.setRange(4, 24)
        self.chat_font_spin.setValue(self.app.settings.chat.builtin.font_size)
        self.chat_font_spin.valueChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Font size:", self.chat_font_spin)

        self.chat_spacing_spin = QSpinBox()
        self.chat_spacing_spin.setRange(0, 12)
        self.chat_spacing_spin.setSuffix(" px")
        self.chat_spacing_spin.setValue(self.app.settings.chat.builtin.line_spacing)
        self.chat_spacing_spin.valueChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Line spacing:", self.chat_spacing_spin)

        self.scrollback_spin = QSpinBox()
        self.scrollback_spin.setRange(100, 50000)
        self.scrollback_spin.setSingleStep(100)
        self.scrollback_spin.setSuffix(" messages")
        self.scrollback_spin.setValue(self.app.settings.chat.builtin.max_messages)
        self.scrollback_spin.valueChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Scrollback buffer:", self.scrollback_spin)

        # Emote provider checkboxes
        emote_providers = self.app.settings.chat.builtin.emote_providers
        self.emote_7tv_cb = QCheckBox("7TV")
        self.emote_7tv_cb.setChecked("7tv" in emote_providers)
        self.emote_7tv_cb.stateChanged.connect(self._on_chat_changed)
        self.emote_bttv_cb = QCheckBox("BTTV")
        self.emote_bttv_cb.setChecked("bttv" in emote_providers)
        self.emote_bttv_cb.stateChanged.connect(self._on_chat_changed)
        self.emote_ffz_cb = QCheckBox("FFZ")
        self.emote_ffz_cb.setChecked("ffz" in emote_providers)
        self.emote_ffz_cb.stateChanged.connect(self._on_chat_changed)

        emote_row = QHBoxLayout()
        emote_row.addWidget(self.emote_7tv_cb)
        emote_row.addWidget(self.emote_bttv_cb)
        emote_row.addWidget(self.emote_ffz_cb)
        emote_row.addStretch()
        builtin_layout.addRow("Emote providers:", emote_row)

        self.chat_timestamps_cb = QCheckBox("Show timestamps")
        self.chat_timestamps_cb.setChecked(self.app.settings.chat.builtin.show_timestamps)
        self.chat_timestamps_cb.stateChanged.connect(self._on_chat_changed)

        self.chat_ts_format_combo = QComboBox()
        self.chat_ts_format_combo.addItem("24-hour", "24h")
        self.chat_ts_format_combo.addItem("12-hour", "12h")
        current_fmt = self.app.settings.chat.builtin.timestamp_format
        self.chat_ts_format_combo.setCurrentIndex(1 if current_fmt == "12h" else 0)
        self.chat_ts_format_combo.currentIndexChanged.connect(self._on_chat_changed)

        ts_row = QHBoxLayout()
        ts_row.addWidget(self.chat_timestamps_cb)
        ts_row.addWidget(self.chat_ts_format_combo)
        ts_row.addStretch()
        builtin_layout.addRow(ts_row)

        self.chat_name_colors_cb = QCheckBox("Use platform name colors")
        self.chat_name_colors_cb.setChecked(self.app.settings.chat.builtin.use_platform_name_colors)
        self.chat_name_colors_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_name_colors_cb)

        self.chat_badges_cb = QCheckBox("Show badges")
        self.chat_badges_cb.setChecked(self.app.settings.chat.builtin.show_badges)
        self.chat_badges_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_badges_cb)

        self.chat_mod_badges_cb = QCheckBox("Show mod/VIP badges")
        self.chat_mod_badges_cb.setChecked(self.app.settings.chat.builtin.show_mod_badges)
        self.chat_mod_badges_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_mod_badges_cb)

        self.chat_emotes_cb = QCheckBox("Show emotes")
        self.chat_emotes_cb.setChecked(self.app.settings.chat.builtin.show_emotes)
        self.chat_emotes_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_emotes_cb)

        self.chat_animate_emotes_cb = QCheckBox("Animate emotes")
        self.chat_animate_emotes_cb.setChecked(self.app.settings.chat.builtin.animate_emotes)
        self.chat_animate_emotes_cb.setEnabled(self.app.settings.chat.builtin.show_emotes)
        self.chat_animate_emotes_cb.stateChanged.connect(self._on_chat_changed)
        self.chat_emotes_cb.stateChanged.connect(
            lambda state: self.chat_animate_emotes_cb.setEnabled(bool(state))
        )
        builtin_layout.addRow(self.chat_animate_emotes_cb)

        self.chat_alt_rows_cb = QCheckBox("Alternating row colors")
        self.chat_alt_rows_cb.setChecked(self.app.settings.chat.builtin.show_alternating_rows)
        self.chat_alt_rows_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_alt_rows_cb)

        self.chat_metrics_cb = QCheckBox("Show metrics in status bar")
        self.chat_metrics_cb.setChecked(self.app.settings.chat.builtin.show_metrics)
        self.chat_metrics_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_metrics_cb)

        self.chat_spellcheck_cb = QCheckBox("Enable spellcheck")
        self.chat_spellcheck_cb.setChecked(self.app.settings.chat.builtin.spellcheck_enabled)
        self.chat_spellcheck_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_spellcheck_cb)

        self.chat_autocorrect_cb = QCheckBox("Auto-correct misspelled words")
        self.chat_autocorrect_cb.setChecked(self.app.settings.chat.builtin.autocorrect_enabled)
        self.chat_autocorrect_cb.setEnabled(self.app.settings.chat.builtin.spellcheck_enabled)
        self.chat_autocorrect_cb.stateChanged.connect(self._on_chat_changed)
        self.chat_spellcheck_cb.stateChanged.connect(
            lambda state: self.chat_autocorrect_cb.setEnabled(bool(state))
        )
        builtin_layout.addRow(self.chat_autocorrect_cb)

        self.chat_user_card_hover_cb = QCheckBox("Show user card on hover")
        self.chat_user_card_hover_cb.setChecked(self.app.settings.chat.builtin.user_card_hover)
        self.chat_user_card_hover_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.chat_user_card_hover_cb)

        # Moderated message display
        self.moderated_display_combo = QComboBox()
        self.moderated_display_combo.addItem("Strikethrough", "strikethrough")
        self.moderated_display_combo.addItem("Truncated", "truncated")
        self.moderated_display_combo.addItem("Hidden", "hidden")
        current_mod = self.app.settings.chat.builtin.moderated_message_display
        idx = self.moderated_display_combo.findData(current_mod)
        if idx >= 0:
            self.moderated_display_combo.setCurrentIndex(idx)
        self.moderated_display_combo.currentIndexChanged.connect(self._on_chat_changed)
        builtin_layout.addRow("Deleted messages:", self.moderated_display_combo)

        # Banner settings separator
        builtin_layout.addRow(QLabel("<b>Chat Banners</b>"))

        # Show stream title toggle
        self.show_stream_title_cb = QCheckBox("Show stream title banner")
        self.show_stream_title_cb.setChecked(self.app.settings.chat.builtin.show_stream_title)
        self.show_stream_title_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.show_stream_title_cb)

        # Show socials toggle
        self.show_socials_cb = QCheckBox("Show channel socials banner")
        self.show_socials_cb.setChecked(self.app.settings.chat.builtin.show_socials_banner)
        self.show_socials_cb.stateChanged.connect(self._on_chat_changed)
        builtin_layout.addRow(self.show_socials_cb)

        layout.addWidget(self.builtin_group)

        # Highlight Keywords group
        self.keywords_group = QGroupBox("Highlight Keywords")
        kw_layout = QVBoxLayout(self.keywords_group)
        kw_info = QLabel("Messages containing these words will be highlighted (case-insensitive).")
        kw_info.setStyleSheet("color: gray; font-style: italic;")
        kw_info.setWordWrap(True)
        kw_layout.addWidget(kw_info)
        self.kw_search = QLineEdit()
        self.kw_search.setPlaceholderText("Filter keywords\u2026")
        self.kw_search.setClearButtonEnabled(True)
        self.kw_search.textChanged.connect(self._refresh_keywords_list)
        kw_layout.addWidget(self.kw_search)
        self.keywords_list = QListWidget()
        self.keywords_list.setMaximumHeight(100)
        self.keywords_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        kw_layout.addWidget(self.keywords_list)
        kw_buttons = QHBoxLayout()
        kw_add_btn = QPushButton("Add")
        kw_add_btn.clicked.connect(self._add_keyword)
        kw_buttons.addWidget(kw_add_btn)
        kw_remove_btn = QPushButton("Remove Selected")
        kw_remove_btn.clicked.connect(self._remove_keywords)
        kw_buttons.addWidget(kw_remove_btn)
        kw_buttons.addStretch()
        kw_layout.addLayout(kw_buttons)
        self._refresh_keywords_list()
        layout.addWidget(self.keywords_group)

        # Blocked Users group
        self.blocked_group = QGroupBox("Blocked Users")
        bl_layout = QVBoxLayout(self.blocked_group)
        bl_filter_row = QHBoxLayout()
        self.bl_search = QLineEdit()
        self.bl_search.setPlaceholderText("Filter users\u2026")
        self.bl_search.setClearButtonEnabled(True)
        self.bl_search.textChanged.connect(self._refresh_blocked_list)
        bl_filter_row.addWidget(self.bl_search)
        self.bl_platform_filter = self._create_platform_filter_combo()
        self.bl_platform_filter.currentIndexChanged.connect(lambda: self._refresh_blocked_list())
        bl_filter_row.addWidget(self.bl_platform_filter)
        bl_layout.addLayout(bl_filter_row)
        self.blocked_list = QListWidget()
        self.blocked_list.setMaximumHeight(120)
        self.blocked_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        bl_layout.addWidget(self.blocked_list)
        bl_buttons = QHBoxLayout()
        bl_remove_btn = QPushButton("Remove Selected")
        bl_remove_btn.clicked.connect(self._remove_blocked_users)
        bl_buttons.addWidget(bl_remove_btn)
        bl_clear_btn = QPushButton("Clear All")
        bl_clear_btn.clicked.connect(self._clear_all_blocked)
        bl_buttons.addWidget(bl_clear_btn)
        bl_buttons.addStretch()
        bl_layout.addLayout(bl_buttons)
        self._refresh_blocked_list()
        layout.addWidget(self.blocked_group)

        # User Nicknames group
        self.nicknames_group = QGroupBox("User Nicknames")
        nn_layout = QVBoxLayout(self.nicknames_group)
        nn_filter_row = QHBoxLayout()
        self.nn_search = QLineEdit()
        self.nn_search.setPlaceholderText("Filter nicknames\u2026")
        self.nn_search.setClearButtonEnabled(True)
        self.nn_search.textChanged.connect(self._refresh_nicknames_list)
        nn_filter_row.addWidget(self.nn_search)
        self.nn_platform_filter = self._create_platform_filter_combo()
        self.nn_platform_filter.currentIndexChanged.connect(lambda: self._refresh_nicknames_list())
        nn_filter_row.addWidget(self.nn_platform_filter)
        nn_layout.addLayout(nn_filter_row)
        self.nicknames_list = QListWidget()
        self.nicknames_list.setMaximumHeight(120)
        self.nicknames_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        nn_layout.addWidget(self.nicknames_list)
        nn_buttons = QHBoxLayout()
        nn_add_btn = QPushButton("Add")
        nn_add_btn.clicked.connect(self._add_nickname)
        nn_buttons.addWidget(nn_add_btn)
        nn_edit_btn = QPushButton("Edit")
        nn_edit_btn.clicked.connect(self._edit_nickname)
        nn_buttons.addWidget(nn_edit_btn)
        nn_remove_btn = QPushButton("Remove Selected")
        nn_remove_btn.clicked.connect(self._remove_nicknames)
        nn_buttons.addWidget(nn_remove_btn)
        nn_buttons.addStretch()
        nn_layout.addLayout(nn_buttons)
        self._refresh_nicknames_list()
        layout.addWidget(self.nicknames_group)

        # User Notes group
        self.notes_group = QGroupBox("User Notes")
        nt_layout = QVBoxLayout(self.notes_group)
        nt_filter_row = QHBoxLayout()
        self.nt_search = QLineEdit()
        self.nt_search.setPlaceholderText("Filter notes\u2026")
        self.nt_search.setClearButtonEnabled(True)
        self.nt_search.textChanged.connect(self._refresh_notes_list)
        nt_filter_row.addWidget(self.nt_search)
        self.nt_platform_filter = self._create_platform_filter_combo()
        self.nt_platform_filter.currentIndexChanged.connect(lambda: self._refresh_notes_list())
        nt_filter_row.addWidget(self.nt_platform_filter)
        nt_layout.addLayout(nt_filter_row)
        self.notes_list = QListWidget()
        self.notes_list.setMaximumHeight(120)
        self.notes_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        nt_layout.addWidget(self.notes_list)
        nt_buttons = QHBoxLayout()
        nt_add_btn = QPushButton("Add")
        nt_add_btn.clicked.connect(self._add_note)
        nt_buttons.addWidget(nt_add_btn)
        nt_edit_btn = QPushButton("Edit")
        nt_edit_btn.clicked.connect(self._edit_note)
        nt_buttons.addWidget(nt_edit_btn)
        nt_remove_btn = QPushButton("Remove Selected")
        nt_remove_btn.clicked.connect(self._remove_notes)
        nt_buttons.addWidget(nt_remove_btn)
        nt_buttons.addStretch()
        nt_layout.addLayout(nt_buttons)
        self._refresh_notes_list()
        layout.addWidget(self.notes_group)

        # Chat Logging group
        self.logging_group = QGroupBox("Chat Logging")
        log_layout = QFormLayout(self.logging_group)
        log_settings = self.app.settings.chat.logging

        self.log_enabled_cb = QCheckBox("Enable chat logging to disk")
        self.log_enabled_cb.setChecked(log_settings.enabled)
        self.log_enabled_cb.stateChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow(self.log_enabled_cb)

        self.log_disk_spin = QSpinBox()
        self.log_disk_spin.setRange(10, 5000)
        self.log_disk_spin.setSuffix(" MB")
        self.log_disk_spin.setValue(log_settings.max_disk_mb)
        self.log_disk_spin.valueChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow("Max disk usage:", self.log_disk_spin)

        self.log_format_combo = QComboBox()
        self.log_format_combo.addItem("JSONL (supports history loading)", "jsonl")
        self.log_format_combo.addItem("Plain text", "text")
        for i in range(self.log_format_combo.count()):
            if self.log_format_combo.itemData(i) == log_settings.log_format:
                self.log_format_combo.setCurrentIndex(i)
                break
        self.log_format_combo.currentIndexChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow("Format:", self.log_format_combo)

        self.log_history_cb = QCheckBox("Load history on chat open")
        self.log_history_cb.setChecked(log_settings.load_history_on_open)
        self.log_history_cb.stateChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow(self.log_history_cb)

        self.log_history_spin = QSpinBox()
        self.log_history_spin.setRange(10, 1000)
        self.log_history_spin.setSuffix(" messages")
        self.log_history_spin.setValue(log_settings.history_lines)
        self.log_history_spin.valueChanged.connect(self._on_chat_logging_changed)
        log_layout.addRow("History lines:", self.log_history_spin)

        # Current disk usage label
        self.log_disk_usage_label = QLabel()
        self._update_log_disk_usage_label()
        log_layout.addRow("Current usage:", self.log_disk_usage_label)

        layout.addWidget(self.logging_group)

        # Set initial visibility based on current mode
        show_browser = current_mode == "browser"
        self.browser_label.setVisible(show_browser)
        self.browser_combo.setVisible(show_browser)
        self.new_window_cb.setVisible(show_browser)
        self.builtin_group.setVisible(not show_browser)
        self.keywords_group.setVisible(not show_browser)
        self.blocked_group.setVisible(not show_browser)
        self.nicknames_group.setVisible(not show_browser)
        self.notes_group.setVisible(not show_browser)
        self.logging_group.setVisible(not show_browser)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.reset_defaults)
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        self.setWidget(widget)

    # --- Shared filter helpers ---

    def _create_platform_filter_combo(self) -> QComboBox:
        """Create a platform filter dropdown with All + each platform."""
        combo = QComboBox()
        combo.addItem("All", "all")
        for p in StreamPlatform:
            combo.addItem(p.value.capitalize(), p.value)
        combo.setFixedWidth(100)
        return combo

    def _format_platform_label(self, user_key: str) -> str:
        """Extract platform from user_key and return a capitalised label."""
        platform = user_key.split(":")[0] if ":" in user_key else "?"
        return platform.capitalize()

    def _matches_platform_filter(self, user_key: str, platform_filter: str) -> bool:
        """Check if a user_key matches the selected platform filter."""
        if platform_filter == "all":
            return True
        return user_key.startswith(platform_filter + ":")

    # --- Callbacks ---

    def _on_chat_client_changed(self, index):
        """Handle chat client type change."""
        client_type = self.chat_client_combo.currentData()
        show_browser = client_type == "browser"
        self.browser_label.setVisible(show_browser)
        self.browser_combo.setVisible(show_browser)
        self.new_window_cb.setVisible(show_browser)
        self.builtin_group.setVisible(not show_browser)
        self.keywords_group.setVisible(not show_browser)
        self.blocked_group.setVisible(not show_browser)
        self.nicknames_group.setVisible(not show_browser)
        self.notes_group.setVisible(not show_browser)
        self.logging_group.setVisible(not show_browser)
        self._on_chat_changed()

    def _on_chat_changed(self):
        if self._loading:
            return
        self.app.settings.chat.mode = self.chat_client_combo.currentData()
        self.app.settings.chat.auto_open = self.chat_auto_cb.isChecked()
        self.app.settings.chat.browser = self.browser_combo.currentData()
        self.app.settings.chat.new_window = self.new_window_cb.isChecked()
        # Built-in chat settings
        self.app.settings.chat.builtin.font_size = self.chat_font_spin.value()
        self.app.settings.chat.builtin.line_spacing = self.chat_spacing_spin.value()
        self.app.settings.chat.builtin.max_messages = self.scrollback_spin.value()
        self.app.settings.chat.builtin.show_timestamps = self.chat_timestamps_cb.isChecked()
        self.app.settings.chat.builtin.timestamp_format = self.chat_ts_format_combo.currentData()
        self.app.settings.chat.builtin.show_badges = self.chat_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_mod_badges = self.chat_mod_badges_cb.isChecked()
        self.app.settings.chat.builtin.show_emotes = self.chat_emotes_cb.isChecked()
        self.app.settings.chat.builtin.animate_emotes = self.chat_animate_emotes_cb.isChecked()
        self.app.settings.chat.builtin.show_alternating_rows = self.chat_alt_rows_cb.isChecked()
        self.app.settings.chat.builtin.show_metrics = self.chat_metrics_cb.isChecked()
        self.app.settings.chat.builtin.spellcheck_enabled = self.chat_spellcheck_cb.isChecked()
        self.app.settings.chat.builtin.autocorrect_enabled = self.chat_autocorrect_cb.isChecked()
        self.app.settings.chat.builtin.user_card_hover = self.chat_user_card_hover_cb.isChecked()
        self.app.settings.chat.builtin.moderated_message_display = (
            self.moderated_display_combo.currentData()
        )
        self.app.settings.chat.builtin.use_platform_name_colors = (
            self.chat_name_colors_cb.isChecked()
        )
        # Banner settings
        self.app.settings.chat.builtin.show_stream_title = self.show_stream_title_cb.isChecked()
        self.app.settings.chat.builtin.show_socials_banner = self.show_socials_cb.isChecked()

        providers = []
        if self.emote_7tv_cb.isChecked():
            providers.append("7tv")
        if self.emote_bttv_cb.isChecked():
            providers.append("bttv")
        if self.emote_ffz_cb.isChecked():
            providers.append("ffz")
        self.app.settings.chat.builtin.emote_providers = providers
        self.app.save_settings()
        # Live-update chat window if open
        if self.app._chat_window:
            self.app._chat_window.update_tab_style()
            self.app._chat_window.update_animation_state()
            self.app._chat_window.update_banner_settings()
            self.app._chat_window.update_metrics_bar()
            self.app._chat_window.update_spellcheck()

    def _on_chat_logging_changed(self):
        """Handle chat logging settings change."""
        log = self.app.settings.chat.logging
        log.enabled = self.log_enabled_cb.isChecked()
        log.max_disk_mb = self.log_disk_spin.value()
        log.log_format = self.log_format_combo.currentData()
        log.load_history_on_open = self.log_history_cb.isChecked()
        log.history_lines = self.log_history_spin.value()
        self.app.save_settings()
        if self.app.chat_manager:
            self.app.chat_manager.update_chat_logging_settings(log)
        self._update_log_disk_usage_label()

    def _update_log_disk_usage_label(self):
        """Update the disk usage display for chat logs."""
        if self.app.chat_manager:
            usage = self.app.chat_manager.chat_log_writer.get_total_disk_usage()
        else:
            from ....chat.chat_log_store import ChatLogWriter

            writer = ChatLogWriter(self.app.settings.chat.logging)
            usage = writer.get_total_disk_usage()
        if usage < 1024 * 1024:
            text = f"{usage / 1024:.1f} KB"
        else:
            text = f"{usage / (1024 * 1024):.1f} MB"
        self.log_disk_usage_label.setText(text)

    # --- Highlight Keywords helpers ---

    def _refresh_keywords_list(self):
        self.keywords_list.clear()
        search = self.kw_search.text().strip().lower()
        for kw in self.app.settings.chat.builtin.highlight_keywords:
            if search and search not in kw.lower():
                continue
            self.keywords_list.addItem(kw)

    def _add_keyword(self):
        text, ok = QInputDialog.getText(self, "Add Highlight Keyword", "Keyword:")
        if ok and text.strip():
            kw = text.strip()
            if kw not in self.app.settings.chat.builtin.highlight_keywords:
                self.app.settings.chat.builtin.highlight_keywords.append(kw)
                self.app.save_settings()
                self._refresh_keywords_list()

    def _remove_keywords(self):
        for item in self.keywords_list.selectedItems():
            kw = item.text()
            if kw in self.app.settings.chat.builtin.highlight_keywords:
                self.app.settings.chat.builtin.highlight_keywords.remove(kw)
        self.app.save_settings()
        self._refresh_keywords_list()

    # --- Blocked Users helpers ---

    def _refresh_blocked_list(self):
        self.blocked_list.clear()
        search = self.bl_search.text().strip().lower()
        platform_filter = self.bl_platform_filter.currentData()
        builtin = self.app.settings.chat.builtin
        for user_key in builtin.blocked_users:
            if not self._matches_platform_filter(user_key, platform_filter):
                continue
            display = builtin.blocked_user_names.get(user_key, user_key)
            platform = self._format_platform_label(user_key)
            label = f"[{platform}]  {display}"
            if search and search not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, user_key)
            self.blocked_list.addItem(item)

    def _remove_blocked_users(self):
        builtin = self.app.settings.chat.builtin
        for item in self.blocked_list.selectedItems():
            user_key = item.data(Qt.ItemDataRole.UserRole)
            if user_key and user_key in builtin.blocked_users:
                builtin.blocked_users.remove(user_key)
            builtin.blocked_user_names.pop(user_key, None)
        self.app.save_settings()
        self._refresh_blocked_list()

    def _clear_all_blocked(self):
        if not self.app.settings.chat.builtin.blocked_users:
            return
        result = QMessageBox.question(
            self,
            "Clear All Blocked Users",
            "Remove all blocked users?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Ok:
            self.app.settings.chat.builtin.blocked_users.clear()
            self.app.settings.chat.builtin.blocked_user_names.clear()
            self.app.save_settings()
            self._refresh_blocked_list()

    # --- User Nicknames helpers ---

    def _refresh_nicknames_list(self):
        self.nicknames_list.clear()
        search = self.nn_search.text().strip().lower()
        platform_filter = self.nn_platform_filter.currentData()
        builtin = self.app.settings.chat.builtin
        for user_key, nickname in builtin.user_nicknames.items():
            if not self._matches_platform_filter(user_key, platform_filter):
                continue
            original = builtin.user_nickname_display_names.get(user_key, user_key)
            platform = self._format_platform_label(user_key)
            label = f"[{platform}]  {original} \u2192 {nickname}"
            if search and search not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, user_key)
            self.nicknames_list.addItem(item)

    def _add_nickname(self):
        """Add a nickname for a user via dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Nickname")
        dialog.setMinimumWidth(350)
        form = QFormLayout(dialog)

        platform_combo = QComboBox()
        for p in StreamPlatform:
            platform_combo.addItem(p.value.capitalize(), p.value)
        form.addRow("Platform:", platform_combo)

        username_edit = QLineEdit()
        username_edit.setPlaceholderText("e.g. ninja, pokimane")
        form.addRow("Username:", username_edit)

        nickname_edit = QLineEdit()
        nickname_edit.setPlaceholderText("Nickname to display")
        form.addRow("Nickname:", nickname_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            username = username_edit.text().strip()
            nickname = nickname_edit.text().strip()
            if username and nickname:
                platform = platform_combo.currentData()
                user_key = f"{platform}:{username}"
                self.app.settings.chat.builtin.user_nicknames[user_key] = nickname
                self.app.settings.chat.builtin.user_nickname_display_names[user_key] = username
                self.app.save_settings()
                self._refresh_nicknames_list()

    def _edit_nickname(self):
        """Edit the selected nickname."""
        items = self.nicknames_list.selectedItems()
        if not items:
            return
        user_key = items[0].data(Qt.ItemDataRole.UserRole)
        if not user_key:
            return
        builtin = self.app.settings.chat.builtin
        current = builtin.user_nicknames.get(user_key, "")
        display = builtin.user_nickname_display_names.get(user_key, user_key)
        text, ok = QInputDialog.getText(
            self, "Edit Nickname", f"Nickname for {display}:", text=current
        )
        if ok and text.strip():
            builtin.user_nicknames[user_key] = text.strip()
            self.app.save_settings()
            self._refresh_nicknames_list()

    def _remove_nicknames(self):
        builtin = self.app.settings.chat.builtin
        for item in self.nicknames_list.selectedItems():
            user_key = item.data(Qt.ItemDataRole.UserRole)
            if user_key:
                builtin.user_nicknames.pop(user_key, None)
                builtin.user_nickname_display_names.pop(user_key, None)
        self.app.save_settings()
        self._refresh_nicknames_list()

    # --- User Notes helpers ---

    def _refresh_notes_list(self):
        self.notes_list.clear()
        search = self.nt_search.text().strip().lower()
        platform_filter = self.nt_platform_filter.currentData()
        builtin = self.app.settings.chat.builtin
        for user_key, note in builtin.user_notes.items():
            if not self._matches_platform_filter(user_key, platform_filter):
                continue
            display = builtin.user_note_display_names.get(user_key, user_key)
            platform = self._format_platform_label(user_key)
            truncated = note if len(note) <= 60 else note[:57] + "\u2026"
            label = f"[{platform}]  {display}: {truncated}"
            if search and search not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, user_key)
            self.notes_list.addItem(item)

    def _add_note(self):
        """Add a note for a user via dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add User Note")
        dialog.setMinimumWidth(350)
        form = QFormLayout(dialog)

        platform_combo = QComboBox()
        for p in StreamPlatform:
            platform_combo.addItem(p.value.capitalize(), p.value)
        form.addRow("Platform:", platform_combo)

        username_edit = QLineEdit()
        username_edit.setPlaceholderText("e.g. ninja, pokimane")
        form.addRow("Username:", username_edit)

        note_edit = QLineEdit()
        note_edit.setPlaceholderText("Note text")
        form.addRow("Note:", note_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            username = username_edit.text().strip()
            note = note_edit.text().strip()
            if username and note:
                platform = platform_combo.currentData()
                user_key = f"{platform}:{username}"
                self.app.settings.chat.builtin.user_notes[user_key] = note
                self.app.settings.chat.builtin.user_note_display_names[user_key] = username
                self.app.save_settings()
                self._refresh_notes_list()

    def _edit_note(self):
        """Edit the selected note."""
        items = self.notes_list.selectedItems()
        if not items:
            return
        user_key = items[0].data(Qt.ItemDataRole.UserRole)
        if not user_key:
            return
        builtin = self.app.settings.chat.builtin
        current = builtin.user_notes.get(user_key, "")
        display = builtin.user_note_display_names.get(user_key, user_key)
        text, ok = QInputDialog.getText(self, "Edit Note", f"Note for {display}:", text=current)
        if ok and text.strip():
            builtin.user_notes[user_key] = text.strip()
            self.app.save_settings()
            self._refresh_notes_list()

    def _remove_notes(self):
        builtin = self.app.settings.chat.builtin
        for item in self.notes_list.selectedItems():
            user_key = item.data(Qt.ItemDataRole.UserRole)
            if user_key:
                builtin.user_notes.pop(user_key, None)
                builtin.user_note_display_names.pop(user_key, None)
        self.app.save_settings()
        self._refresh_notes_list()

    def reset_defaults(self) -> None:
        """Reset Chat tab settings to defaults."""
        from ....core.settings import BuiltinChatSettings, ChatLoggingSettings, ChatSettings

        result = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Reset all Chat settings to their default values?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Ok:
            return

        chat_defaults = ChatSettings()
        builtin = BuiltinChatSettings()
        self.chat_auto_cb.setChecked(chat_defaults.auto_open)
        for i in range(self.chat_client_combo.count()):
            if self.chat_client_combo.itemData(i) == chat_defaults.mode:
                self.chat_client_combo.setCurrentIndex(i)
                break
        for i in range(self.browser_combo.count()):
            if self.browser_combo.itemData(i) == chat_defaults.browser:
                self.browser_combo.setCurrentIndex(i)
                break
        self.new_window_cb.setChecked(chat_defaults.new_window)
        self.chat_font_spin.setValue(builtin.font_size)
        self.chat_spacing_spin.setValue(builtin.line_spacing)
        self.scrollback_spin.setValue(builtin.max_messages)
        self.emote_7tv_cb.setChecked("7tv" in builtin.emote_providers)
        self.emote_bttv_cb.setChecked("bttv" in builtin.emote_providers)
        self.emote_ffz_cb.setChecked("ffz" in builtin.emote_providers)
        self.chat_timestamps_cb.setChecked(builtin.show_timestamps)
        self.chat_ts_format_combo.setCurrentIndex(0 if builtin.timestamp_format == "24h" else 1)
        self.chat_badges_cb.setChecked(builtin.show_badges)
        self.chat_mod_badges_cb.setChecked(builtin.show_mod_badges)
        self.chat_emotes_cb.setChecked(builtin.show_emotes)
        self.chat_animate_emotes_cb.setChecked(builtin.animate_emotes)
        self.chat_alt_rows_cb.setChecked(builtin.show_alternating_rows)
        self.chat_metrics_cb.setChecked(builtin.show_metrics)
        self.chat_spellcheck_cb.setChecked(builtin.spellcheck_enabled)
        self.chat_autocorrect_cb.setChecked(builtin.autocorrect_enabled)
        self.chat_user_card_hover_cb.setChecked(builtin.user_card_hover)
        idx = self.moderated_display_combo.findData(builtin.moderated_message_display)
        if idx >= 0:
            self.moderated_display_combo.setCurrentIndex(idx)
        self.chat_name_colors_cb.setChecked(builtin.use_platform_name_colors)
        # Banner settings
        self.show_stream_title_cb.setChecked(builtin.show_stream_title)
        self.show_socials_cb.setChecked(builtin.show_socials_banner)
        # Logging defaults
        log_defaults = ChatLoggingSettings()
        self.log_enabled_cb.setChecked(log_defaults.enabled)
        self.log_disk_spin.setValue(log_defaults.max_disk_mb)
        for i in range(self.log_format_combo.count()):
            if self.log_format_combo.itemData(i) == log_defaults.log_format:
                self.log_format_combo.setCurrentIndex(i)
                break
        self.log_history_cb.setChecked(log_defaults.load_history_on_open)
        self.log_history_spin.setValue(log_defaults.history_lines)
        self._on_chat_changed()
