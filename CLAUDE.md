# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Qt port of [livestream.list.linux](https://github.com/mkeguy106/livestream-list-linux) - a Python application for monitoring Twitch, YouTube, Kick, and Chaturbate livestreams. This fork uses PySide6 (Qt6) instead of GTK4/Libadwaita.

**Current state**: PySide6/Qt6 migration complete. Feature parity work in progress.

**Requirements**: Python 3.10+, PySide6, aiohttp, yt-dlp

## Development Commands

### Linux

```bash
# Install (editable mode)
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Install with dev dependencies (ruff, mypy, pytest)
pip install -e ".[dev]"

# Run
livestream-list-qt
# or
.venv/bin/livestream-list-qt

# Relaunch during development (kill existing, launch fresh)
pkill -9 -f livestream-list-qt 2>/dev/null; sleep 0.5
.venv/bin/livestream-list-qt 2>&1 &
```

### Windows

```bash
# Create venv (use full path to Python if not in PATH)
/c/Users/admin/AppData/Local/Programs/Python/Python312/python.exe -m venv .venv

# Install dependencies (hunspell won't build on Windows — skip it, pyspellchecker is the fallback)
.venv/Scripts/python.exe -m pip install aiohttp PySide6 pydantic pydantic-settings desktop-notifier keyring appdirs yt-dlp pytchat pyspellchecker

# Install app in editable mode (skip deps since we installed them manually)
.venv/Scripts/python.exe -m pip install -e . --no-deps

# Run
.venv/Scripts/livestream-list-qt.exe

# Relaunch during development (kill existing, launch fresh)
taskkill //F //IM livestream-list-qt.exe 2>/dev/null; sleep 0.5
.venv/Scripts/livestream-list-qt.exe 2>&1 &
```

### Common (both platforms)

```bash
# Lint
ruff check src/

# Lint with auto-fix
ruff check src/ --fix

# Format code
ruff format src/

# Type check
mypy src/

# Run tests
pytest tests/

# Run single test
pytest tests/test_file.py::test_name -v
```

### Ruff Configuration

- Line length: 100
- Target: Python 3.10
- Selected rules: `E`, `F`, `I`, `N`, `W`, `UP`
- pytest uses `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- Build system: hatchling (version sourced from `__version__.py`)

### Mypy Configuration

- **Strict mode is enabled and CI blocks on errors** — all source files must pass `mypy src/` with zero errors
- mypy must be run from the venv (`pip install -e ".[dev]"`) to resolve PySide6/aiohttp/keyring stubs. System mypy will show false `import-not-found` errors.
- Use `sys.platform == "win32"` (not `IS_WINDOWS`) for Windows-only code guards — mypy narrows `winreg`/`winsound` types with `sys.platform` but doesn't understand custom constants
- Chat connection `disconnect()` methods conflict with `QObject.disconnect()` — use `# type: ignore[override]`

## Architecture

### Module Structure

```
src/livestream_list/
├── api/                  # Platform API clients (Twitch Helix+GraphQL, YouTube/yt-dlp, Kick REST, Chaturbate REST)
├── chat/
│   ├── connections/      # Per-platform WebSocket/IRC connections (BaseChatConnection subclasses)
│   ├── emotes/           # Emote fetching, caching (two-tier LRU+disk), rendering, matching
│   ├── auth/             # Kick OAuth 2.1+PKCE, YouTube auth helpers
│   ├── spellcheck/       # Spellcheck integration + autocorrect
│   ├── manager.py        # ChatManager - orchestrates connections, emotes, EventSub, routing
│   ├── models.py         # ChatMessage, ChatUser, ChatEmote, ChatBadge dataclasses
│   └── chat_log_store.py # JSONL/text per-channel logging with disk rotation
├── core/                 # Data models, settings, theme definitions, monitor, streamlink, platform detection
├── gui/
│   ├── chat/             # Chat UI widgets (ChatWidget, ChatWindow, YouTubeWebChatWidget, ChaturbateWebChatWidget, delegate, emote picker, etc.)
│   ├── dialogs/          # Preferences, theme editor, add channel, import/export dialogs
│   ├── stream_list/      # Stream list model and custom delegate
│   ├── app.py            # Main QApplication, AsyncWorker
│   ├── main_window.py    # QMainWindow, toolbar, stream list container
│   └── theme.py          # ThemeManager singleton, stylesheet generation
└── notifications/        # Desktop notification integration
```

### Crash Diagnostics

`faulthandler` is enabled in `main.py` at startup (guarded by `sys.stderr is not None` for PyInstaller windowed builds). On SIGSEGV/SIGFPE/SIGABRT, Python prints a traceback to stderr instead of silently crashing. Zero-cost unless a crash occurs.

### Threading Model (Critical)

Qt requires UI updates on the main thread. `AsyncWorker` (in `gui/app.py`) is a QThread subclass that runs async coroutines in a background thread, creating its own event loop. It emits `finished(object)`, `error(str)`, and `progress(str, str)` signals.

**Key rules:**
- Always pass `parent=self` when creating AsyncWorker to prevent QThread garbage collection crashes
- Use Qt Signals for cross-thread communication (thread-safe)
- Call `monitor.reset_all_sessions()` before/after async operations in threads (aiohttp sessions are tied to event loops)
- Never call `QTimer.singleShot` from a background thread — use a Qt Signal instead

### Data Flow

1. `Application` (app.py) initializes `StreamMonitor` and `Settings`
2. `StreamMonitor` owns API clients and channel/livestream state
3. `AsyncWorker` runs async operations in background threads, emits Qt Signals on completion
4. `MainWindow` receives signals and updates UI on the main thread
5. Channels persist to `channels.json`, settings to `settings.json`

### API Clients

- **Twitch**: Helix API (authenticated) + GraphQL (unauthenticated, for public data). GraphQL uses batched queries (up to 35 channels/request). GraphQL works without authentication.
- **YouTube**: yt-dlp subprocess (`yt-dlp --dump-json --no-download <url>`), batch size 5.
- **Kick**: Direct REST API. Uses `start_time` field (not `created_at`) for stream duration.
- **Chaturbate**: REST API (`/api/chatvideocontext/{username}/` for individual, `/api/ts/roomlist/room-list/?follow=true` for bulk). Bulk API requires session cookies (from QWebEngine login). Individual endpoint is public/unauthenticated. WebSocket chat connection for native chat. `room_status` field from individual API detects private/hidden/group shows — bulk API only returns public rooms, so live channels are verified via individual API concurrently during refresh.

### Flatpak Support

The app runs both natively and in Flatpak. Key patterns:
- `IS_FLATPAK` constant in `core/platform.py` checks for `/.flatpak-info` or `FLATPAK_ID` env var
- `flatpak-spawn --host` wraps commands to run on host (browser launch, streamlink)
- Flatpak builds are the primary distribution method via GitHub releases

### Built-in Chat System

The app has a built-in chat client (alternative to opening browser popout chat).

**Architecture**:
- `ChatManager` (QObject) orchestrates connections, emote loading, and message routing via Qt Signals
- Each platform has a `BaseChatConnection` subclass running in a `ChatConnectionWorker` (QThread with its own event loop)
- `ChatWindow` (QMainWindow) holds a `QTabWidget` of `ChatWidget` instances (one per channel)
- `ChatMessageDelegate` (QStyledItemDelegate) renders messages with badges, emotes, colors

**Twitch chat**: IRC over WebSocket (`wss://irc-ws.chat.twitch.tv`). Uses OAuth implicit flow for auth. Handles PRIVMSG, USERNOTICE (subs/raids), and CLEARCHAT/CLEARMSG moderation.

**Kick chat**: Pusher WebSocket for reading (`wss://ws-us2.pusher.com`). Uses OAuth 2.1 + PKCE for auth. Sends messages via official API (`POST https://api.kick.com/public/v1/chat`). Kick echoes your own messages back via websocket (no local echo needed, unlike Twitch).

**Kick OAuth**: OAuth 2.1 + PKCE flow in `chat/auth/kick_auth.py`. Auto-refreshes expired tokens on 401.

**YouTube chat**: Embedded QWebEngineView (`gui/chat/youtube_web_chat.py`) loads YouTube's native popout chat URL. Uses a shared persistent `QWebEngineProfile` ("youtube_chat") that stores cookies to disk automatically. Users sign into Google directly inside the app via `_YouTubeLoginWindow` (persistent top-level QWidget, never destroyed — see Wayland pitfall below). Reading, sending, and rendering are all handled by YouTube's web UI. Title and socials banners are added above the web view, matching the native ChatWidget's banners.

**Chaturbate chat**: Embedded QWebEngineView (`gui/chat/chaturbate_web_chat.py`) loads the Chaturbate room page and injects CSS/JS to isolate just the chat panel, hiding the video player and other content. Uses a shared persistent `QWebEngineProfile` ("chaturbate_chat") with cookie tracking for session persistence. Native WebSocket chat connection (`chat/connections/chaturbate.py`) is also available. Users sign in via `_ChaturbateLoginWindow` in the Accounts tab. DOM isolation uses `data-llqt-*` attributes and a `MutationObserver` for dynamically added elements. Chaturbate's global keyboard event interceptors are neutralized via capture-phase `stopImmediatePropagation()` on both `document` and `window` to allow typing in the `contenteditable` chat input. All 4 native tabs (CHAT, PM, USERS, SETTINGS) are enabled with a tab-switching helper that ensures only one content panel is visible at a time. A `QStackedWidget` hides the web view behind a loading label until isolation JS completes, preventing the full-page flash.

**Emotes**: Supports Twitch, 7TV, BTTV, FFZ. Kick emotes are parsed inline from `[emote:ID:name]` tokens in chat messages (not fetched via a provider API). Loaded async per-channel. Rendered inline via `EmoteCache` (shared pixmap dict). Tab-completion via `EmoteCompleter`.

**Emote Caching**: Two-tier (memory LRU 2000 entries + disk 500MB max). User emotes use stale-while-revalidate pattern. Manual refresh via Ctrl+Shift+E.

**Whisper/DM system**: EventSub WebSocket (`WhisperEventSubWorker` in manager.py) for receiving whispers, Helix API for sending. Persisted via `WhisperStore`.

**Reply threading**: Twitch uses `@reply-parent-msg-id` IRC tag, Kick uses `reply_to_original_message_id` API field. Reply context text word-wraps (no truncation) with height calculated via `QFontMetrics.boundingRect(TextWordWrap)`. Clicking the reply context opens a `ConversationDialog` showing the full @mention conversation between the two users (not just the narrow reply chain).

**Per-channel badge caching**: `_badge_url_map` is `dict[str, dict[str, tuple[str, str]]]` (channel_key → badge_id → (url, title)). Badge images are cached per-channel to avoid showing wrong channel's sub badges.

**Banners**: `DismissibleBanner` in `chat_widget.py` uses overlay X button pattern (button as child widget, NOT in layout, repositioned in `resizeEvent`). Used for title banner, socials banner. Hype Chat banner is a simpler inline widget. Title banner shows clickable category/game link (Twitch/Kick) via `category_url` property — link is placed outside the opacity `<span>` because Qt's `linkActivated` doesn't fire for `<a>` tags inside styled spans. `ClickableTitleLabel` uses `QTextDocument.documentLayout().anchorAt()` for reliable hit-testing.

**Socials Banner**: Fetched via `SocialsFetchWorker`. Twitch uses GraphQL, YouTube scrapes `/about` page (note: `UC...` IDs need `/channel/UC.../about` format), Kick uses REST API.

**Spellcheck & Autocorrect**: `SpellChecker` in `chat/spellcheck/checker.py` wraps hunspell (Linux, fast C extension) or pyspellchecker (cross-platform, pure Python fallback) with chat-aware skip rules (emotes, URLs, mentions, all-caps). Red wavy underlines drawn in `ChatInput.paintEvent()`. `SpellCompleter` shows click-to-correct popup. Autocorrect (`get_confident_correction`) auto-replaces obvious typos when the user moves past a misspelled word (space + next letter). Confidence rule: correct if apostrophe expansion matches (dont→don't), only 1 suggestion, or top suggestion is within Damerau-Levenshtein distance 1. Bundled adult word list (`data/adult.txt`) prevents profanity from being flagged. Custom dictionary additions sync to backend runtime via callback. Corrected words show a green underline for 3 seconds. Both features togglable in Preferences > Chat and the gear menu.

**Chat Logging**: `ChatLogWriter` in `chat/chat_log_store.py` writes buffered JSONL + plain text per-channel logs. Date-based files with configurable disk limit and LRU deletion. History loads from JSONL on channel open.

### Theme System

Two-file architecture:
- `core/theme_data.py` — `ThemeData` dataclass, 32 color fields across 8 categories, 6 built-in theme definitions, theme file I/O (load/save/import/export). Custom themes stored in `~/.config/livestream-list-qt/themes/*.json`.
- `gui/theme.py` — `ThemeManager` singleton, runtime theme state, stylesheet generation with caching, dark/light mode detection via QPalette. Chat color overrides via `ChatColorSettings`.

Theme editor dialog: `gui/dialogs/theme_editor.py`.

### Key Files

Core architecture files (most other files follow patterns established in these):

| File | Purpose |
|------|---------|
| `gui/app.py` | Main QApplication, AsyncWorker, signal-based threading |
| `gui/main_window.py` | QMainWindow, toolbar, stream list container |
| `gui/theme.py` | ThemeManager singleton, stylesheet generation |
| `gui/dialogs/preferences/` | Preferences package — dialog coordinator + per-tab modules (general, playback, chat, accounts) |
| `gui/chat/chat_widget.py` | Single-channel chat widget (message list, input, banners) |
| `gui/chat/youtube_web_chat.py` | YouTube embedded QWebEngineView chat, shared profile, cookie tracker |
| `gui/chat/chaturbate_web_chat.py` | Chaturbate embedded QWebEngineView chat, DOM isolation, shared profile, cookie tracker |
| `api/chaturbate.py` | Chaturbate API client (bulk + individual endpoints) |
| `gui/chat/chat_window.py` | Chat QMainWindow, tab management, pop-out windows |
| `gui/chat/message_delegate.py` | Custom delegate for rendering chat messages (paint + hit-testing) |
| `gui/chat/emote_picker.py` | Searchable emote grid popup with animation/viewport culling |
| `chat/manager.py` | ChatManager - connection lifecycle, emote loading, EventSub, routing |
| `chat/models.py` | ChatMessage, ChatUser, ChatEmote, ChatBadge dataclasses |
| `chat/emotes/cache.py` | Two-tier emote cache (memory LRU 2000 + disk 500MB) |
| `core/monitor.py` | StreamMonitor - channel tracking, refresh logic |
| `core/models.py` | Channel, Livestream, StreamPlatform data classes |
| `core/settings.py` | Settings persistence (JSON), all app preferences |
| `core/theme_data.py` | Theme definitions, built-in themes, theme file I/O |
| `api/twitch.py` | Twitch Helix + GraphQL client |
| `core/platform.py` | Platform detection (`IS_WINDOWS`, `IS_LINUX`, `IS_FLATPAK`, `host_command()`) |
| `core/credential_store.py` | Keyring-based secret storage (tokens, cookies) |
| `core/streamlink.py` | StreamlinkLauncher, subprocess management, Turbo auth, recording |
| `gui/streamlink_console.py` | Console window for streamlink/yt-dlp output, auto-close on exit |

All paths relative to `src/livestream_list/`.

### Versioning

Version is defined in `src/livestream_list/__version__.py`. Update `__version__ = "x.y.z"` before release.

### Configuration Paths

**Linux:**
- Settings: `~/.config/livestream-list-qt/settings.json`
- Channels: `~/.config/livestream-list-qt/channels.json`
- Data dir: `~/.local/share/livestream-list-qt/`

**Windows:**
- Settings: `%APPDATA%\livestream-list-qt\livestream-list-qt\settings.json`
- Channels: `%APPDATA%\livestream-list-qt\livestream-list-qt\channels.json`
- Data dir: `%LOCALAPPDATA%\livestream-list-qt\livestream-list-qt\`

### Key Data Structures

- **Channel**: `channel_id`, `platform` (enum), `display_name`, `favorite`, `dont_notify`, `added_at`, `imported_by`
- **Livestream**: Wraps Channel with live status, `viewers`, `title`, `game`, `game_slug` (URL slug for category links), `start_time`, `last_live_time`, `video_id` (YouTube), `chatroom_id` (Kick), `thumbnail_url`, `room_status` (Chaturbate: public/private/hidden/offline)
- **unique_key**: `"{platform}:{channel_id}"` - used as dict key throughout

### Windows Support

Platform detection is centralized in `core/platform.py` (`IS_WINDOWS`, `IS_LINUX`, `IS_FLATPAK`, `host_command()`). Files with platform conditionals:

| File | Windows behaviour |
|------|-------------------|
| `core/autostart.py` | Registry key (`HKCU\...\Run`) instead of `.desktop` file |
| `core/chat.py` | Windows browser executable names (`chrome`, `msedge`) |
| `core/streamlink.py` | `CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW` instead of `start_new_session` |
| `core/credential_store.py` | Skips `os.chmod(0o600)` on Windows |
| `chat/spellcheck/checker.py` | Bundled hunspell dictionaries via `sys._MEIPASS` or exe-relative path |
| `notifications/notifier.py` | `winsound` for sound playback instead of `paplay` |
| `gui/youtube_login.py` | Windows browser cookie paths (`%LOCALAPPDATA%`, `%APPDATA%`) |
| `gui/dialogs/preferences/general_tab.py` | Hides "notify-send" notification backend option |

**Distribution**: PyInstaller `--onedir` build, wrapped by Inno Setup `.exe` installer. Bundles `yt-dlp.exe`. Users install streamlink and mpv separately.

## Known Pitfalls

| Issue | Solution |
|-------|----------|
| QThread destroyed while running | Always pass `parent=self` to AsyncWorker |
| aiohttp session attached to different loop | Call `monitor.reset_all_sessions()` before creating new event loop in thread |
| Notification Watch button does nothing | Use Qt Signal to marshal callback to main thread |
| Port 65432 already in use (OAuth) | `ReuseAddrHTTPServer` with `allow_reuse_address = True` |
| offset-naive/offset-aware datetime mismatch | Use `datetime.now(timezone.utc)` |
| Kick wrong duration | Use `start_time` field, add UTC timezone to parsed datetime |
| Kick chat send 401 | Token expired; auto-refresh handles it. If persists, re-login (check `chat:write` scope in Dev Portal) |
| auth_state_changed affects wrong platform | Handler must check each widget's platform, not apply blindly |
| Kick shows duplicate messages on send | Don't use local echo for Kick (it echoes via websocket unlike Twitch) |
| `livestream.platform` AttributeError | Use `livestream.channel.platform` (Livestream wraps Channel) |
| YouTube socials 404 | UC channel IDs need `/channel/UC.../about` URL format, not `/@UC.../about` |
| QWebEngineView destroys Wayland focus | Never destroy a QWebEngineView that had user interaction on Wayland — hide it and navigate to about:blank instead. The YouTube login window (`_YouTubeLoginWindow`) is a persistent singleton for this reason (QTBUG-73321). |
| QWebEngineView in modal dialog can't receive input | Wayland modality stack: if Preferences is ApplicationModal, a child window must also be ApplicationModal to receive input. Set `setWindowModality(Qt.ApplicationModal)` on the login window. |
| QWebEngineView stacks behind parent on Wayland | Must set transient parent via `windowHandle().setTransientParent()` BEFORE calling `show()`. Call `winId()` first to force native handle creation. |
| QWebEngineView Ctrl+scroll zoom broken | Chromium's built-in Ctrl+scroll zoom doesn't work for zoom-out. Install an event filter on `focusProxy()` (after `loadFinished`) and call `page().setZoomFactor()` manually. |
| QWebEngineView triggers KDE close animation on Wayland | Adding the first QWebEngineView to a visible window causes Qt to recreate the native surface (RasterSurface→OpenGLSurface), triggering KDE's close animation. Fix: in `_ensure_chat_window()` (app.py), add a temporary QWebEngineView and do a show()/processEvents()/hide() cycle before the window is ever visible. `setWindowOpacity(0)` does NOT work (Wayland QPA doesn't support it). KWin's `skipCloseAnimation` doesn't work for surface recreation. |
| Badge images showing wrong channel's sub badges | Per-channel `_badge_url_map` with channel-scoped cache keys |
| Chat scrolls even when user scrolled up | Defer buffer trimming with `_trim_paused` flag, flush on scroll-to-bottom |
| Twitch Turbo token "invalid" | Must use browser `auth-token` cookie (not OAuth access token) with `Authorization=OAuth` prefix. Token is client-ID-bound. |
| Streamlink args dropping values like `debug` | `_validate_additional_args` must allow non-flag values after flags (e.g., `--loglevel debug`) |
| Streamlink `--record-and-play` doesn't exist | Use `--record PATH` (plays AND records when a player is configured). `--record-and-play` is not a valid flag; `--record-and-pipe` (`-R`) exists but is deprecated. |
| Twitch shows own messages twice | `TwitchChatConnection` must skip messages from `self._nick` since `ChatManager.send_message()` creates a local echo (Twitch IRC doesn't echo back, but if it does, skip it) |
| `start_new_session=True` on Windows | Use `creationflags=CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW` instead (see `core/streamlink.py`) |
| `os.chmod(0o600)` on Windows | No-op on Windows — skip with `IS_WINDOWS` guard (see `core/credential_store.py`) |
| `notify-send` on Windows | Doesn't exist — `desktop-notifier` handles Windows toast notifications. Hidden from Preferences backend list. |
| `hunspell` package on Windows | C extension can't compile — `hunspell` is Linux-only in pyproject.toml. `pyspellchecker` is the cross-platform fallback. |
| `pyspellchecker` `candidates()` freezes UI | `candidates()` with `distance=2` takes 600-1000ms for longer words. Always init with `distance=1` — autocorrect only trusts distance-1 anyway. |
| PyInstaller bundled data files | Use `sys._MEIPASS` for base path in frozen builds vs `__file__` in dev (see `chat/spellcheck/checker.py`) |
| `sys.stderr`/`sys.stdout` is `None` in windowed builds | Guard `faulthandler.enable()`, `StreamHandler`, `traceback.print_exc()` — use `logging.NullHandler()` fallback and `logger.error(..., exc_info=True)` instead |
| Chaturbate cookie rotation loses sessionid | QWebEngine fires `cookieRemoved` then `cookieAdded` when rotating cookies. `_on_cookie_removed` must NOT remove from `_tracked_cookies` — cookies are only cleared on explicit logout via `clear_chaturbate_cookies()`. |
| Chaturbate global keyboard interception | Chaturbate's JS intercepts keydown/keypress on document and window, blocking typing in the contenteditable chat input. Must add capture-phase `stopImmediatePropagation()` on both `document` AND `window` for `keydown`, `keypress`, `keyup`, `beforeinput`, `textInput`. Enter key must be let through for message sending. |
| Chaturbate settings tab blank | Previously hidden. Now enabled — the tab-switching helper in `_ISOLATE_CHAT_JS` manages visibility of all 4 tab content panels including settings. |
| New preferences not in export/import | When adding/removing settings (except cookies/tokens), update both `gui/dialogs/export.py` and `gui/main_window.py:_import_from_file()` to include the new fields |
| Qt `linkActivated` not firing for `<a>` after `<br>` | `ClickableTitleLabel` uses `QTextDocument.documentLayout().anchorAt()` in `mouseReleaseEvent` as fallback — Qt's rich text engine doesn't reliably emit `linkActivated` for links after line breaks or inside styled `<span>` tags |
| `webbrowser.open()` blocks main thread | Python's `webbrowser` module calls `subprocess.wait(5)` internally, blocking for ~4.5s. Run on a daemon thread: `threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()` |
| Unknown platform in channels.json | `_load_channels` in `monitor.py` skips channels with unknown `StreamPlatform` values (e.g., from experimental branches) with a warning instead of crashing |
| Chaturbate private room shows as live | Bulk API returns private rooms as "online". Individual API `room_status` field detects private/hidden/group shows. Live Chaturbate channels are verified via concurrent individual API checks during refresh. Stream delegate shows dimmed color + tooltip for non-public rooms. |

## CI/CD

The release workflow (`.github/workflows/release.yml`) runs 5 jobs on tag push (`v*`):

1. **create-release** — GitHub-hosted `ubuntu-latest`, creates draft release
2. **build-flatpak** — Self-hosted Linux runner (`docker01.dd.local`), builds Flatpak in Docker
3. **build-windows** — GitHub-hosted `windows-latest`, builds PyInstaller exe + Inno Setup installer
4. **update-screenshots** — Calls the reusable `screenshots.yml` workflow (see below)
5. **publish-release** — Undrafts the release after both builds succeed

**Flatpak runner location**: `docker01.dd.local:/share/bsv/docker-compose/github-runner/`

**Windows build files**: `livestream-list-qt.spec` (PyInstaller), `installer/livestream-list-qt.iss` (Inno Setup)

### Automated Screenshots (`.github/workflows/screenshots.yml`)

A separate reusable workflow that auto-generates README screenshots. Runs on `ubuntu-latest` with `QT_QPA_PLATFORM=offscreen`. Triggered two ways:
- **Automatically** on every release (called from `release.yml`)
- **Manually** from the GitHub Actions tab (`workflow_dispatch`)

```bash
# Trigger manually
gh workflow run screenshots.yml

# Watch the run
gh run watch <run-id> --exit-status
```

**What it captures** (`scripts/capture_screenshots.py`):

| Screenshot | Description |
|------------|-------------|
| `main-window-dark.png` | Main window, dark theme, 540x700 |
| `main-window-light.png` | Main window, light theme |
| `compact-mode.png` | Compact UIStyle.COMPACT_2, 360x700 |
| `chat-window.png` | Chat with real emotes (static), 450x600 |
| `chat-animated.gif` | Chat with animated emotes cycling |
| `mpv-playback.gif` | Live stream capture via streamlink+ffmpeg |
| `preferences-*.png` | General, Chat, Appearance tabs |

**How it works**:
1. Creates a `MockApplication` (QObject with Qt Signals) that proxies `QApplication` methods — avoids full `Application.initialize()` (no network, no timers, no ChatManager)
2. Injects sample `Channel`/`Livestream` objects directly into `StreamMonitor._channels`/`_livestreams`
3. Creates `MainWindow` with `_initial_check_complete = True` and calls `refresh_stream_list()`
4. Downloads real emotes from 7TV/Twitch CDN, extracts animated frames via `QImageReader`, populates `EmoteCache._memory`/`_animated`/`_frame_delays`
5. Creates `ChatWidget` with `set_connected()`, injects `ChatMessage` objects with `emote_positions` pointing to pre-loaded `ImageSet`/`ImageRef` objects
6. Captures animated GIF by advancing `delegate.set_animation_frame(elapsed_ms)` and grabbing frames via `QWidget.grab()` → PIL
7. mpv capture: finds a live streamer via Twitch GraphQL, runs `streamlink | ffmpeg` to extract frames, composites mpv OSD overlay via PIL

**Testing locally**:
```bash
# With real display (dev machine) — includes mpv live capture
python scripts/capture_screenshots.py

# Simulating CI (offscreen platform) — mpv capture skipped if no streamlink/ffmpeg
QT_QPA_PLATFORM=offscreen python scripts/capture_screenshots.py
```

**Dependencies**: `pip install -e .` + `Pillow` (for GIF creation). CI installs Pillow separately. Local dev needs `streamlink` and `ffmpeg` for the mpv capture.

### Screenshot Pitfalls

| Issue | Solution |
|-------|----------|
| `MockApplication` missing attribute | `MainWindow` uses `self.app` as both `Application` and `QApplication` — proxy any missing QApplication methods (e.g., `styleSheet()`, `setStyleSheet()`) to `QApplication.instance()`. Add missing attributes like `tray_icon = None`. |
| Theme doesn't switch (dark stays dark) | Clear `_stylesheet_cache` before switching. Also clear the app stylesheet (`qt_app.setStyleSheet("")`) before calling `window._apply_theme()` — it skips if the stylesheet matches. |
| Chat shows "Loading channels..." | The `QStackedWidget` defaults to index 0 (loading). Set `window._initial_check_complete = True` and call `refresh_stream_list()` to switch to the stream list. |
| Chat shows "Connecting to chat..." | `ChatWidget` hides `_list_view` and shows `_connecting_label` by default. Call `chat_widget.set_connected()` to swap them. |
| Emotes render as text instead of images | Need `ImageSet` → `ImageRef` → `EmoteCache` pipeline. Put pixmaps in `cache._memory[key]`, create `ImageRef(store=cache)` bound to that key, wrap in `ImageSet`, assign to `ChatEmote.image_set`. Set `delegate.set_image_store(cache)`. |
| Animated emotes don't cycle in GIF | Must call `delegate.set_animation_frame(elapsed_ms)` with advancing values and `viewport().update()` + `processEvents()` between frame captures. Frames stored in `cache._animated[key]` with delays in `cache._frame_delays[key]`. |
| `libgl1-mesa-glx` not available on Ubuntu 24.04 | Replaced by `libgl1`. |
| Qt xcb plugin fails in CI | Use `QT_QPA_PLATFORM=offscreen` — no X11/Xvfb needed. |
| mpv capture fails in CI | Requires `streamlink` + `ffmpeg` + network access + live stream. Script skips gracefully if unavailable. |
| Emote CDN URLs change | 7TV emote IDs are stable. If URLs break, use the 7TV GraphQL API to search: `query SearchEmotes($query: String!) { emotes(query: $query, limit: 1) { items { id host { url files { name format } } } } }` |

### Troubleshooting Release Builds

If a release stays "queued" for a long time:

```bash
# Check runner status
ssh docker01.dd.local "cd /share/bsv/docker-compose/github-runner && docker compose ps"

# Check runner logs (look for "deprecated" or errors)
ssh docker01.dd.local "cd /share/bsv/docker-compose/github-runner && docker compose logs --tail 50 github-runner-qt"

# Restart runners
ssh docker01.dd.local "cd /share/bsv/docker-compose/github-runner && docker compose down && docker compose up -d"

# Cleanup docker to free space (run periodically)
ssh docker01.dd.local "docker system prune -af --volumes"
```

**Common issues:**
- Runner version deprecated → pull latest image and restart
- Runner in restart loop → check logs for auth/token issues
- Job canceled mid-run → re-run workflow with `gh run rerun <run-id>`

## Before Creating a Release

**ALWAYS test the changes locally before pushing a release.** Run the app and verify the fix/feature works:

```bash
# Kill existing and launch fresh from dev environment
pkill -9 -f livestream-list-qt 2>/dev/null; sleep 0.5
.venv/bin/livestream-list-qt 2>&1 &
```

Test the specific functionality that was changed. Only after confirming it works should you create the release.

## Release Hygiene

After pushing a new release, perform these cleanup checks:

1. **Prune old releases**: Keep only the latest release per minor version series. Delete older patch releases (e.g., if v0.9.1 exists, delete v0.9.0). Each flatpak is ~170MB so old releases add up fast.
   ```bash
   gh release list
   gh release delete <tag> --yes --cleanup-tag
   ```

2. **Delete merged branches**: Remove remote branches that have been merged.
   ```bash
   git branch -r --merged origin/main | grep -v main | sed 's|origin/||' | xargs -r git push origin --delete
   ```

3. **Check total release storage**: Should stay under ~1GB (keep 4-5 releases max).
   ```bash
   gh api repos/mkeguy106/livestream.list.qt/releases --paginate --jq '[.[] | .assets[0].size // 0] | add / 1048576 | floor'
   # Shows total MB across all release assets
   ```

4. **Verify repo size**: Should remain well under 1GB.
   ```bash
   gh api repos/mkeguy106/livestream.list.qt --jq '.size'  # KB
   ```

5. **Sync documentation**: Review all changes since the last release and ensure CLAUDE.md, README.md, and the wiki are up to date. Use the decision matrix in the [Documentation Maintenance](#documentation-maintenance) section to determine what needs updating. At minimum:
   - Compare `git log <previous-tag>..HEAD` to identify all changes included in the release
   - Update CLAUDE.md for any new pitfalls, architecture changes, or API changes
   - Update README.md feature list, keyboard shortcuts, and roadmap checkboxes
   - Clone/pull the wiki and update relevant pages (Features, Chat, Preferences, Troubleshooting, etc.)
   - Commit and push wiki changes

## Git Workflow

**Branch protection is enabled on `main`.** Direct commits to `main` are not allowed.

### Making Changes

1. **Create a feature branch** from `main` (e.g., `fix/description`, `feature/description`, `refactor/description`)
2. **Commit changes** to the feature branch
3. **Push the branch** and **create a pull request** to merge into `main`
4. PR requires 1 approving review and all conversations resolved before merge
5. Linear history is enforced — use squash or rebase merges, not merge commits

### Commit Messages

Never include in commit messages:
- "Generated with Claude Code"
- "Co-Authored-By: Claude"
- Any reference to AI, Claude, or automated generation

### Releases

Releases are created by pushing a version tag (`v*`) to `main` after a PR is merged. Always test locally before tagging. See [Before Creating a Release](#before-creating-a-release) and [Release Hygiene](#release-hygiene).

## Documentation Maintenance

When making changes to the codebase, **all three documentation targets must be kept in sync**: CLAUDE.md, README.md, and the wiki. Each serves a different audience and purpose — update the relevant ones for every change.

### When to Update

| Change type | CLAUDE.md | README.md | Wiki |
|-------------|-----------|-----------|------|
| New feature added | Architecture/chat/theme sections if non-trivial | Features list, keyboard shortcuts | `Features.md`, `Chat.md`, `Preferences.md`, `Keyboard-Shortcuts.md` |
| Feature removed | Remove from relevant sections | Remove from features list, roadmap | Remove from all relevant pages |
| New setting/preference | Only if architecturally significant | — | `Preferences.md` |
| New keyboard shortcut | — | Keyboard Shortcuts section | `Keyboard-Shortcuts.md` |
| Architecture change | Module structure, threading, data flow sections | — | `Architecture.md`, `Contributing.md` |
| New API endpoint or auth flow | API Clients section | — | `API-Clients.md` |
| Chat system change | Built-in Chat System section | — | `Chat-System.md`, `Chat.md` |
| New platform added | Multiple sections (API, chat, models, etc.) | Features, requirements, adding channels | Multiple wiki pages |
| New pitfall discovered | Known Pitfalls table | — | `Troubleshooting.md` or `Known-Issues.md` |
| New known issue/limitation | Known Pitfalls table (if dev-facing) | — | `Known-Issues.md` |
| CI/CD or build change | CI/CD section | — | `Contributing.md` |
| Streamlink/playback change | — | Streamlink Settings section | `Streamlink.md` |
| New export/import fields | Known Pitfalls (the existing reminder) | — | `Preferences.md` |
| Windows support change | Windows Support table | — | `Installation.md`, `Contributing.md` |
| Flatpak change | Flatpak Support section | Installation section | `Installation.md` |
| Roadmap item completed | — | Move from `[ ]` to `[x]` in Roadmap | `Features.md` if not already listed |
| FAQ-worthy behavior | — | — | `FAQ.md` |

### What Goes Where

- **CLAUDE.md** — Developer-facing: architecture, threading rules, pitfalls, data structures, API details, CI/CD. Things that require reading multiple files to understand. Don't duplicate what's obvious from reading a single file.
- **README.md** — User-facing: feature list, installation, usage, keyboard shortcuts, configuration, roadmap. Keep the feature list comprehensive but concise (one line per feature with sub-bullets for details).
- **Wiki** — Both audiences, expanded: detailed guides, step-by-step instructions, troubleshooting, FAQ. The wiki is the most detailed reference and should be the most thorough.

### How to Update the Wiki

```bash
# Clone the wiki repo (if not already cloned)
git clone https://github.com/mkeguy106/livestream.list.qt.wiki.git /tmp/wiki
cd /tmp/wiki

# Or pull latest if already cloned
cd /tmp/wiki && git pull

# Edit relevant pages, then commit and push
git add -A && git commit -m "Update wiki for <feature>" && git push origin master
```

### Wiki Page Reference

**User Guide:**

| Page | Covers |
|------|--------|
| `Home.md` | Landing page with navigation links |
| `Installation.md` | Flatpak, Windows, and source install instructions |
| `Getting-Started.md` | First launch walkthrough, basic usage |
| `Adding-Channels.md` | URL detection, importing follows, manual entry |
| `Features.md` | Comprehensive feature list |
| `Preferences.md` | All settings tabs: General, Playback, Accounts, Chat, Themes, Notifications |

**User Reference:**

| Page | Covers |
|------|--------|
| `Chat.md` | Built-in chat UI, tabs, pop-out, banners, emotes, badges, sending |
| `Keyboard-Shortcuts.md` | All shortcuts: main window, channel list, chat, selection mode |
| `Streamlink.md` | Player setup, quality, latency, Turbo auth, recording |
| `Troubleshooting.md` | Stream launch, chat connection, player, performance, crash logs |
| `Known-Issues.md` | Current limitations (YouTube, Kick rate limits, etc.) |
| `FAQ.md` | Organized by topic: General, YouTube, Kick, Twitch, Chaturbate, Notifications |

**Developer Documentation:**

| Page | Covers |
|------|--------|
| `Architecture.md` | Threading model, data flow, key patterns |
| `API-Clients.md` | Twitch (Helix + GraphQL), YouTube (yt-dlp), Kick (REST), Chaturbate (bulk + individual) |
| `Chat-System.md` | ChatManager, connections, emotes, message rendering, event routing |
| `Contributing.md` | Dev setup (Linux/Windows), commands, coding patterns, project structure |
