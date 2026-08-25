"""Window position persistence on KDE Wayland, via KWin scripting.

On a native Wayland session a client is never told its absolute position and
``QWidget.move()`` is ignored by the compositor, so Qt alone cannot remember
where a window was closed. KWin, however, exposes ``org.kde.kwin.Scripting``
on the session bus: a loaded script can both read a window's true geometry and
set it. This module drives that interface.

The geometry helpers here are deliberately free of Qt imports so they can be
unit-tested without a running QApplication.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import ClassInfo, QObject, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PySide6.QtGui import QGuiApplication

# Our Wayland app_id, matching the Flatpak application id and the installed
# .desktop file. KWin reports this as a window's `resourceClass`. Set via
# QGuiApplication.setDesktopFileName() so it is identical for native and
# Flatpak runs.
WAYLAND_APP_ID = "app.livestreamlist.LivestreamListQt"

# Role names for the windows whose position is persisted.
PLACEMENT_ROLE_MAIN = "main"
PLACEMENT_ROLE_CHAT = "chat"

# Height of the strip at the top of a window that must remain grabbable. If no
# part of it overlaps a screen the user cannot drag the window back into view.
TITLEBAR_HEIGHT = 40


@dataclass(frozen=True)
class Rect:
    """A rectangle in KWin's logical (scaled) coordinate space."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def intersects(self, other: Rect) -> bool:
        """True if this rectangle shares at least one pixel with ``other``."""
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )


def is_titlebar_reachable(window: Rect, screens: Sequence[Rect]) -> bool:
    """True if the window's titlebar strip overlaps any connected screen.

    False means the saved position is unusable — the monitor it lived on is
    gone, or it sits above the top edge — and the caller should let the
    compositor place the window instead.
    """
    titlebar = Rect(
        x=window.x,
        y=window.y,
        width=window.width,
        height=min(TITLEBAR_HEIGHT, window.height),
    )
    return any(titlebar.intersects(screen) for screen in screens)


# Qt joins a window title and the application display name with a spaced
# EM DASH (U+2014) when composing the title the compositor sees.
_CAPTION_SEPARATOR = " — "


def expected_captions(title: str, display_name: str) -> list[str]:
    """Captions KWin may report for a Qt window with this title.

    Qt appends ``applicationDisplayName()`` to the window title unless the two
    already match. The bare title is included as a fallback in case the
    display name is not applied. Ordered most-specific first.
    """
    if not display_name or title == display_name:
        return [title]
    return [f"{title}{_CAPTION_SEPARATOR}{display_name}", title]


def resolve_placement(saved: Rect | None, screens: Sequence[Rect]) -> Rect | None:
    """Decide whether a saved geometry should be restored.

    Returns ``saved`` when its titlebar is reachable on the current screens,
    or ``None`` to emit no placement at all — which leaves the window to
    KWin's own placement policy (on this user's setup, under the mouse).
    """
    if saved is None:
        return None
    # Before this feature existed the app persisted Wayland's bogus (0, 0), so
    # an exact origin means "never actually learned", not "top-left corner".
    if saved.x == 0 and saved.y == 0:
        return None
    if not is_titlebar_reachable(saved, screens):
        return None
    return saved


def caption_of(title: str, display_name: str) -> str:
    """The caption KWin is expected to report for a Qt window."""
    return expected_captions(title, display_name)[0]


class PlacementRegistry:
    """Tracks which windows to place, and where KWin says they actually are.

    Windows are keyed by a caller-chosen ``role`` ("main", "chat"). KWin only
    knows captions, so the registry translates between the two and remembers
    the last geometry reported for each role.
    """

    def __init__(self, display_name: str) -> None:
        self._display_name = display_name
        self._role_by_caption: dict[str, str] = {}
        self._pending: dict[str, Rect] = {}
        self._geometry: dict[str, Rect] = {}

    def register(
        self,
        role: str,
        title: str,
        saved: Rect | None,
        screens: Sequence[Rect],
    ) -> None:
        """Declare a window, ideally before it is shown.

        ``saved`` is the geometry from settings; it is applied only if it is
        still reachable on ``screens``.
        """
        for caption in expected_captions(title, self._display_name):
            self._role_by_caption[caption] = role
        placement = resolve_placement(saved, screens)
        if placement is not None:
            self._pending[caption_of(title, self._display_name)] = placement

    def known_captions(self) -> list[str]:
        """Every caption that maps to a registered window."""
        return list(self._role_by_caption)

    def pending_placements(self) -> dict[str, Rect]:
        """Caption-to-position instructions not yet carried out by KWin."""
        return dict(self._pending)

    def note_report(self, caption: str, rect: Rect) -> str | None:
        """Record geometry KWin reported. Returns the role, or None if unknown."""
        role = self._role_by_caption.get(caption)
        if role is None:
            return None
        self._geometry[role] = rect
        # The window exists now, so its placement has either been applied or
        # missed its chance. Either way it must not be re-applied on a later
        # script reload, which would yank the window back.
        self._pending.pop(caption, None)
        return role

    def geometry(self, role: str) -> Rect | None:
        """Last frame geometry KWin reported for ``role``, or None."""
        return self._geometry.get(role)

    def position(self, role: str) -> tuple[int, int] | None:
        """Last position KWin reported for ``role``, or None if never seen.

        Position only, deliberately. KWin reports the frame rect, which
        includes the titlebar, while Qt's ``resize()`` takes the client size —
        round-tripping KWin's height through ``resize()`` would grow the
        window by the titlebar height on every launch.
        """
        rect = self._geometry.get(role)
        return None if rect is None else (rect.x, rect.y)


def build_config(
    *,
    app_id: str,
    captions: Sequence[str],
    placements: Mapping[str, Rect],
    service: str,
    path: str,
    interface: str,
) -> dict[str, Any]:
    """Build the JSON payload embedded in the generated KWin script.

    Windows are identified by ``app_id`` (the Wayland app_id, which KWin
    reports as ``resourceClass``) narrowed to ``captions``. Note this is
    deliberately NOT the process id: inside a Flatpak sandbox ``os.getpid()``
    returns the namespaced pid while KWin reports the host pid, so a pid
    filter matches nothing and silently places no windows.

    ``placements`` maps a KWin caption to the geometry to restore. Only the
    position is passed through: Qt's ``resize()`` works on Wayland, so size is
    left alone and we avoid fighting the toolkit over frame-vs-client extents.
    """
    return {
        "appId": app_id,
        "captions": list(captions),
        "service": service,
        "path": path,
        "interface": interface,
        "placements": {caption: {"x": rect.x, "y": rect.y} for caption, rect in placements.items()},
    }


# Applies saved positions as windows map, and reports true geometry back over
# D-Bus. `pid` scoping guarantees the script only ever touches our own windows.
_SCRIPT_BODY = """
(function () {
    var lastReported = {};

    function report(w) {
        var g = w.frameGeometry;
        var x = Math.round(g.x), y = Math.round(g.y);
        var width = Math.round(g.width), height = Math.round(g.height);
        var key = w.caption;
        var stamp = x + ":" + y + ":" + width + ":" + height;
        if (lastReported[key] === stamp) {
            return;
        }
        lastReported[key] = stamp;
        callDBus(CONFIG.service, CONFIG.path, CONFIG.interface,
                 "reportGeometry", key, x, y, width, height);
    }

    function attach(w) {
        if (!w || !w.normalWindow || w.resourceClass !== CONFIG.appId) {
            return;
        }
        if (CONFIG.captions.indexOf(w.caption) === -1) {
            return;
        }
        var target = CONFIG.placements[w.caption];
        if (target) {
            var g = w.frameGeometry;
            w.frameGeometry = {
                x: target.x, y: target.y, width: g.width, height: g.height
            };
        }
        report(w);

        // Reporting on every frameGeometryChanged would fire once per frame
        // during a drag, so suppress it while an interactive move is running
        // and report once when it finishes. frameGeometryChanged still covers
        // tiling, keyboard moves and programmatic changes.
        var interactive = false;
        if (w.interactiveMoveResizeStarted) {
            w.interactiveMoveResizeStarted.connect(function () {
                interactive = true;
            });
        }
        if (w.interactiveMoveResizeFinished) {
            w.interactiveMoveResizeFinished.connect(function () {
                interactive = false;
                report(w);
            });
        }
        if (w.frameGeometryChanged) {
            w.frameGeometryChanged.connect(function () {
                if (!interactive) {
                    report(w);
                }
            });
        }
    }

    var existing = workspace.windowList();
    for (var i = 0; i < existing.length; i++) {
        attach(existing[i]);
    }
    workspace.windowAdded.connect(attach);
})();
"""


def build_script(config: Mapping[str, Any]) -> str:
    """Render the KWin script that applies ``config``.

    The payload is ASCII-escaped JSON so the file is encoding-agnostic when
    KWin reads it, and so captions — which are user-influenced text — cannot
    break out of the surrounding JavaScript.
    """
    payload = json.dumps(config, ensure_ascii=True, sort_keys=True)
    return f"var CONFIG = {payload};\n{_SCRIPT_BODY}"


# --- KWin driver ------------------------------------------------------------

KWIN_SERVICE = "org.kde.KWin"
KWIN_SCRIPTING_PATH = "/Scripting"
KWIN_SCRIPTING_INTERFACE = "org.kde.kwin.Scripting"

# Registered under our own PID so `--allow-multiple` instances don't collide.
DBUS_SERVICE_PREFIX = "app.livestreamlist.Placement"
DBUS_OBJECT_PATH = "/Placement"
DBUS_INTERFACE = "app.livestreamlist.Placement"

SCRIPT_NAME = "livestream-list-qt-placement"
SCRIPT_FILENAME = "kwin-placement.js"

logger = logging.getLogger(__name__)


# PySide6 ships a stub for ClassInfo that does not model its decorator form;
# the call is correct at runtime and is what exports the interface name KWin
# addresses in callDBus().
@ClassInfo({"D-Bus Interface": DBUS_INTERFACE})  # type: ignore[call-arg]
class _GeometryReceiver(QObject):  # type: ignore[operator]
    """D-Bus object the KWin script calls back into."""

    reported = Signal(str, int, int, int, int)  # caption, x, y, width, height

    @Slot(str, int, int, int, int)
    def reportGeometry(  # noqa: N802 - D-Bus method name
        self, caption: str, x: int, y: int, width: int, height: int
    ) -> None:
        self.reported.emit(caption, x, y, width, height)


class KWinPlacement(QObject):
    """Restores and tracks window positions through KWin's scripting API.

    Every method is a no-op when KWin is not on the session bus, so callers
    can use this unconditionally on any platform.
    """

    def __init__(
        self,
        script_dir: Path,
        display_name: str,
        app_id: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._script_path = script_dir / SCRIPT_FILENAME
        self._registry = PlacementRegistry(display_name)
        self._app_id = app_id
        # A random token, not the pid: inside Flatpak every instance sees pid
        # 2, so pid-suffixed names would collide under --allow-multiple.
        self._service_name = f"{DBUS_SERVICE_PREFIX}.i{uuid.uuid4().hex[:12]}"
        self._receiver: _GeometryReceiver | None = None
        self._available: bool | None = None
        self._service_registered = False

    # -- availability --

    @property
    def available(self) -> bool:
        """True if KWin's scripting interface is reachable on the session bus."""
        if self._available is None:
            self._available = self._probe_kwin()
        return self._available

    @staticmethod
    def _probe_kwin() -> bool:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        interface = bus.interface()
        if interface is None:
            return False
        return bool(interface.isServiceRegistered(KWIN_SERVICE).value())

    # -- public API --

    def register_window(self, role: str, title: str, saved: Rect | None) -> None:
        """Declare a window and its saved geometry, before showing it."""
        if not self.available:
            return
        self._registry.register(role, title, saved, _screen_rects())
        self._sync()

    def position(self, role: str) -> tuple[int, int] | None:
        """Last true position KWin reported for ``role``, or None.

        Size is not returned on purpose — see ``PlacementRegistry.position``.
        """
        return self._registry.position(role)

    def shutdown(self) -> None:
        """Unload the KWin script and release the D-Bus name."""
        if self._service_registered:
            self._call_kwin("unloadScript", SCRIPT_NAME)
            bus = QDBusConnection.sessionBus()
            bus.unregisterObject(DBUS_OBJECT_PATH)
            bus.unregisterService(self._service_name)
            self._service_registered = False

    # -- internals --

    def _sync(self) -> None:
        """Write the current script and (re)load it into KWin."""
        if not self._ensure_service():
            return
        config = build_config(
            app_id=self._app_id,
            captions=self._registry.known_captions(),
            placements=self._registry.pending_placements(),
            service=self._service_name,
            path=DBUS_OBJECT_PATH,
            interface=DBUS_INTERFACE,
        )
        try:
            self._script_path.parent.mkdir(parents=True, exist_ok=True)
            self._script_path.write_text(build_script(config), encoding="utf-8")
        except OSError as exc:
            logger.warning("kwin_placement: could not write script: %s", exc)
            return
        # Reloading re-attaches to windows that already exist; pending
        # placements are cleared as they are reported, so nothing moves twice.
        self._call_kwin("unloadScript", SCRIPT_NAME)
        self._call_kwin("loadScript", str(self._script_path), SCRIPT_NAME)
        self._call_kwin("start")

    def _ensure_service(self) -> bool:
        """Register our D-Bus callback object, once."""
        if self._service_registered:
            return True
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        self._receiver = _GeometryReceiver(self)
        self._receiver.reported.connect(self._on_reported)
        if not bus.registerService(self._service_name):
            logger.warning("kwin_placement: could not own %s", self._service_name)
            return False
        if not bus.registerObject(
            DBUS_OBJECT_PATH, self._receiver, QDBusConnection.RegisterOption.ExportAllSlots
        ):
            logger.warning("kwin_placement: could not export %s", DBUS_OBJECT_PATH)
            bus.unregisterService(self._service_name)
            return False
        self._service_registered = True
        return True

    def _on_reported(self, caption: str, x: int, y: int, width: int, height: int) -> None:
        role = self._registry.note_report(caption, Rect(x=x, y=y, width=width, height=height))
        if role:
            logger.debug("kwin_placement: %s at %d,%d %dx%d", role, x, y, width, height)

    def _call_kwin(self, method: str, *args: object) -> None:
        bus = QDBusConnection.sessionBus()
        iface = QDBusInterface(KWIN_SERVICE, KWIN_SCRIPTING_PATH, KWIN_SCRIPTING_INTERFACE, bus)
        if not iface.isValid():
            logger.debug("kwin_placement: KWin scripting interface unavailable")
            return
        reply = iface.call(method, *args)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            logger.debug("kwin_placement: %s failed: %s", method, reply.errorMessage())


def _screen_rects() -> list[Rect]:
    """Current screen layout in the same logical coordinate space as KWin."""
    rects = []
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        rects.append(Rect(x=geo.x(), y=geo.y(), width=geo.width(), height=geo.height()))
    return rects
