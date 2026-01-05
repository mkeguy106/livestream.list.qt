# Livestream List - Linux

A Python GTK4/Libadwaita application for monitoring livestreams on Twitch, YouTube, and Kick. Inspired by the C# [Livestream.Monitor](https://github.com/laurencee/Livestream.Monitor) application.

## Project Overview

This is a Linux-native livestream monitoring application that tracks when your favorite streamers go live, sends desktop notifications, and integrates with Streamlink to launch streams in external players like mpv.

## Architecture

### Core Components

- **GTK4/Libadwaita GUI** - Native Linux desktop interface
- **Async HTTP with aiohttp** - Non-blocking API requests
- **Twitch Helix API + GraphQL** - Both authenticated and public data access
- **YouTube via yt-dlp** - Stream detection without API key (bundled in Flatpak)
- **Kick API** - Direct API calls for stream detection
- **OAuth 2.0 Implicit Grant Flow** - Local HTTP callback server on port 65432
- **Streamlink Integration** - Launch streams in external video players with process tracking
- **Desktop Notifications** - via desktop-notifier library

### File Structure

```
livestream.list.linux/
├── .github/workflows/
│   ├── flatpak.yml           # PR build testing
│   └── release.yml           # Release workflow (triggers on v* tags)
├── data/
│   ├── life.covert.livestreamList.desktop    # Desktop entry
│   ├── life.covert.livestreamList.svg        # App icon
│   ├── life.covert.livestreamList-symbolic.svg  # Symbolic icon for themes
│   └── life.covert.livestreamList.metainfo.xml  # AppStream metadata
├── src/livestream_list/
│   ├── api/
│   │   ├── base.py           # Abstract BaseApiClient class
│   │   ├── twitch.py         # Twitch Helix + GraphQL client
│   │   ├── oauth_server.py   # Local OAuth callback server (port 65432)
│   │   ├── youtube.py        # YouTube client using yt-dlp (no API key required)
│   │   └── kick.py           # Kick API client
│   ├── core/
│   │   ├── models.py         # Channel, Livestream, StreamPlatform dataclasses
│   │   ├── monitor.py        # StreamMonitor service for tracking channels
│   │   ├── settings.py       # Settings persistence (JSON)
│   │   ├── streamlink.py     # Streamlink launcher with process tracking
│   │   ├── chat.py           # Chat launcher for opening stream chat in browser (Twitch, Kick, YouTube)
│   │   └── autostart.py      # Launch on login (creates .desktop in ~/.config/autostart)
│   ├── gui/
│   │   ├── app.py            # Main GTK Application with threaded init
│   │   ├── main_window.py    # Main window, StreamRow, dialogs
│   │   └── tray.py           # System tray icon (StatusNotifierItem D-Bus)
│   ├── notifications/
│   │   └── notifier.py       # Desktop notification handling
│   ├── __version__.py        # Version (single source of truth)
│   ├── main.py               # Entry point
│   └── __init__.py
├── life.covert.livestreamList.yml  # Flatpak manifest
├── pyproject.toml
├── CHANGELOG.md
└── README.md
```

## Key Implementation Details

### Twitch API

Two client IDs are used:
- **Helix API** (authenticated): `phiay4sq36lfv9zu7cbqwz2ndnesfd8` (Streamlink Twitch GUI's registered app)
- **GraphQL API** (unauthenticated): `kimne78kx3ncx6brgo4mv6wki5h1ko`

GraphQL is used for public data (channel info lookup) when not authenticated. Helix API is used for authenticated requests (importing followed channels).

**Batched GraphQL Queries**: Stream status checks use batched GraphQL queries with aliases to query up to 35 channels per request. This significantly improves performance compared to individual requests per channel.

### YouTube API

YouTube uses **yt-dlp** for stream detection - no API key required:

- **yt-dlp** is bundled in the Flatpak and as a pip dependency
- Runs as subprocess: `yt-dlp --dump-json --no-download <url>`
- Detects live streams, gets title, viewers, video ID
- Video ID is used for live chat URL: `https://www.youtube.com/live_chat?v={video_id}`

**Channel Resolution**:
- Handles: `@username` → `https://www.youtube.com/@username/live`
- Plain URLs: `youtube.com/destiny` → auto-prefixed with `@`
- Channel IDs: `UC...` → `https://www.youtube.com/channel/{id}/live`

**Batch Processing**: YouTube channels are checked in batches of 5 to avoid overwhelming yt-dlp subprocess calls.

### OAuth Flow

1. Opens browser to Twitch authorization URL
2. Local HTTP server listens on `localhost:65432/redirect`
3. Uses threading.Event for cross-thread signaling (not asyncio.Event)
4. ReuseAddrHTTPServer with `allow_reuse_address = True` to avoid port conflicts
5. Twitch login/logout managed in Preferences → Accounts

### Threading Model

GTK4 requires UI updates on the main thread. Pattern used:
```python
def background_task():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Reset aiohttp session for this event loop
        client._session = None
        result = loop.run_until_complete(async_operation())
        GLib.idle_add(update_ui_callback, result)
    finally:
        client._session = None
        loop.close()

thread = threading.Thread(target=background_task, daemon=True)
thread.start()
```

**Critical**: Always set `client._session = None` before/after async operations in threads to avoid "Task got Future attached to a different loop" errors.

### Stream Process Tracking

StreamlinkLauncher tracks active streams via subprocess.Popen objects:
- `_active_streams: dict[str, tuple[subprocess.Popen, Livestream]]` - Maps channel_key to process
- `is_playing(channel_key)` - Check if stream is currently playing
- `stop_stream(channel_key)` - Terminate playback
- `cleanup_dead_processes()` - Remove finished processes, fire callbacks
- Periodic check every 2 seconds updates UI when player is closed

## Features

- **Channel Monitoring** - Add channels from Twitch, YouTube, and Kick
  - Paste channel URLs (e.g. `https://twitch.tv/username`) to auto-detect platform
  - Clipboard auto-paste when opening add dialog
- **Live Notifications** - Desktop notifications when streams go live
  - Only notifies when streams transition from offline to online
  - Suppressed on initial startup (no notification flurry on launch)
- **System Tray Icon** - Minimize to tray with StatusNotifierItem D-Bus protocol
  - Click to show/hide window, right-click menu (Open, Notifications toggle, Quit)
  - Theme-matching icon via IconPixmap (reads KDE panel color)
  - Works on KDE, XFCE, Cinnamon, Budgie, LXQt, MATE, GNOME (with AppIndicator extension)
- **Run in Background** - Keep running when window is closed
  - First-launch prompt to choose behavior (quit or run in background)
  - Continues receiving notifications while minimized to tray
- **Launch on Login** - Option to start automatically when you log in
  - Creates .desktop file in ~/.config/autostart
  - Detects Flatpak and uses correct Exec command
- **Stream Playback Tracking** - Shows "▶ Playing" indicator with stop button
- **Platform Filter** - Filter by platform (All, Twitch, YouTube, Kick)
- **Name Filter** - Filter by channel name with wildcard support (`*gaming*`)
- **Sort Options**: Name, Viewers, Playing, Last Seen, Time Live (default: Viewers)
  - All sorts prioritize live streams first
  - "Playing" sort puts currently playing streams at top
  - "Time Live" sort orders by stream duration (longest first)
  - List auto-refreshes when starting a stream if sorted by Playing
- **Hide Offline Filter** - Toggle to show only live streams
  - Shows "Checking stream status..." during initial load
  - Shows "All channels are offline" when all hidden
- **Last Seen** - Offline channels show when they were last live
  - Formats: "just now", "5m ago", "2h ago", "3d ago", "2mo ago", "1y ago"
  - Persisted to channels.json across app restarts
- **Platform Colors** - Color channel names by platform (Preferences → Appearance)
  - Twitch: Purple (#9146FF)
  - Kick: Green (#53FC18)
  - YouTube: Red (#FF0000)
- **UI Styles** - Default, Compact 1, Compact 2, Compact 3 (in Preferences → Appearance)
  - Compact modes reduce row height, margins, and spacing
  - Stop button and favorite star icons scale down in compact modes (14px/12px/10px)
  - Toolbar elements (checkboxes, labels) also scale in compact modes
- **Favorites** - Star button on each channel to mark as favorite
  - Favorites filter checkbox to show only favorite channels
  - Favorites persisted to channels.json
- **Window Size Persistence** - Window size saved on close and restored on launch
  - Can also set window size manually in Preferences
- **Selection Mode** - Multi-select with Select All/Deselect All, bulk delete
- **Export/Import** - Export and import channels and settings
  - Export includes app version and schema version for compatibility
  - Import can restore channels only or channels + settings
  - JSON format for easy inspection
- **Import Twitch Follows** - In Preferences → Accounts, with live progress feedback
- **Streamlink Launch** - Double-click to open stream with progress feedback
  - Shows "Launching..." → "Loading... (Xs)" → "Playing [name]"
  - Monitors process for up to 10 seconds
- **Chat Integration** - Open stream chat in browser (Twitch, Kick, YouTube)
  - Chat button on each stream row (scales with compact modes)
  - Browser selection: System Default, Chrome, Chromium, Edge, Firefox
  - Chat URL types: Popout (recommended), Embedded, Default (legacy) - Twitch only
  - YouTube chat uses video ID from yt-dlp (only works when stream is live)
  - Auto-open chat launches in parallel with stream (no delay)
  - Configurable in Preferences → Chat
- **Settings Persistence** - Sort mode, hide offline, UI style, refresh interval saved

## Configuration

Settings stored in: `~/.config/livestream-list/`
- `settings.json` - App settings and preferences
- `channels.json` - Saved channels list

Data stored in: `~/.local/share/livestream-list/`

### Settings Schema (settings.py)

```python
sort_mode: int = 1        # 0=Name, 1=Viewers, 2=Playing, 3=Last Seen, 4=Time Live
hide_offline: bool = False
favorites_only: bool = False
ui_style: int = 0         # 0=Default, 1=Compact 1, 2=Compact 2, 3=Compact 3
platform_colors: bool = True  # Color channel names by platform
refresh_interval: int = 60  # seconds
run_in_background: bool = False  # Keep running when window closed
launch_on_login: bool = False    # Start on login
has_asked_background: bool = False  # Whether first-launch prompt was shown
window.width: int = 1000
window.height: int = 700
chat.enabled: bool = True
chat.browser: str = "default"  # default, chrome, chromium, edge, firefox
chat.url_type: int = 0    # 0=Popout, 1=Embedded, 2=Default (legacy)
chat.auto_open: bool = False
channel_info.show_live_duration: bool = True
channel_info.show_viewers: bool = True
channel_icons.show_platform: bool = True
channel_icons.show_play: bool = True
channel_icons.show_favorite: bool = True
channel_icons.show_chat: bool = True
channel_icons.show_browser: bool = True
```

## UI Components

### StreamRow
Each channel row displays:
- Live indicator (🟢 live / ⚫ offline)
- Platform icon (T/Y/K) - colored by platform when enabled
- Channel name - colored by platform when enabled
- Last seen time (offline channels) or Live duration (live channels, hh:mm or dd:hh:mm)
- "▶ Playing" label (when stream is playing)
- Viewer count (when live)
- Game/title info (hidden in compact modes)
- Browser button (opens channel in browser, scales in compact modes)
- Chat button (opens chat in browser, scales in compact modes)
- Favorite star button (scales in compact modes)
- Play button (launches stream, scales in compact modes) / Stop button (when playing)
- Checkbox (in selection mode)

Icon order from right to left: Play/Stop, Favorite, Chat, Browser

### Main Toolbar
- **Hide Offline** checkbox (left side)
- **Favorites** checkbox (filter to show only favorites)
- **Filter by name** search entry with wildcard support
- **Platform** dropdown (All, Twitch, YouTube, Kick)
- **Sort** dropdown (Name, Viewers, Playing)
- Toolbar scales with compact modes (smaller margins/spacing)

### Preferences Dialog
- **General**: Refresh interval, notification settings, UI style (Appearance), Window size
- **Streamlink**:
  - Enable/disable toggle
  - Streamlink path (e.g. `streamlink`, `/usr/bin/streamlink`)
  - Streamlink arguments (e.g. `--twitch-low-latency --twitch-disable-ads`)
  - Player path (e.g. `mpv`, `vlc`, `/usr/bin/mpv`)
  - Player arguments (e.g. `--fullscreen --volume=80 --ontop`)
  - Each field has italic hint text showing examples
- **Chat**:
  - Enable/disable toggle
  - Auto-open chat when launching streams
  - Browser selection (System Default, Chrome, Chromium, Edge, Firefox)
  - Chat URL type (Popout, Embedded, Default/Legacy)
- **Accounts**: Twitch login/logout, Import Follows button

## Known Issues & Fixes Applied

| Issue | Solution |
|-------|----------|
| Channel not found for valid Twitch channels | Added GraphQL API (no auth required) |
| OAuth "leaving Twitch" warning | Use Streamlink Twitch GUI's client ID |
| Port 65432 already in use | ReuseAddrHTTPServer with allow_reuse_address |
| RuntimeError: no current event loop | threading.Event instead of asyncio.Event |
| offset-naive/offset-aware datetime mismatch | datetime.now(timezone.utc) |
| Event loop already running | Queue notifications for async processing |
| Sound.Default AttributeError | Sound(name="default") |
| Gtk.events_pending not in GTK4 | Removed (not needed in GTK4) |
| Lambda scope with exception variable | Capture error message before lambda |
| aiohttp session attached to different loop | Set _session = None before new loop |
| Window close doesn't kill process | Added app.quit() to close handler |
| Empty list during startup with hide offline | Show channels immediately, update status in background |
| No feedback when launching stream | Added progress timer and status updates |
| App/MPV opens in background | Window manager policy; can add `--focus-on=open` to player args |
| System tray icon not themed | Use IconPixmap with dynamically generated pixels from KDE panel color |
| Tray menu empty on KDE | DBusMenu requires single struct variant, not double-wrapped |
| Flatpak tray not working | Need `--talk-name=org.kde.StatusNotifierWatcher` permission |
| Launch on login not working in Flatpak | Detect Flatpak via `/.flatpak-info` and use `flatpak run` command |

## Development

### Prerequisites

- Python 3.11+
- GTK4, Libadwaita
- gobject-introspection
- yt-dlp (bundled, for YouTube stream detection)
- streamlink (optional, for stream playback)
- mpv (optional, default player)

### Install

```bash
cd ~/livestream.list.linux
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Run

```bash
livestream-list
# or
.venv/bin/livestream-list
```

### Relaunch (during development)

When relaunching the app after code changes, always kill existing processes first:

```bash
pkill -9 -f livestream-list 2>/dev/null; sleep 0.5
.venv/bin/livestream-list 2>&1 &
sleep 5
pgrep -f livestream-list
```

**Important**: Always use this exact launch method to ensure the window appears properly.

### Project Info

- **Repo**: https://github.com/mkeguy106/livestream-list-linux
- **Flatpak ID**: `life.covert.livestreamList`
- **Command**: `livestream-list`

## Versioning & Releases

### Semantic Versioning (SemVer)

Format: `MAJOR.MINOR.PATCH` (e.g., `0.1.0`)

- **MAJOR**: Breaking changes (API, config format, etc.)
- **MINOR**: New features (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)

Pre-1.0 versions (0.x.y) indicate early development.

### Single Source of Truth

Version is defined in `src/livestream_list/__version__.py`:
```python
__version__ = "0.1.0"
```

- `pyproject.toml` reads version dynamically via hatch
- About dialog imports and displays `__version__`

### GitHub Actions Workflows

| Trigger | Workflow | Output |
|---------|----------|--------|
| PR to main | `flatpak.yml` | Test build artifact (7 days) |
| Tag `v*` | `release.yml` | GitHub Release with `livestreamList.flatpak` |

### Creating a Release

```bash
# 1. Update version in __version__.py
vim src/livestream_list/__version__.py

# 2. Update CHANGELOG.md with changes

# 3. Commit
git add -A && git commit -m "Release v0.2.0"

# 4. Tag and push
git tag v0.2.0
git push && git push --tags
```

The release workflow automatically:
1. Builds the Flatpak bundle
2. Creates a GitHub Release
3. Attaches `livestreamList.flatpak` to the release
4. Generates release notes from commits

### Flatpak

- **Manifest**: `life.covert.livestreamList.yml`
- **Runtime**: GNOME 48 (GTK4/Libadwaita)
- **Bundle**: `livestreamList.flatpak`

Install locally:
```bash
flatpak install livestreamList.flatpak
flatpak run life.covert.livestreamList
```

## Git Commits

Never include in commit messages:
- "Generated with Claude Code"
- "Co-Authored-By: Claude"
- Any reference to AI, Claude, or automated generation
