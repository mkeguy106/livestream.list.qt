"""Theme editor widget for the Appearance tab in Preferences."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.theme_data import (
    ALPHA_COLOR_FIELDS,
    BUILTIN_THEMES,
    THEME_COLOR_CATEGORIES,
    THEME_COLOR_LABELS,
    ThemeData,
    delete_theme_file,
    export_theme,
    import_theme,
    list_all_themes,
    save_theme_file,
    theme_colors_to_dict,
    theme_data_to_theme_colors,
)
from ..theme import ThemeManager, get_app_stylesheet

if TYPE_CHECKING:
    from ..app import Application

logger = logging.getLogger(__name__)


class ThemeEditorWidget(QWidget):
    """Theme editor widget embedded as the Appearance tab in Preferences."""

    def __init__(self, app: Application, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._loading = False  # Prevent cascading updates during programmatic changes
        self._color_edits: dict[str, QLineEdit] = {}  # field_name -> QLineEdit
        self._color_swatches: dict[str, QPushButton] = {}  # field_name -> swatch button
        self._color_resets: dict[str, QPushButton] = {}  # field_name -> reset button
        self._editing_data: ThemeData | None = None  # Working copy of active theme
        self._saved_data: ThemeData | None = None  # Last-saved state (for discard)
        self._setup_ui()
        self._load_current_theme()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Top bar: theme selector + action buttons ---
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Theme:"))

        self._theme_combo = QComboBox()
        self._theme_combo.setMinimumWidth(180)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        top_bar.addWidget(self._theme_combo, 1)

        self._customize_btn = QPushButton("Customize")
        self._customize_btn.setToolTip("Create a custom copy of this built-in theme")
        self._customize_btn.clicked.connect(self._on_customize)
        top_bar.addWidget(self._customize_btn)

        self._import_btn = QPushButton("Import")
        self._import_btn.setToolTip("Import a theme from a JSON file")
        self._import_btn.clicked.connect(self._on_import)
        top_bar.addWidget(self._import_btn)

        self._export_btn = QPushButton("Export")
        self._export_btn.setToolTip("Export the current theme to a JSON file")
        self._export_btn.clicked.connect(self._on_export)
        top_bar.addWidget(self._export_btn)

        layout.addLayout(top_bar)

        # --- Scroll area for color editor ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._editor_widget = QWidget()
        self._editor_layout = QVBoxLayout(self._editor_widget)
        self._editor_layout.setSpacing(6)

        # Theme name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Theme name:"))
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_color_changed)
        name_row.addWidget(self._name_edit, 1)
        self._editor_layout.addLayout(name_row)

        # Color categories
        for category_name, field_names in THEME_COLOR_CATEGORIES.items():
            group = QGroupBox(category_name)
            group_layout = QFormLayout(group)
            group_layout.setSpacing(4)
            group_layout.setContentsMargins(8, 12, 8, 8)

            for field_name in field_names:
                label_text = THEME_COLOR_LABELS.get(field_name, field_name)
                row = QHBoxLayout()
                row.setSpacing(4)

                # Color swatch button
                swatch = QPushButton()
                swatch.setFixedSize(24, 24)
                swatch.setCursor(Qt.CursorShape.PointingHandCursor)
                row.addWidget(swatch)

                # Hex color line edit
                edit = QLineEdit()
                edit.setMaximumWidth(100)
                edit.setPlaceholderText("#RRGGBB")
                row.addWidget(edit)

                # Reset button
                reset_btn = QPushButton("Reset")
                reset_btn.setFixedWidth(50)
                row.addWidget(reset_btn)
                row.addStretch()

                # Store references
                self._color_edits[field_name] = edit
                self._color_swatches[field_name] = swatch
                self._color_resets[field_name] = reset_btn

                # Connect signals
                edit.textChanged.connect(lambda t, s=swatch: self._update_swatch(s, t))
                edit.editingFinished.connect(self._on_color_changed)
                fn = field_name  # capture for lambda
                if field_name in ALPHA_COLOR_FIELDS:
                    swatch.clicked.connect(
                        lambda checked=False, f=fn: self._pick_color_alpha(f)
                    )
                else:
                    swatch.clicked.connect(
                        lambda checked=False, f=fn: self._pick_color(f)
                    )
                reset_btn.clicked.connect(
                    lambda checked=False, f=fn: self._reset_single_color(f)
                )

                group_layout.addRow(label_text + ":", row)

            self._editor_layout.addWidget(group)

        self._editor_layout.addStretch()
        scroll.setWidget(self._editor_widget)
        layout.addWidget(scroll, 1)

        # --- Bottom action buttons ---
        btn_row = QHBoxLayout()

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        self._save_as_btn = QPushButton("Save As...")
        self._save_as_btn.clicked.connect(self._on_save_as)
        btn_row.addWidget(self._save_as_btn)

        self._discard_btn = QPushButton("Discard Changes")
        self._discard_btn.clicked.connect(self._on_discard)
        btn_row.addWidget(self._discard_btn)

        self._delete_btn = QPushButton("Delete Theme")
        self._delete_btn.setStyleSheet("color: #e05555;")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    # -----------------------------------------------------------------
    # Theme loading / population
    # -----------------------------------------------------------------

    def _populate_theme_combo(self, select_slug: str = "") -> None:
        """Rebuild the theme combo box from available themes."""
        self._loading = True
        try:
            self._theme_combo.clear()
            themes = list_all_themes()
            selected_idx = 0
            for i, td in enumerate(themes):
                suffix = " (built-in)" if td.builtin else ""
                self._theme_combo.addItem(td.name + suffix, td.slug)
                if td.slug == select_slug:
                    selected_idx = i
            if self._theme_combo.count() > 0:
                self._theme_combo.setCurrentIndex(selected_idx)
        finally:
            self._loading = False

    def _load_current_theme(self) -> None:
        """Load the currently active theme into the editor."""
        from ...core.settings import ThemeMode

        mode = self.app.settings.theme_mode
        if mode == ThemeMode.CUSTOM:
            slug = self.app.settings.custom_theme_slug
        elif mode == ThemeMode.HIGH_CONTRAST:
            slug = "high-contrast"
        elif mode == ThemeMode.LIGHT:
            slug = "light"
        elif mode == ThemeMode.DARK:
            slug = "dark"
        else:
            # AUTO - pick based on detected mode
            slug = "dark" if ThemeManager.is_dark_mode() else "light"

        self._populate_theme_combo(select_slug=slug)
        # Explicitly load — _on_theme_selected is blocked by _loading flag
        td = self._find_theme(slug)
        if td:
            self._load_theme_into_editor(td)

    def _load_theme_into_editor(self, td: ThemeData) -> None:
        """Populate editor fields from a ThemeData."""
        self._loading = True
        try:
            self._editing_data = ThemeData(
                name=td.name,
                slug=td.slug,
                author=td.author,
                base=td.base,
                builtin=td.builtin,
                colors=dict(td.colors),
            )
            # Save a copy for discard
            self._saved_data = ThemeData(
                name=td.name,
                slug=td.slug,
                author=td.author,
                base=td.base,
                builtin=td.builtin,
                colors=dict(td.colors),
            )

            self._name_edit.setText(td.name)

            # Get fully-resolved colors (filling in any missing from base)
            tc = theme_data_to_theme_colors(td)
            all_colors = theme_colors_to_dict(tc)

            for field_name, edit in self._color_edits.items():
                val = all_colors.get(field_name, "")
                edit.setText(val)
                self._update_swatch(self._color_swatches[field_name], val)

            # Update UI state
            is_builtin = td.builtin
            self._set_editor_enabled(not is_builtin)
            self._customize_btn.setVisible(is_builtin)
            self._delete_btn.setVisible(not is_builtin)
            self._save_btn.setEnabled(not is_builtin)
            self._discard_btn.setEnabled(not is_builtin)
        finally:
            self._loading = False

    def _set_editor_enabled(self, enabled: bool) -> None:
        """Enable/disable the color editor fields."""
        self._name_edit.setEnabled(enabled)
        for edit in self._color_edits.values():
            edit.setEnabled(enabled)
        for swatch in self._color_swatches.values():
            swatch.setEnabled(enabled)
        for reset in self._color_resets.values():
            reset.setEnabled(enabled)

    # -----------------------------------------------------------------
    # Signal handlers
    # -----------------------------------------------------------------

    def _on_theme_selected(self, index: int) -> None:
        """Handle theme selection from combo box."""
        if self._loading or index < 0:
            return
        slug = self._theme_combo.currentData()
        if not slug:
            return

        # Find the theme
        td = self._find_theme(slug)
        if td is None:
            return

        self._load_theme_into_editor(td)
        self._apply_theme_live(td)

    def _find_theme(self, slug: str) -> ThemeData | None:
        """Find a theme by slug from built-ins or custom files."""
        if slug in BUILTIN_THEMES:
            return BUILTIN_THEMES[slug]
        # Search custom themes
        for td in list_all_themes():
            if td.slug == slug:
                return td
        return None

    def _on_color_changed(self) -> None:
        """Handle any color or name change in the editor."""
        if self._loading or self._editing_data is None:
            return
        if self._editing_data.builtin:
            return

        # Update editing data from UI
        self._editing_data.name = self._name_edit.text().strip()
        for field_name, edit in self._color_edits.items():
            val = edit.text().strip()
            if val:
                self._editing_data.colors[field_name] = val

        # Live preview
        self._apply_theme_live(self._editing_data)

    def _apply_theme_live(self, td: ThemeData) -> None:
        """Apply a theme for live preview."""
        from ...core.settings import ThemeMode

        tc = theme_data_to_theme_colors(td)
        ThemeManager.set_custom_theme(tc)
        self.app.settings.theme_mode = ThemeMode.CUSTOM
        self.app.settings.custom_theme_slug = td.slug
        self.app.settings.custom_theme_base = td.base
        ThemeManager.invalidate_cache()

        # Re-apply the global stylesheet
        self.app.setStyleSheet(get_app_stylesheet())

        # Update main window if it exists
        if self.app.main_window and hasattr(self.app.main_window, "_apply_theme"):
            self.app.main_window._apply_theme()

        # Update chat window if open
        if self.app._chat_window:
            self.app._chat_window.apply_theme()

        self.app.save_settings()

    # -----------------------------------------------------------------
    # Color picker helpers
    # -----------------------------------------------------------------

    def _update_swatch(self, button: QPushButton, hex_color: str) -> None:
        """Update a color swatch button's background from a hex string."""
        color = QColor(hex_color)
        if color.isValid():
            if color.alpha() < 255:
                css_color = (
                    f"rgba({color.red()}, {color.green()}, {color.blue()}, "
                    f"{color.alpha() / 255:.2f})"
                )
            else:
                css_color = hex_color
            button.setStyleSheet(
                f"background-color: {css_color}; border: 1px solid #666; border-radius: 3px;"
            )
        else:
            button.setStyleSheet(
                "background-color: #333; border: 1px solid #666; border-radius: 3px;"
            )

    def _pick_color(self, field_name: str) -> None:
        """Open an RGB color picker for a field."""
        edit = self._color_edits[field_name]
        swatch = self._color_swatches[field_name]
        current = QColor(edit.text().strip())
        if not current.isValid():
            current = QColor("#666666")
        color = QColorDialog.getColor(current, self, "Pick a color")
        if color.isValid():
            edit.setText(color.name())
            self._update_swatch(swatch, color.name())
            self._on_color_changed()

    def _pick_color_alpha(self, field_name: str) -> None:
        """Open a color picker with alpha channel for a field."""
        edit = self._color_edits[field_name]
        swatch = self._color_swatches[field_name]
        text = edit.text().strip()
        current = None
        # Parse #AARRGGBB format
        if text.startswith("#") and len(text) == 9:
            try:
                a = int(text[1:3], 16)
                r = int(text[3:5], 16)
                g = int(text[5:7], 16)
                b = int(text[7:9], 16)
                current = QColor(r, g, b, a)
            except ValueError:
                pass
        if current is None or not current.isValid():
            current = QColor(text)
        if not current.isValid():
            current = QColor(255, 255, 255, 15)
        color = QColorDialog.getColor(
            current,
            self,
            "Pick a color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            a = color.alpha()
            if a < 32:
                a = 255
            r, g, b = color.red(), color.green(), color.blue()
            hex_color = f"#{a:02x}{r:02x}{g:02x}{b:02x}"
            edit.setText(hex_color)
            self._update_swatch(swatch, hex_color)
            self._on_color_changed()

    def _reset_single_color(self, field_name: str) -> None:
        """Reset a single color field to the base theme's default."""
        if self._editing_data is None:
            return
        from ...core.theme_data import _DARK_COLORS, _LIGHT_COLORS

        base_colors = _DARK_COLORS if self._editing_data.base == "dark" else _LIGHT_COLORS
        default_val = base_colors.get(field_name, "#000000")
        self._color_edits[field_name].setText(default_val)
        self._on_color_changed()

    # -----------------------------------------------------------------
    # Action buttons
    # -----------------------------------------------------------------

    def _on_customize(self) -> None:
        """Fork a built-in theme into a custom copy."""
        if self._editing_data is None:
            return

        name, ok = QInputDialog.getText(
            self,
            "Customize Theme",
            "Name for your custom theme:",
            text=f"My {self._editing_data.name}",
        )
        if not ok or not name.strip():
            return

        # Create custom copy
        from ...core.theme_data import _name_to_slug

        slug = _name_to_slug(name.strip())
        # Ensure unique slug
        existing = {t.slug for t in list_all_themes()}
        base_slug = slug
        counter = 1
        while slug in existing:
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Get the fully resolved colors
        tc = theme_data_to_theme_colors(self._editing_data)
        all_colors = theme_colors_to_dict(tc)

        new_td = ThemeData(
            name=name.strip(),
            slug=slug,
            base=self._editing_data.base,
            builtin=False,
            colors=all_colors,
        )
        save_theme_file(new_td)

        # Switch to the new theme
        self._populate_theme_combo(select_slug=slug)
        self._load_theme_into_editor(new_td)
        self._apply_theme_live(new_td)

    def _on_save(self) -> None:
        """Save the current custom theme."""
        if self._editing_data is None or self._editing_data.builtin:
            return

        self._editing_data.name = self._name_edit.text().strip() or "Untitled"
        # Collect colors from editor
        for field_name, edit in self._color_edits.items():
            val = edit.text().strip()
            if val:
                self._editing_data.colors[field_name] = val

        save_theme_file(self._editing_data)
        self._saved_data = ThemeData(
            name=self._editing_data.name,
            slug=self._editing_data.slug,
            author=self._editing_data.author,
            base=self._editing_data.base,
            builtin=False,
            colors=dict(self._editing_data.colors),
        )

        # Persist to settings
        self.app.settings.custom_theme_slug = self._editing_data.slug
        self.app.settings.custom_theme_base = self._editing_data.base
        self.app.save_settings()

        # Update combo text in case name changed
        idx = self._theme_combo.currentIndex()
        if idx >= 0:
            self._theme_combo.setItemText(idx, self._editing_data.name)

    def _on_save_as(self) -> None:
        """Save the current theme with a new name."""
        if self._editing_data is None:
            return

        name, ok = QInputDialog.getText(
            self,
            "Save Theme As",
            "New theme name:",
            text=self._editing_data.name,
        )
        if not ok or not name.strip():
            return

        from ...core.theme_data import _name_to_slug

        slug = _name_to_slug(name.strip())
        existing = {t.slug for t in list_all_themes()}
        base_slug = slug
        counter = 1
        while slug in existing:
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Collect colors from editor
        colors = {}
        for field_name, edit in self._color_edits.items():
            val = edit.text().strip()
            if val:
                colors[field_name] = val

        base = self._editing_data.base if self._editing_data else "dark"
        new_td = ThemeData(
            name=name.strip(),
            slug=slug,
            base=base,
            builtin=False,
            colors=colors,
        )
        save_theme_file(new_td)

        self._populate_theme_combo(select_slug=slug)
        self._load_theme_into_editor(new_td)
        self._apply_theme_live(new_td)

    def _on_discard(self) -> None:
        """Revert to the last-saved state."""
        if self._saved_data is not None and not self._saved_data.builtin:
            self._load_theme_into_editor(self._saved_data)
            self._apply_theme_live(self._saved_data)

    def _on_delete(self) -> None:
        """Delete the current custom theme."""
        if self._editing_data is None or self._editing_data.builtin:
            return

        result = QMessageBox.question(
            self,
            "Delete Theme",
            f'Delete custom theme "{self._editing_data.name}"?\n\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        delete_theme_file(self._editing_data.slug)

        # Switch back to dark theme
        from ...core.settings import ThemeMode

        self.app.settings.theme_mode = ThemeMode.DARK
        self.app.settings.custom_theme_slug = ""
        self.app.settings.custom_theme_base = "dark"
        ThemeManager.set_settings(self.app.settings)
        ThemeManager.invalidate_cache()
        self.app.setStyleSheet(get_app_stylesheet())
        self.app.save_settings()

        if self.app.main_window and hasattr(self.app.main_window, "_apply_theme"):
            self.app.main_window._apply_theme()
        if self.app._chat_window:
            self.app._chat_window.apply_theme()

        self._populate_theme_combo(select_slug="dark")
        td = self._find_theme("dark")
        if td:
            self._load_theme_into_editor(td)

    def _on_import(self) -> None:
        """Import a theme from a JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Theme",
            "",
            "Theme Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            from pathlib import Path

            td = import_theme(Path(path))
            self._populate_theme_combo(select_slug=td.slug)
            self._load_theme_into_editor(td)
            self._apply_theme_live(td)
        except Exception as e:
            QMessageBox.warning(self, "Import Failed", f"Could not import theme:\n{e}")

    def _on_export(self) -> None:
        """Export the current theme to a JSON file."""
        if self._editing_data is None:
            return

        suggested_name = f"{self._editing_data.slug or 'theme'}.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Theme",
            suggested_name,
            "Theme Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            from pathlib import Path

            # Build a clean ThemeData for export
            colors = {}
            for field_name, edit in self._color_edits.items():
                val = edit.text().strip()
                if val:
                    colors[field_name] = val

            export_td = ThemeData(
                name=self._editing_data.name,
                slug=self._editing_data.slug,
                author=self._editing_data.author,
                base=self._editing_data.base,
                colors=colors,
            )
            export_theme(export_td, Path(path))
        except Exception as e:
            QMessageBox.warning(self, "Export Failed", f"Could not export theme:\n{e}")
