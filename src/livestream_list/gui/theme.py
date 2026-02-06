"""Theme management for light and dark mode support."""

from typing import TYPE_CHECKING

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from livestream_list.core.settings import ThemeMode

if TYPE_CHECKING:
    from livestream_list.core.settings import Settings


class ThemeColors:
    """Color definitions for a theme."""

    def __init__(
        self,
        *,
        # Window and widget backgrounds
        window_bg: str,
        widget_bg: str,
        input_bg: str,
        # Text colors
        text_primary: str,
        text_secondary: str,
        text_muted: str,
        # Accent colors
        accent: str,
        accent_hover: str,
        # Borders
        border: str,
        border_light: str,
        # Selection
        selection_bg: str,
        selection_text: str,
        # Status colors
        status_live: str,
        status_offline: str,
        status_error: str,
        status_success: str,
        status_info: str,
        # Chat-specific
        chat_bg: str,
        chat_input_bg: str,
        chat_tab_active: str,
        chat_tab_inactive: str,
        chat_banner_bg: str,
        chat_banner_text: str,
        chat_url: str,
        chat_url_selected: str,
        chat_system_message: str,
        chat_alt_row_even: str,
        chat_alt_row_odd: str,
        chat_mention_highlight: str,
        # Completer/popup backgrounds
        popup_bg: str,
        popup_hover: str,
        popup_selected: str,
        popup_border: str,
        # Toolbar
        toolbar_bg: str,
    ):
        self.window_bg = window_bg
        self.widget_bg = widget_bg
        self.input_bg = input_bg
        self.text_primary = text_primary
        self.text_secondary = text_secondary
        self.text_muted = text_muted
        self.accent = accent
        self.accent_hover = accent_hover
        self.border = border
        self.border_light = border_light
        self.selection_bg = selection_bg
        self.selection_text = selection_text
        self.status_live = status_live
        self.status_offline = status_offline
        self.status_error = status_error
        self.status_success = status_success
        self.status_info = status_info
        self.chat_bg = chat_bg
        self.chat_input_bg = chat_input_bg
        self.chat_tab_active = chat_tab_active
        self.chat_tab_inactive = chat_tab_inactive
        self.chat_banner_bg = chat_banner_bg
        self.chat_banner_text = chat_banner_text
        self.chat_url = chat_url
        self.chat_url_selected = chat_url_selected
        self.chat_system_message = chat_system_message
        self.chat_alt_row_even = chat_alt_row_even
        self.chat_alt_row_odd = chat_alt_row_odd
        self.chat_mention_highlight = chat_mention_highlight
        self.popup_bg = popup_bg
        self.popup_hover = popup_hover
        self.popup_selected = popup_selected
        self.popup_border = popup_border
        self.toolbar_bg = toolbar_bg


# Dark theme colors (current app colors)
DARK_THEME = ThemeColors(
    # Window and widget backgrounds
    window_bg="#0e1525",
    widget_bg="#1a1a2e",
    input_bg="#16213e",
    # Text colors
    text_primary="#eeeeee",
    text_secondary="#cccccc",
    text_muted="#999999",
    # Accent colors (Twitch purple)
    accent="#7b5cbf",
    accent_hover="#9171d6",
    # Borders
    border="#444444",
    border_light="#333333",
    # Selection
    selection_bg="#7b5cbf",
    selection_text="#ffffff",
    # Status colors
    status_live="#4CAF50",
    status_offline="#999999",
    status_error="#f44336",
    status_success="#4CAF50",
    status_info="#2196F3",
    # Chat-specific
    chat_bg="#0e1525",
    chat_input_bg="#16213e",
    chat_tab_active="#7b5cbf",
    chat_tab_inactive="#16213e",
    chat_banner_bg="#16213e",
    chat_banner_text="#cccccc",
    chat_url="#58a6ff",
    chat_url_selected="#90d5ff",
    chat_system_message="#be96ff",
    chat_alt_row_even="#00000000",
    chat_alt_row_odd="#1affffff",
    chat_mention_highlight="#33ff8800",
    # Completer/popup backgrounds
    popup_bg="#1a1a2e",
    popup_hover="#1f2b4d",
    popup_selected="#7b5cbf",
    popup_border="#444444",
    # Toolbar
    toolbar_bg="#0e1525",
)

# Light theme colors
LIGHT_THEME = ThemeColors(
    # Window and widget backgrounds
    window_bg="#f5f5f5",
    widget_bg="#ffffff",
    input_bg="#ffffff",
    # Text colors
    text_primary="#1a1a1a",
    text_secondary="#444444",
    text_muted="#666666",
    # Accent colors (Twitch purple)
    accent="#6441a5",
    accent_hover="#7d5bbe",
    # Borders
    border="#cccccc",
    border_light="#e0e0e0",
    # Selection
    selection_bg="#6441a5",
    selection_text="#ffffff",
    # Status colors
    status_live="#2e7d32",
    status_offline="#555555",
    status_error="#b71c1c",
    status_success="#2e7d32",
    status_info="#1565c0",
    # Chat-specific
    chat_bg="#ffffff",
    chat_input_bg="#f5f5f5",
    chat_tab_active="#6441a5",
    chat_tab_inactive="#e8e8e8",
    chat_banner_bg="#e8e8f0",
    chat_banner_text="#333333",
    chat_url="#0550ae",
    chat_url_selected="#003d82",
    chat_system_message="#6b4f96",
    chat_alt_row_even="#00000000",
    chat_alt_row_odd="#0f000000",
    chat_mention_highlight="#40ff8800",
    # Completer/popup backgrounds
    popup_bg="#ffffff",
    popup_hover="#f0f0f5",
    popup_selected="#6441a5",
    popup_border="#cccccc",
    # Toolbar
    toolbar_bg="#e8e8e8",
)

# High contrast theme (WCAG AAA compliant)
HIGH_CONTRAST_THEME = ThemeColors(
    # Window and widget backgrounds
    window_bg="#000000",
    widget_bg="#0a0a0a",
    input_bg="#1a1a1a",
    # Text colors (all exceed 7:1 on black)
    text_primary="#ffffff",
    text_secondary="#e0e0e0",
    text_muted="#bbbbbb",
    # Accent colors (bright yellow for visibility)
    accent="#ffcc00",
    accent_hover="#ffe066",
    # Borders (high visibility)
    border="#888888",
    border_light="#666666",
    # Selection
    selection_bg="#ffcc00",
    selection_text="#000000",
    # Status colors (bright, saturated)
    status_live="#00ff00",
    status_offline="#bbbbbb",
    status_error="#ff4444",
    status_success="#00ff00",
    status_info="#44aaff",
    # Chat-specific
    chat_bg="#000000",
    chat_input_bg="#1a1a1a",
    chat_tab_active="#ffcc00",
    chat_tab_inactive="#1a1a1a",
    chat_banner_bg="#1a1a1a",
    chat_banner_text="#e0e0e0",
    chat_url="#44aaff",
    chat_url_selected="#88ccff",
    chat_system_message="#ddaaff",
    chat_alt_row_even="#00000000",
    chat_alt_row_odd="#20ffffff",
    chat_mention_highlight="#55ff8800",
    # Completer/popup backgrounds
    popup_bg="#0a0a0a",
    popup_hover="#222222",
    popup_selected="#ffcc00",
    popup_border="#888888",
    # Toolbar
    toolbar_bg="#000000",
)

# Platform colors (same for both themes)
PLATFORM_COLORS = {
    "twitch": "#9146FF",
    "youtube": "#FF0000",
    "kick": "#53FC18",
}


# Cache for generated stylesheets (keyed by theme object id)
_stylesheet_cache: dict[int, str] = {}


class ThemeManager:
    """Manages theme state and provides current theme colors."""

    _instance: "ThemeManager | None" = None
    _settings: "Settings | None" = None
    _cached_is_dark: bool | None = None

    @classmethod
    def instance(cls) -> "ThemeManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def set_settings(cls, settings: "Settings") -> None:
        """Set the settings instance for theme management."""
        cls._settings = settings
        cls._cached_is_dark = None

    @classmethod
    def get_theme_mode(cls) -> ThemeMode:
        """Get the current theme mode setting."""
        if cls._settings is None:
            return ThemeMode.AUTO
        return cls._settings.theme_mode

    @classmethod
    def set_theme_mode(cls, mode: ThemeMode) -> None:
        """Set the theme mode and save settings."""
        if cls._settings is not None:
            cls._settings.theme_mode = mode
            cls._settings.save()
        cls._cached_is_dark = None

    @classmethod
    def detect_system_dark_mode(cls) -> bool:
        """Detect if system is using dark mode."""
        app = QApplication.instance()
        if app is None:
            return True  # Default to dark if no app

        palette = app.palette()
        # Compare window background luminance - dark themes have low luminance
        bg_color = palette.color(QPalette.ColorRole.Window)
        # Calculate relative luminance
        luminance = (0.299 * bg_color.red() + 0.587 * bg_color.green() + 0.114 * bg_color.blue())
        return luminance < 128

    @classmethod
    def is_dark_mode(cls) -> bool:
        """Check if we should use dark mode based on settings and system."""
        if cls._cached_is_dark is not None:
            return cls._cached_is_dark

        mode = cls.get_theme_mode()
        if mode == ThemeMode.DARK or mode == ThemeMode.HIGH_CONTRAST:
            result = True
        elif mode == ThemeMode.LIGHT:
            result = False
        else:  # AUTO
            result = cls.detect_system_dark_mode()

        cls._cached_is_dark = result
        return result

    @classmethod
    def invalidate_cache(cls) -> None:
        """Invalidate the cached theme state (call when system theme changes)."""
        cls._cached_is_dark = None
        _stylesheet_cache.clear()

    @classmethod
    def colors(cls) -> ThemeColors:
        """Get the current theme colors."""
        if cls.get_theme_mode() == ThemeMode.HIGH_CONTRAST:
            return HIGH_CONTRAST_THEME
        return DARK_THEME if cls.is_dark_mode() else LIGHT_THEME


def get_theme() -> ThemeColors:
    """Get the current theme colors (convenience function)."""
    return ThemeManager.colors()


def is_dark_mode() -> bool:
    """Check if dark mode is active (convenience function)."""
    return ThemeManager.is_dark_mode()


def get_app_stylesheet() -> str:
    """Generate a comprehensive application-wide stylesheet for the current theme.

    This stylesheet applies to all QWidgets in the application including dialogs.
    Uses caching to avoid regenerating the same stylesheet repeatedly.
    """
    theme = get_theme()
    theme_id = id(theme)
    if theme_id in _stylesheet_cache:
        return _stylesheet_cache[theme_id]

    stylesheet = f"""
        QWidget {{
            background-color: {theme.window_bg};
            color: {theme.text_primary};
        }}
        QDialog {{
            background-color: {theme.window_bg};
            color: {theme.text_primary};
        }}
        QGroupBox {{
            border: 1px solid {theme.border};
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 4px;
            color: {theme.text_primary};
        }}
        QTabWidget::pane {{
            border: 1px solid {theme.border};
            background-color: {theme.widget_bg};
        }}
        QTabBar::tab {{
            background-color: {theme.input_bg};
            color: {theme.text_secondary};
            padding: 6px 12px;
            border: 1px solid {theme.border};
            border-bottom: none;
        }}
        QTabBar::tab:selected {{
            background-color: {theme.widget_bg};
            color: {theme.text_primary};
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox {{
            background-color: {theme.input_bg};
            color: {theme.text_primary};
            border: 1px solid {theme.border};
            border-radius: 4px;
            padding: 4px;
        }}
        QLineEdit:focus, QSpinBox:focus {{
            border-color: {theme.accent};
        }}
        QTextEdit, QPlainTextEdit {{
            background-color: {theme.input_bg};
            color: {theme.text_primary};
            border: 1px solid {theme.border};
        }}
        QComboBox {{
            background-color: {theme.input_bg};
            color: {theme.text_primary};
            border: 1px solid {theme.border};
            border-radius: 4px;
            padding: 4px 8px;
        }}
        QComboBox::drop-down {{
            border: none;
        }}
        QComboBox QAbstractItemView {{
            background-color: {theme.popup_bg};
            color: {theme.text_primary};
            selection-background-color: {theme.selection_bg};
            selection-color: {theme.selection_text};
        }}
        QToolButton {{
            color: {theme.text_primary};
        }}
        QPushButton {{
            background-color: {theme.input_bg};
            color: {theme.text_primary};
            border: 1px solid {theme.border};
            border-radius: 4px;
        }}
        QPushButton:hover {{
            background-color: {theme.accent_hover};
            color: white;
        }}
        QPushButton:pressed {{
            background-color: {theme.accent};
        }}
        QPushButton:disabled {{
            background-color: {theme.border};
            color: {theme.text_muted};
        }}
        QCheckBox {{
            color: {theme.text_primary};
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
        }}
        QRadioButton {{
            color: {theme.text_primary};
        }}
        QLabel {{
            color: {theme.text_primary};
            background: transparent;
        }}
        QScrollArea {{
            border: none;
            background-color: {theme.window_bg};
        }}
        QScrollBar:vertical {{
            background-color: {theme.window_bg};
            width: 12px;
        }}
        QScrollBar::handle:vertical {{
            background-color: {theme.border};
            border-radius: 4px;
            min-height: 20px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QListWidget {{
            background-color: {theme.widget_bg};
            color: {theme.text_primary};
            border: 1px solid {theme.border};
        }}
        QListWidget::item:selected {{
            background-color: {theme.selection_bg};
            color: {theme.selection_text};
        }}
        QProgressBar {{
            background-color: {theme.input_bg};
            border: 1px solid {theme.border};
            border-radius: 4px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background-color: {theme.accent};
            border-radius: 3px;
        }}
        QMenu {{
            background-color: {theme.popup_bg};
            color: {theme.text_primary};
            border: 1px solid {theme.border};
        }}
        QMenu::item:selected {{
            background-color: {theme.selection_bg};
            color: {theme.selection_text};
        }}
        QToolTip {{
            background-color: {theme.popup_bg};
            color: {theme.text_primary};
            border: 1px solid {theme.border};
            padding: 4px;
        }}
    """
    _stylesheet_cache[theme_id] = stylesheet
    return stylesheet
