#!/usr/bin/env python3
"""Capture screenshots of the app with sample data for README/docs.

Usage:
    # With a real display (dev machine)
    python scripts/capture_screenshots.py

    # With Qt offscreen platform (CI)
    QT_QPA_PLATFORM=offscreen python scripts/capture_screenshots.py

Screenshots are saved to docs/screenshots/.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add src to path so we can import the app
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow

from livestream_list.chat.emotes.cache import EmoteCache
from livestream_list.chat.emotes.image import ImageRef, ImageSet
from livestream_list.chat.models import ChatBadge, ChatEmote, ChatMessage, ChatUser
from livestream_list.core.models import Channel, Livestream, StreamPlatform, UIStyle
from livestream_list.core.monitor import StreamMonitor
from livestream_list.core.settings import BuiltinChatSettings, Settings, ThemeMode
from livestream_list.gui.theme import ThemeManager, get_app_stylesheet

OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "screenshots"

# ── Sample stream data ──────────────────────────────────────────────


def create_sample_data() -> list[tuple[Channel, Livestream]]:
    """Create realistic sample channels and livestreams."""
    now = datetime.now(timezone.utc)
    data = [
        (
            Channel("shroud", StreamPlatform.TWITCH, "shroud", favorite=True),
            Livestream(
                channel=None, live=True,
                title="CS2 Ranked Grind - Road to Global",
                game="Counter-Strike 2", viewers=42_831,
                start_time=now - timedelta(hours=3, minutes=22),
            ),
        ),
        (
            Channel("pokimane", StreamPlatform.TWITCH, "pokimane"),
            Livestream(
                channel=None, live=True,
                title="cozy morning stream | chatting & reacting",
                game="Just Chatting", viewers=18_204,
                start_time=now - timedelta(hours=1, minutes=45),
            ),
        ),
        (
            Channel("lirik", StreamPlatform.TWITCH, "LIRIK", favorite=True),
            Livestream(
                channel=None, live=True,
                title="NEW GAME MONDAY - trying out the new survival game",
                game="Schedule I", viewers=8_912,
                start_time=now - timedelta(hours=5, minutes=10),
            ),
        ),
        (
            Channel("summit1g", StreamPlatform.TWITCH, "summit1g"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(hours=6)),
        ),
        (
            Channel("UCX6OQ3DkcsbYNE6H8uQQuVA", StreamPlatform.YOUTUBE, "MrBeast", favorite=True),
            Livestream(
                channel=None, live=True,
                title="LIVE: Building 100 Houses For People In Need",
                game="Entertainment", viewers=127_450,
                start_time=now - timedelta(hours=2, minutes=15),
            ),
        ),
        (
            Channel("UC-lHJZR3Gqxm24_Vd_AJ5Yw", StreamPlatform.YOUTUBE, "PewDiePie"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(days=3)),
        ),
        (
            Channel("xqc", StreamPlatform.KICK, "xQc"),
            Livestream(
                channel=None, live=True,
                title="JUICING | variety gaming and reacting all day",
                game="Just Chatting", viewers=65_210,
                start_time=now - timedelta(hours=7, minutes=30),
            ),
        ),
        (
            Channel("nickmercs", StreamPlatform.KICK, "NICKMERCS", favorite=True),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(hours=14)),
        ),
        (
            Channel("exampleroom", StreamPlatform.CHATURBATE, "exampleroom"),
            Livestream(
                channel=None, live=True, title="", viewers=3_412,
                start_time=now - timedelta(hours=1),
            ),
        ),
        (
            Channel("anotherroom", StreamPlatform.CHATURBATE, "anotherroom"),
            Livestream(
                channel=None, live=False,
                last_live_time=now - timedelta(days=1, hours=8),
            ),
        ),
        # ── Additional channels to fill out the list ──
        (
            Channel("tarik", StreamPlatform.TWITCH, "tarik"),
            Livestream(
                channel=None, live=True,
                title="VALORANT ranked with the boys",
                game="VALORANT", viewers=31_420,
                start_time=now - timedelta(hours=4, minutes=5),
            ),
        ),
        (
            Channel("hasanabi", StreamPlatform.TWITCH, "HasanAbi"),
            Livestream(
                channel=None, live=True,
                title="NEWS & POLITICS | reacting to everything",
                game="Just Chatting", viewers=24_870,
                start_time=now - timedelta(hours=6, minutes=40),
            ),
        ),
        (
            Channel("kaicenat", StreamPlatform.TWITCH, "KaiCenat"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(hours=2)),
        ),
        (
            Channel("ironmouse", StreamPlatform.TWITCH, "ironmouse", favorite=True),
            Livestream(
                channel=None, live=True,
                title="SINGING STREAM!! come hang out",
                game="Music", viewers=12_340,
                start_time=now - timedelta(hours=2, minutes=50),
            ),
        ),
        (
            Channel("amouranth", StreamPlatform.KICK, "Amouranth"),
            Livestream(
                channel=None, live=True,
                title="IRL stream from the ranch",
                game="IRL", viewers=9_876,
                start_time=now - timedelta(hours=1, minutes=20),
            ),
        ),
        (
            Channel("trainwreckstv", StreamPlatform.KICK, "Trainwreckstv"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(days=2)),
        ),
        (
            Channel("UCHcMjkuAVZvHoc6Wmj-VCuA", StreamPlatform.YOUTUBE, "Valkyrae"),
            Livestream(
                channel=None, live=True,
                title="Late night gaming with friends!!",
                game="Fortnite", viewers=15_600,
                start_time=now - timedelta(hours=3, minutes=10),
            ),
        ),
        (
            Channel("UCq6VFHwMzcMXbuKyG7SQYIg", StreamPlatform.YOUTUBE, "Sykkuno"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(hours=18)),
        ),
        (
            Channel("thirdroom", StreamPlatform.CHATURBATE, "thirdroom"),
            Livestream(
                channel=None, live=True, title="", viewers=1_205,
                start_time=now - timedelta(hours=2, minutes=30),
            ),
        ),
        (
            Channel("sodapoppin", StreamPlatform.TWITCH, "sodapoppin"),
            Livestream(channel=None, live=False, last_live_time=now - timedelta(days=1)),
        ),
    ]
    for channel, livestream in data:
        livestream.channel = channel
    return data


# ── Emote helpers ───────────────────────────────────────────────────

# Real emote URLs from 7TV and Twitch CDN
# Format: name -> (provider, url)
EMOTE_URLS = {
    # 7TV emotes (static)
    "KEKW": ("7tv", "https://cdn.7tv.app/emote/01KKS9SWWTC1YVVCWVZ7RJ7B7M/2x.webp"),
    "monkaS": ("7tv", "https://cdn.7tv.app/emote/01KKTY56JYKWMRAHYEN8ZAXHC2/2x.webp"),
    "OMEGALUL": ("7tv", "https://cdn.7tv.app/emote/01KM7YKT43NBMNTAT2D2QBGGMN/2x.webp"),
    "Sadge": ("7tv", "https://cdn.7tv.app/emote/01KKNEHEJDY4QXKE0F8PSEGZ7X/2x.webp"),
    "EZ": ("7tv", "https://cdn.7tv.app/emote/01KM6S0VF4JHTCDVG56VJZ51K8/2x.webp"),
    "PogChamp": ("7tv", "https://cdn.7tv.app/emote/01KKZ80Y19BF5EHNASGZ0A584Z/2x.webp"),
    # 7TV emotes (animated)
    "catJAM": ("7tv", "https://cdn.7tv.app/emote/01KJY840CHFM7VRBZTX8FTQHTB/2x.webp"),
    "peepoHappy": ("7tv", "https://cdn.7tv.app/emote/01KE35Y6NCX9WJTJNEJDKG44YJ/2x.webp"),
    "Clap": ("7tv", "https://cdn.7tv.app/emote/01KKEGSN981ABHPCYABEC05SBH/2x.webp"),
    "pepeDS": ("7tv", "https://cdn.7tv.app/emote/01F7A76NER000AYA348VR68XJT/2x.webp"),
    # Twitch global emotes
    "Kappa": ("twitch", "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/2.0"),
    "LUL": ("twitch", "https://static-cdn.jtvnw.net/emoticons/v2/425618/default/dark/2.0"),
    "4Head": ("twitch", "https://static-cdn.jtvnw.net/emoticons/v2/354/default/dark/2.0"),
}

# Track which emotes are animated (populated during download)
_animated_emotes: set[str] = set()


def _download_emote(name: str, url: str) -> bytes | None:
    """Download an emote image from CDN."""
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.read()
    except Exception as e:
        print(f"  Warning: failed to download {name}: {e}")
        return None


def setup_emote_cache() -> EmoteCache:
    """Create an EmoteCache pre-populated with real emotes from CDN."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImageReader

    cache = EmoteCache()

    print("  Downloading emotes from CDN...")
    for name, (provider, url) in EMOTE_URLS.items():
        data = _download_emote(name, url)
        if not data:
            continue

        key = f"emote:{provider}:{name}"
        qdata = QByteArray(data)
        buf = QBuffer(qdata)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buf)

        if reader.supportsAnimation() and reader.imageCount() > 1:
            # Animated emote — extract all frames
            _animated_emotes.add(name)
            frames = []
            delays = []
            while reader.canRead():
                delay = max(reader.nextImageDelay(), 20)
                qimage = reader.read()
                if qimage.isNull():
                    break
                frames.append(QPixmap.fromImage(qimage))
                delays.append(delay)
            if frames:
                cache._animated[key] = frames
                cache._frame_delays[key] = delays
                cache._memory[key] = frames[0]  # Static fallback
                print(f"    {name}: {len(frames)} frames (animated)")
        else:
            # Static emote
            buf.seek(0)
            reader2 = QImageReader(buf)
            qimage = reader2.read()
            if not qimage.isNull():
                cache._memory[key] = QPixmap.fromImage(qimage)
                print(f"    {name}: static")
            else:
                print(f"    {name}: decode failed")

        buf.close()

    return cache


def make_emote(name: str, cache: EmoteCache) -> ChatEmote:
    """Create a ChatEmote with a pre-loaded ImageSet."""
    provider = EMOTE_URLS.get(name, ("7tv", ""))[0]
    key = f"emote:{provider}:{name}"
    animated = name in _animated_emotes
    image_ref = ImageRef(scale=2, key=key, url="", store=cache, animated=animated)
    image_set = ImageSet({2: image_ref})
    return ChatEmote(
        id=name, name=name, url_template="", provider=provider,
        image_set=image_set,
    )


def emote_pos(text: str, emote_name: str, cache: EmoteCache):
    """Find emote position in text and return (start, end, ChatEmote) tuple."""
    start = text.find(emote_name)
    if start == -1:
        return None
    return (start, start + len(emote_name), make_emote(emote_name, cache))


# ── Sample chat messages ────────────────────────────────────────────


def create_sample_chat_messages(cache: EmoteCache) -> list[ChatMessage]:
    """Create realistic chat messages for screenshot."""
    now = datetime.now(timezone.utc)
    t = now - timedelta(minutes=5)

    def ts(offset_s: int) -> datetime:
        return t + timedelta(seconds=offset_s)

    def ep(text, name):
        """Shorthand for emote_pos."""
        return emote_pos(text, name, cache)

    msgs = []

    def msg(id, ts_offset, text, user, emote_names=None, **kwargs):
        positions = []
        if emote_names:
            for name in emote_names:
                pos = ep(text, name)
                if pos:
                    positions.append(pos)
        msgs.append(ChatMessage(
            id=id, platform=StreamPlatform.TWITCH, timestamp=ts(ts_offset),
            text=text, user=user, emote_positions=positions, **kwargs,
        ))

    # Users
    u_gamer = ChatUser(
        id="u1", name="gamerfan42", display_name="GamerFan42",
        platform=StreamPlatform.TWITCH, color="#1E90FF", badges=[],
    )
    u_pixel = ChatUser(
        id="u2", name="pixelwarrior", display_name="PixelWarrior",
        platform=StreamPlatform.TWITCH, color="#FF6347",
        badges=[ChatBadge(id="subscriber/12", name="subscriber", image_url="")],
    )
    u_sniper = ChatUser(
        id="u3", name="streamsniper99", display_name="StreamSniper99",
        platform=StreamPlatform.TWITCH, color="#9ACD32", badges=[],
    )
    u_owl = ChatUser(
        id="u4", name="nightowl_tv", display_name="NightOwl_TV",
        platform=StreamPlatform.TWITCH, color="#DDA0DD",
        badges=[
            ChatBadge(id="moderator/1", name="moderator", image_url=""),
            ChatBadge(id="subscriber/24", name="subscriber", image_url=""),
        ],
    )
    u_new = ChatUser(
        id="u5", name="newviewer2024", display_name="NewViewer2024",
        platform=StreamPlatform.TWITCH, color="#FFD700", badges=[],
    )
    u_tips = ChatUser(
        id="u6", name="csgo_tips", display_name="CSGO_Tips",
        platform=StreamPlatform.TWITCH, color="#00CED1", badges=[],
    )
    u_hype = ChatUser(
        id="u7", name="hypemaster", display_name="HypeMaster",
        platform=StreamPlatform.TWITCH, color="#FF4500", badges=[],
    )
    u_clip = ChatUser(
        id="u8", name="clipchamp", display_name="ClipChamp",
        platform=StreamPlatform.TWITCH, color="#20B2AA",
        badges=[ChatBadge(id="subscriber/3", name="subscriber", image_url="")],
    )
    u_map = ChatUser(
        id="u9", name="mapexpert", display_name="MapExpert",
        platform=StreamPlatform.TWITCH, color="#BA55D3", badges=[],
    )
    u_frag = ChatUser(
        id="u10", name="fragmovie", display_name="FragMovie",
        platform=StreamPlatform.TWITCH, color="#FF1493", badges=[],
    )

    # Messages with emotes
    msg("m1", 0, "let's gooo shroud is live! PogChamp", u_gamer, ["PogChamp"])
    msg("m2", 3, "that clutch was insane LUL", u_pixel, ["LUL"])
    msg("m3", 7, "@GamerFan42 yeah he's been playing really well today", u_sniper)
    msg("m4", 12, "GG EZ no re Kappa", u_owl, ["EZ", "Kappa"])
    msg("m5", 15, "first time watching, this is amazing peepoHappy",
        u_new, ["peepoHappy"], is_first_message=True)
    msg("m6", 18, "anyone know what sens he uses?", u_tips)
    msg("m7", 22, "subscribed for 6 months! catJAM", u_pixel, ["catJAM"],
        is_system=True,
        system_text="PixelWarrior subscribed at Tier 1. They've subscribed for 6 months!")
    msg("m8", 25, "LET'S GO SHROUD PogChamp Clap", u_hype, ["PogChamp", "Clap"])
    msg("m9", 30, "400 IQ play right there KEKW", u_gamer, ["KEKW"],
        reply_parent_msg_id="m4", reply_parent_display_name="NightOwl_TV",
        reply_parent_text="GG EZ no re Kappa")
    msg("m10", 34, "can someone clip that? monkaS I missed it", u_clip, ["monkaS"])
    msg("m11", 38, "this map is so good for cs2", u_map)
    msg("m12", 42, "@NewViewer2024 welcome! peepoHappy you picked a great stream",
        u_owl, ["peepoHappy"])
    msg("m13", 46, "ace ace ace!!! OMEGALUL LETS GOOO pepeDS", u_frag, ["OMEGALUL", "pepeDS"])
    msg("m14", 50, "how does he make it look so easy Sadge", u_sniper, ["Sadge"])

    return msgs


# ── Mock Application ────────────────────────────────────────────────


class MockApplication(QObject):
    """Minimal mock of Application for MainWindow and PreferencesDialog."""

    stream_online = Signal(object)
    refresh_complete = Signal()
    refresh_error = Signal(str)

    def __init__(self, settings: Settings, monitor: StreamMonitor):
        super().__init__()
        self.settings = settings
        self.monitor = monitor
        self.streamlink = None
        self.chat_manager = None
        self._chat_window = None
        self._qt_app = QApplication.instance()
        self.tray_icon = None

    def styleSheet(self):  # noqa: N802
        return self._qt_app.styleSheet()

    def setStyleSheet(self, stylesheet):  # noqa: N802
        self._qt_app.setStyleSheet(stylesheet)

    def save_settings(self):
        pass

    def save_channels(self):
        pass

    def refresh(self, on_complete=None):
        if on_complete:
            on_complete()


# ── Helpers ─────────────────────────────────────────────────────────


def capture_screenshot(widget, filename: str) -> Path:
    """Capture a widget screenshot and save it."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    pixmap = widget.grab()
    pixmap.save(str(path))
    print(f"  Saved: {path}")
    return path


def apply_theme(settings, mode: ThemeMode, qt_app, window=None):
    """Switch theme mode and apply stylesheet."""
    from livestream_list.gui.theme import _stylesheet_cache

    settings.theme_mode = mode
    _stylesheet_cache.clear()  # Force stylesheet regeneration
    ThemeManager.set_settings(settings)
    if window:
        # _apply_theme checks if stylesheet changed, so clear app stylesheet first
        qt_app.setStyleSheet("")
        window._apply_theme()
    else:
        qt_app.setStyleSheet(get_app_stylesheet())
    qt_app.processEvents()


# ── Main ────────────────────────────────────────────────────────────


def main():
    print("Starting screenshot capture...")

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Livestream List (Qt)")

    # Create settings with defaults (no disk I/O)
    settings = Settings()

    # Create monitor and inject sample data
    monitor = StreamMonitor(settings)
    for channel, livestream in create_sample_data():
        key = channel.unique_key
        monitor._channels[key] = channel
        monitor._livestreams[key] = livestream

    # Start with dark theme
    apply_theme(settings, ThemeMode.DARK, qt_app)

    # Create mock app and main window
    mock_app = MockApplication(settings, monitor)

    from livestream_list.gui.main_window import MainWindow

    window = MainWindow(mock_app)
    window._initial_check_complete = True
    window.resize(540, 700)  # 900 * 0.6 = 540 (40% narrower)
    window.show()
    window.refresh_stream_list()
    qt_app.processEvents()

    def do_captures():
        print("\nCapturing screenshots...")

        # ── 1. Main window - dark theme ──
        capture_screenshot(window, "main-window-dark.png")

        # ── 2. Main window - light theme ──
        apply_theme(settings, ThemeMode.LIGHT, qt_app, window)
        window.refresh_stream_list()
        qt_app.processEvents()
        capture_screenshot(window, "main-window-light.png")

        # ── 3. Main window - compact mode (dark) ──
        apply_theme(settings, ThemeMode.DARK, qt_app, window)
        settings.ui_style = UIStyle.COMPACT_2
        window.resize(360, 700)  # 900 * 0.4 = 360 (60% narrower)
        if window._stream_delegate:
            window._stream_delegate.invalidate_size_cache()
        window.refresh_stream_list()
        qt_app.processEvents()
        capture_screenshot(window, "compact-mode.png")

        # Reset to default style and size
        settings.ui_style = UIStyle.DEFAULT
        window.resize(540, 700)
        if window._stream_delegate:
            window._stream_delegate.invalidate_size_cache()
        window.refresh_stream_list()
        qt_app.processEvents()

        # ── 4–5. Chat window + animated GIF (dark theme) ──
        # Share the emote cache between static and animated captures
        emote_cache = setup_emote_cache()
        capture_chat_window(settings, qt_app, emote_cache)
        capture_chat_animated_gif(settings, qt_app, emote_cache)

        # ── 6. mpv player (real stream capture) ──
        capture_mpv_playback()

        # ── 7. Preferences dialogs (dark theme) ──
        capture_preferences(mock_app, window, qt_app)

        print("\nDone! Screenshots saved to docs/screenshots/")
        qt_app.quit()

    QTimer.singleShot(500, do_captures)
    qt_app.exec()


def capture_chat_window(settings, qt_app, emote_cache):
    """Capture a standalone chat widget with sample messages and emotes."""
    from livestream_list.gui.chat.chat_widget import ChatWidget

    # Create a livestream for the chat title banner
    now = datetime.now(timezone.utc)
    channel = Channel("shroud", StreamPlatform.TWITCH, "shroud")
    livestream = Livestream(
        channel=channel, live=True,
        title="CS2 Ranked Grind - Road to Global",
        game="Counter-Strike 2", viewers=42_831,
        start_time=now - timedelta(hours=3, minutes=22),
    )

    chat_settings = BuiltinChatSettings(
        font_size=10,
        show_timestamps=True,
        timestamp_format="24h",
        show_alternating_rows=True,
    )

    # Wrap ChatWidget in a QMainWindow for proper window chrome
    chat_window = QMainWindow()
    chat_window.setWindowTitle("Chat - shroud")
    chat_window.resize(450, 600)

    chat_widget = ChatWidget(
        channel_key="twitch:shroud",
        livestream=livestream,
        settings=chat_settings,
        authenticated=False,
        parent=chat_window,
    )

    # Inject emote cache into the delegate so it can render emote pixmaps
    chat_widget._delegate.set_image_store(emote_cache)

    chat_window.setCentralWidget(chat_widget)
    chat_window.show()
    qt_app.processEvents()

    # Switch from "Connecting..." to message list view
    chat_widget.set_connected()

    # Add sample messages with emotes
    messages = create_sample_chat_messages(emote_cache)
    chat_widget._model.add_messages(messages)

    # Scroll to bottom
    qt_app.processEvents()
    chat_widget._list_view.scrollToBottom()
    qt_app.processEvents()

    # Apply theme styling to the chat widget
    from livestream_list.gui.theme import get_theme

    theme = get_theme()
    chat_window.setStyleSheet(f"""
        QMainWindow {{ background-color: {theme.chat_bg}; }}
    """)
    qt_app.processEvents()

    capture_screenshot(chat_window, "chat-window.png")
    chat_window.close()


def capture_preferences(mock_app, parent_window, qt_app):
    """Capture preferences dialog tabs."""
    from livestream_list.gui.dialogs.preferences.dialog import PreferencesDialog

    tabs_to_capture = [
        (0, "preferences-general.png"),
        (2, "preferences-chat.png"),
        (3, "preferences-appearance.png"),
    ]

    for tab_index, filename in tabs_to_capture:
        dialog = PreferencesDialog(parent_window, mock_app, initial_tab=tab_index)
        dialog.resize(600, 550)
        dialog.show()
        qt_app.processEvents()
        capture_screenshot(dialog, filename)
        dialog.close()
        qt_app.processEvents()


def capture_mpv_playback():
    """Capture a real live stream via streamlink+ffmpeg and create an mpv-style GIF."""
    import glob
    import json
    import shutil
    import subprocess
    import tempfile
    import urllib.request

    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "mpv-playback.gif"

    # Find a live Rocket League streamer via Twitch GraphQL
    print("  Finding Rocket League stream...")
    query = (
        '{ game(name: "Rocket League") { streams(first: 1) '
        "{ edges { node { broadcaster { login displayName } } } } } }"
    )
    try:
        req = urllib.request.Request(
            "https://gql.twitch.tv/gql",
            data=json.dumps({"query": query}).encode(),
            headers={
                "Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko",
                "Content-Type": "application/json",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        edges = data["data"]["game"]["streams"]["edges"]
        if not edges:
            print("  No Rocket League streams live, skipping mpv capture")
            return
        streamer = edges[0]["node"]["broadcaster"]
        login = streamer["login"]
        display = streamer["displayName"]
    except Exception as e:
        print(f"  Failed to find stream: {e}, skipping mpv capture")
        return

    print(f"  Capturing stream: {display} ({login})")

    # Capture 2 seconds of video via streamlink + ffmpeg
    tmp_dir = tempfile.mkdtemp(prefix="mpv-capture-")
    try:
        subprocess.run(
            f"streamlink twitch.tv/{login} best --stdout 2>/dev/null | "
            f"ffmpeg -y -i pipe:0 -t 2 -vf 'fps=5,scale=640:360' "
            f"{tmp_dir}/frame_%03d.png",
            shell=True,
            capture_output=True,
            timeout=30,
        )
        frame_files = sorted(glob.glob(f"{tmp_dir}/frame_*.png"))
        if not frame_files:
            print("  No frames captured, skipping mpv capture")
            return

        print(f"  Captured {len(frame_files)} frames")

        # Load frames and add mpv-style OSD overlay
        osd_frames = []
        try:
            pil_font = ImageFont.truetype(
                "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf", 14
            )
            pil_font_small = ImageFont.truetype(
                "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf", 11
            )
        except OSError:
            pil_font = ImageFont.load_default()
            pil_font_small = pil_font

        for frame_path in frame_files:
            frame = PILImage.open(frame_path).convert("RGBA")
            overlay = PILImage.new("RGBA", frame.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            w, h = frame.size

            # OSD text (top-left)
            osd_lines = [
                f"Twitch: {display}",
                "1080p | 6.8 Mbps",
                "streamlink 8.2.1",
            ]
            y = 10
            for line in osd_lines:
                draw.text((11, y + 1), line, fill=(0, 0, 0, 200), font=pil_font)
                draw.text((10, y), line, fill=(255, 255, 255, 230), font=pil_font)
                y += 20

            # Bottom bar
            bar_h = 26
            draw.rectangle([(0, h - bar_h), (w, h)], fill=(0, 0, 0, 160))
            draw.text((8, h - 20), "03:22:15", fill=(200, 200, 200), font=pil_font_small)
            draw.text((w - 50, h - 20), "LIVE", fill=(200, 200, 200), font=pil_font_small)

            # Progress bar
            bar_y = h - 14
            draw.rectangle([(70, bar_y), (w - 60, bar_y + 2)], fill=(100, 100, 100))
            progress_w = int((w - 130) * 0.85)
            draw.rectangle([(70, bar_y), (70 + progress_w, bar_y + 2)], fill=(233, 69, 96))

            composited = PILImage.alpha_composite(frame, overlay).convert("RGB")
            # Quantize for smaller GIF
            osd_frames.append(
                composited.quantize(colors=128, method=PILImage.Quantize.MEDIANCUT).convert("RGB")
            )

        osd_frames[0].save(
            str(output_path),
            save_all=True,
            append_images=osd_frames[1:],
            duration=200,
            loop=0,
            optimize=True,
        )
        size_kb = output_path.stat().st_size // 1024
        print(f"  Saved: {output_path} ({size_kb}KB, {len(osd_frames)} frames)")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def capture_chat_animated_gif(settings, qt_app, emote_cache):
    """Capture animated GIF of chat with animated emotes cycling."""
    from PIL import Image as PILImage

    from livestream_list.gui.chat.chat_widget import ChatWidget

    now = datetime.now(timezone.utc)
    channel = Channel("shroud", StreamPlatform.TWITCH, "shroud")
    livestream = Livestream(
        channel=channel, live=True,
        title="CS2 Ranked Grind - Road to Global",
        game="Counter-Strike 2", viewers=42_831,
        start_time=now - timedelta(hours=3, minutes=22),
    )

    chat_settings = BuiltinChatSettings(
        font_size=10,
        show_timestamps=True,
        timestamp_format="24h",
        show_alternating_rows=True,
        animate_emotes=True,
    )

    chat_window = QMainWindow()
    chat_window.setWindowTitle("Chat - shroud")
    chat_window.resize(450, 600)

    chat_widget = ChatWidget(
        channel_key="twitch:shroud",
        livestream=livestream,
        settings=chat_settings,
        authenticated=False,
        parent=chat_window,
    )
    chat_widget._delegate.set_image_store(emote_cache)

    chat_window.setCentralWidget(chat_widget)
    chat_window.show()
    qt_app.processEvents()

    chat_widget.set_connected()
    messages = create_sample_chat_messages(emote_cache)
    chat_widget._model.add_messages(messages)
    qt_app.processEvents()
    chat_widget._list_view.scrollToBottom()
    qt_app.processEvents()

    from livestream_list.gui.theme import get_theme

    theme = get_theme()
    chat_window.setStyleSheet(f"QMainWindow {{ background-color: {theme.chat_bg}; }}")
    qt_app.processEvents()

    # Capture frames at different animation times
    # Find the longest animation cycle from the cache
    max_duration = 800  # fallback
    for key, delays in emote_cache._frame_delays.items():
        total = sum(delays)
        if total > max_duration:
            max_duration = total
    frame_interval_ms = 100  # 100ms per GIF frame
    num_frames = max(8, max_duration // frame_interval_ms)

    pil_frames = []
    for i in range(num_frames):
        elapsed = i * frame_interval_ms
        chat_widget._delegate.set_animation_frame(elapsed)
        chat_widget._list_view.viewport().update()
        qt_app.processEvents()

        # Grab the window as QPixmap -> convert to PIL Image
        qpixmap = chat_window.grab()
        qimage = qpixmap.toImage().convertToFormat(qpixmap.toImage().Format.Format_RGBA8888)
        pil_img = PILImage.frombytes(
            "RGBA",
            (qimage.width(), qimage.height()),
            qimage.constBits().tobytes(),
        )
        pil_frames.append(pil_img)

    # Save as animated GIF
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gif_path = OUTPUT_DIR / "chat-animated.gif"
    pil_frames[0].save(
        str(gif_path),
        save_all=True,
        append_images=pil_frames[1:],
        duration=frame_interval_ms,
        loop=0,
    )
    print(f"  Saved: {gif_path}")

    chat_window.close()


if __name__ == "__main__":
    main()
