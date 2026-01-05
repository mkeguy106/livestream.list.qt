"""System tray icon for Livestream List using StatusNotifierItem D-Bus protocol.

This implementation uses the StatusNotifierItem specification directly via D-Bus,
avoiding the GTK3 dependency of AppIndicator3.
"""

import logging
import struct
from typing import Callable, Optional

from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

# StatusNotifierItem D-Bus interface
SNI_INTERFACE = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
    <signal name="NewIcon"/>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
  </interface>
</node>
"""

# DBusMenu interface for the tray menu
DBUSMENU_INTERFACE = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>
"""

# 16x16 icon design (1 = filled, 0 = empty)
# Monitor with play button and live dot
ICON_PATTERN = [
    "0011111111111100",
    "0111111111111110",
    "0110000000000110",
    "0110000000001110",
    "0110011000000110",
    "0110011100000110",
    "0110011110000110",
    "0110011111000110",
    "0110011110000110",
    "0110011100000110",
    "0110011000000110",
    "0110000000000110",
    "0111111111111110",
    "0011111111111100",
    "0000011111100000",
    "0000111111110000",
]


def is_tray_available() -> bool:
    """Check if system tray support is available via StatusNotifierWatcher."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(
            "org.kde.StatusNotifierWatcher",
            "/StatusNotifierWatcher",
            "org.freedesktop.DBus.Peer",
            "Ping",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        return True
    except Exception:
        return False


def _get_kde_panel_color() -> tuple[int, int, int]:
    """Try to get KDE panel text/icon color from theme settings."""
    try:
        import subprocess
        result = subprocess.run(
            ["kreadconfig5", "--group", "Colors:Window", "--key", "ForegroundNormal"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            if len(parts) >= 3:
                return (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        pass
    # Default to a light blue/cyan that works on dark panels
    return (138, 180, 248)  # Light blue similar to Breeze icons


def _generate_icon_pixmap(size: int = 22) -> bytes:
    """Generate ARGB32 icon pixmap data in network byte order."""
    r, g, b = _get_kde_panel_color()

    # Scale the 16x16 pattern to the requested size
    scale = size / 16.0
    pixels = bytearray()

    for y in range(size):
        src_y = int(y / scale)
        if src_y >= 16:
            src_y = 15
        for x in range(size):
            src_x = int(x / scale)
            if src_x >= 16:
                src_x = 15

            if ICON_PATTERN[src_y][src_x] == "1":
                # ARGB in network byte order (big endian)
                pixels.extend([255, r, g, b])  # Full opacity
            else:
                # Transparent
                pixels.extend([0, 0, 0, 0])

    return bytes(pixels)


class TrayIcon:
    """System tray icon using StatusNotifierItem D-Bus protocol."""

    MENU_ID_OPEN = 1
    MENU_ID_SEPARATOR1 = 2
    MENU_ID_NOTIFICATIONS = 3
    MENU_ID_SEPARATOR2 = 4
    MENU_ID_QUIT = 5

    def __init__(
        self,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        get_notifications_enabled: Callable[[], bool],
        set_notifications_enabled: Callable[[bool], None],
    ) -> None:
        """Initialize the tray icon."""
        self._on_open = on_open
        self._on_quit = on_quit
        self._get_notifications_enabled = get_notifications_enabled
        self._set_notifications_enabled = set_notifications_enabled
        self._bus: Optional[Gio.DBusConnection] = None
        self._sni_registration_id: int = 0
        self._menu_registration_id: int = 0
        self._item_path = "/StatusNotifierItem"
        self._menu_path = "/StatusNotifierItem/Menu"
        self._status = "Active"
        self._menu_revision = 1
        self._available = False
        self._icon_pixmaps: list[tuple[int, int, bytes]] = []

        # Pre-generate icon pixmaps at common sizes
        for size in [16, 22, 24, 32, 48]:
            pixmap_data = _generate_icon_pixmap(size)
            self._icon_pixmaps.append((size, size, pixmap_data))

        if not is_tray_available():
            logger.info("StatusNotifierWatcher not available - system tray disabled")
            return

        try:
            self._setup_dbus()
            self._available = True
            logger.info("System tray icon created successfully")
        except Exception as e:
            logger.error(f"Failed to create tray icon: {e}")

    def _setup_dbus(self) -> None:
        """Set up D-Bus interfaces for StatusNotifierItem."""
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        # Use standard StatusNotifierItem paths
        self._item_path = "/StatusNotifierItem"
        self._menu_path = "/StatusNotifierItem/Menu"

        # Register StatusNotifierItem interface
        sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_INTERFACE)
        self._sni_registration_id = self._bus.register_object(
            self._item_path,
            sni_info.interfaces[0],
            self._handle_sni_method_call,
            self._handle_sni_get_property,
            None,
        )

        # Register DBusMenu interface
        menu_info = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_INTERFACE)
        self._menu_registration_id = self._bus.register_object(
            self._menu_path,
            menu_info.interfaces[0],
            self._handle_menu_method_call,
            self._handle_menu_get_property,
            None,
        )

        # Register with StatusNotifierWatcher - pass just the bus name
        # The watcher will look for the interface at /StatusNotifierItem
        try:
            bus_name = self._bus.get_unique_name()
            self._bus.call_sync(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (bus_name,)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            logger.debug(f"Registered with StatusNotifierWatcher: {bus_name}")
        except Exception as e:
            logger.error(f"Failed to register with StatusNotifierWatcher: {e}")

    def _handle_sni_method_call(
        self, connection, sender, object_path, interface_name, method_name, parameters, invocation
    ):
        """Handle StatusNotifierItem method calls."""
        if method_name == "Activate":
            GLib.idle_add(self._on_open)
            invocation.return_value(None)
        elif method_name == "SecondaryActivate":
            GLib.idle_add(self._on_open)
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, f"Unknown method: {method_name}"
            )

    def _handle_sni_get_property(self, connection, sender, object_path, interface_name, property_name):
        """Handle StatusNotifierItem property gets."""
        if property_name == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        elif property_name == "Id":
            return GLib.Variant("s", "livestream-list")
        elif property_name == "Title":
            return GLib.Variant("s", "Livestream List")
        elif property_name == "Status":
            return GLib.Variant("s", self._status)
        elif property_name == "IconName":
            # Return empty string to force use of IconPixmap
            return GLib.Variant("s", "")
        elif property_name == "IconPixmap":
            # Return array of (width, height, pixel_data) tuples
            # pixel_data is passed as a list of bytes
            pixmaps = []
            for width, height, data in self._icon_pixmaps:
                pixmaps.append((width, height, list(data)))
            return GLib.Variant("a(iiay)", pixmaps)
        elif property_name == "Menu":
            return GLib.Variant("o", self._menu_path)
        return None

    def _handle_menu_method_call(
        self, connection, sender, object_path, interface_name, method_name, parameters, invocation
    ):
        """Handle DBusMenu method calls."""
        if method_name == "GetLayout":
            parent_id, depth, props = parameters.unpack()
            layout = self._build_menu_layout()
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._menu_revision, layout)))
        elif method_name == "GetGroupProperties":
            ids, prop_names = parameters.unpack()
            # Return empty array - properties are in GetLayout
            invocation.return_value(GLib.Variant("(a(ia{sv}))", ([],)))
        elif method_name == "AboutToShow":
            item_id = parameters.unpack()[0]
            # Return False - no update needed before showing
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method_name == "Event":
            item_id, event_id, data, timestamp = parameters.unpack()
            self._handle_menu_event(item_id, event_id)
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, f"Unknown method: {method_name}"
            )

    def _handle_menu_get_property(self, connection, sender, object_path, interface_name, property_name):
        """Handle DBusMenu property gets."""
        if property_name == "Version":
            return GLib.Variant("u", 3)
        elif property_name == "TextDirection":
            return GLib.Variant("s", "ltr")
        elif property_name == "Status":
            return GLib.Variant("s", "normal")
        elif property_name == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    def _build_menu_layout(self):
        """Build the menu layout for DBusMenu."""
        notif_enabled = self._get_notifications_enabled()

        # Build menu items as (id, properties, children) tuples
        # Children array uses 'av' type but items should be structs wrapped in single variant
        children = [
            # Open item
            GLib.Variant("(ia{sv}av)", (
                self.MENU_ID_OPEN,
                {"label": GLib.Variant("s", "Open Livestream List")},
                []
            )),
            # Separator
            GLib.Variant("(ia{sv}av)", (
                self.MENU_ID_SEPARATOR1,
                {"type": GLib.Variant("s", "separator")},
                []
            )),
            # Notifications toggle
            GLib.Variant("(ia{sv}av)", (
                self.MENU_ID_NOTIFICATIONS,
                {
                    "label": GLib.Variant("s", "Notifications"),
                    "toggle-type": GLib.Variant("s", "checkmark"),
                    "toggle-state": GLib.Variant("i", 1 if notif_enabled else 0),
                },
                []
            )),
            # Separator
            GLib.Variant("(ia{sv}av)", (
                self.MENU_ID_SEPARATOR2,
                {"type": GLib.Variant("s", "separator")},
                []
            )),
            # Quit item
            GLib.Variant("(ia{sv}av)", (
                self.MENU_ID_QUIT,
                {"label": GLib.Variant("s", "Quit")},
                []
            )),
        ]

        return (0, {"children-display": GLib.Variant("s", "submenu")}, children)

    def _handle_menu_event(self, item_id: int, event_id: str) -> None:
        """Handle menu item events."""
        if event_id != "clicked":
            return

        if item_id == self.MENU_ID_OPEN:
            GLib.idle_add(self._on_open)
        elif item_id == self.MENU_ID_NOTIFICATIONS:
            current = self._get_notifications_enabled()
            GLib.idle_add(lambda: self._set_notifications_enabled(not current))
            self._menu_revision += 1
            # Emit layout updated signal
            if self._bus:
                self._bus.emit_signal(
                    None,
                    self._menu_path,
                    "com.canonical.dbusmenu",
                    "LayoutUpdated",
                    GLib.Variant("(ui)", (self._menu_revision, 0)),
                )
        elif item_id == self.MENU_ID_QUIT:
            GLib.idle_add(self._on_quit)

    def update_notifications_state(self, enabled: bool) -> None:
        """Update the notifications checkbox state."""
        self._menu_revision += 1
        if self._bus:
            self._bus.emit_signal(
                None,
                self._menu_path,
                "com.canonical.dbusmenu",
                "LayoutUpdated",
                GLib.Variant("(ui)", (self._menu_revision, 0)),
            )

    def is_available(self) -> bool:
        """Check if tray icon was successfully created."""
        return self._available

    def show(self) -> None:
        """Show the tray icon."""
        if self._bus and self._available:
            self._status = "Active"
            self._bus.emit_signal(
                None,
                self._item_path,
                "org.kde.StatusNotifierItem",
                "NewStatus",
                GLib.Variant("(s)", (self._status,)),
            )

    def hide(self) -> None:
        """Hide the tray icon."""
        if self._bus and self._available:
            self._status = "Passive"
            self._bus.emit_signal(
                None,
                self._item_path,
                "org.kde.StatusNotifierItem",
                "NewStatus",
                GLib.Variant("(s)", (self._status,)),
            )

    def destroy(self) -> None:
        """Clean up the tray icon."""
        if self._bus:
            if self._sni_registration_id:
                self._bus.unregister_object(self._sni_registration_id)
            if self._menu_registration_id:
                self._bus.unregister_object(self._menu_registration_id)
        self._available = False
