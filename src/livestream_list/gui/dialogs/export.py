"""Dialog for exporting channels and settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from ...__version__ import __version__

if TYPE_CHECKING:
    from ..app import Application


class ExportDialog(QDialog):
    """Dialog for exporting channels and settings."""

    def __init__(self, parent, app: Application):
        super().__init__(parent)
        self.app = app

        self.setWindowTitle("Export")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        # Info
        channel_count = len(self.app.monitor.channels)
        info_label = QLabel(f"Export {channel_count} channels")
        layout.addWidget(info_label)

        # Options
        self.include_settings_cb = QCheckBox("Include settings")
        self.include_settings_cb.setChecked(True)
        layout.addWidget(self.include_settings_cb)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_export(self):
        """Handle export."""
        import json
        from datetime import datetime

        default_name = f"livestream-list-export-{datetime.now().strftime('%Y-%m-%d')}.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Channels", default_name, "JSON Files (*.json)"
        )

        if not file_path:
            return

        try:
            data = {
                "meta": {
                    "schema_version": 1,
                    "app_version": __version__,
                    "export_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
                "channels": [],
            }

            for channel in self.app.monitor.channels:
                ch_data = {
                    "channel_id": channel.channel_id,
                    "platform": channel.platform.value,
                    "display_name": channel.display_name,
                    "favorite": channel.favorite,
                }
                data["channels"].append(ch_data)

            if self.include_settings_cb.isChecked():
                # Export all settings except sensitive auth tokens
                settings_dict = self.app.settings._to_dict()
                # Remove sensitive data
                if "twitch" in settings_dict:
                    settings_dict["twitch"] = {}  # Don't export tokens
                if "youtube" in settings_dict:
                    settings_dict["youtube"] = {}  # Don't export API key
                if "kick" in settings_dict:
                    settings_dict["kick"] = {}  # Don't export tokens
                # Remove window geometry (machine-specific) but keep preferences
                if "window" in settings_dict:
                    window_prefs = {
                        "always_on_top": settings_dict["window"].get("always_on_top", False),
                    }
                    settings_dict["window"] = window_prefs
                data["settings"] = settings_dict

            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)

            self.accept()
            QMessageBox.information(self.parent(), "Export Complete", f"Exported to {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")
