# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Qt port of [livestream.list.linux](https://github.com/mkeguy106/livestream-list-linux) - a Python application for monitoring Twitch, YouTube, and Kick livestreams. This fork uses PySide6 (Qt6) instead of GTK4/Libadwaita.

**Current state**: PySide6/Qt6 migration complete. Feature parity work in progress.

**Requirements**: Python 3.10+, PySide6, aiohttp, yt-dlp

## Development Commands

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

# Lint
ruff check src/

# Lint with auto-fix
ruff check src/ --fix

# Format code
ruff format src/

# Type check
mypy src/

# Run tests (currently empty)
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

## Architecture

### Threading Model (Critical)

Qt requires UI updates on the main thread. Pattern for async operations using AsyncWorker:

```python
class AsyncWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, coro_func, monitor=None, parent=None):
        super().__init__(parent)
        self.coro_func = coro_func
        self.monitor = monitor

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # CRITICAL: Reset aiohttp sessions for this event loop
            if self.monitor:
                for client in self.monitor._clients.values():
                    client._session = None
            result = loop.run_until_complete(self.coro_func())
            self.finished.emit(result)
        finally:
            if self.monitor:
                for client in self.monitor._clients.values():
                    client._session = None
            loop.close()
```

**Key points:**
- Always pass `parent=self` when creating AsyncWorker to prevent QThread garbage collection crashes
- Use Qt Signals for cross-thread communication (thread-safe)
- Always set `client._session = None` before/after async operations in threads

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

### Flatpak Support

The app runs both natively and in Flatpak. Key patterns:
- `is_flatpak()` in `chat.py` checks for `/.flatpak-info` or `FLATPAK_ID` env var
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

**Kick OAuth**: App credentials hardcoded (`DEFAULT_KICK_CLIENT_ID`/`SECRET` in `chat/auth/kick_auth.py`). Requires `chat:write` and `user:read` scopes enabled in Kick Developer Portal. Uses port 65432 for redirect (`http://localhost:65432/redirect`). Auto-refreshes expired tokens on 401.

**YouTube chat**: Uses pytchat library for reading chat messages. Message sending uses InnerTube API with SAPISIDHASH authentication (requires YouTube cookies: SID, HSID, SSID, APISID, SAPISID). Supports SuperChat tier detection and membership badge rendering.

**Emotes**: Supports Twitch, Kick native, 7TV, BTTV, FFZ. Loaded async per-channel. Rendered inline via `EmoteCache` (shared pixmap dict). Tab-completion via `EmoteCompleter`.

**Emote Caching**:
- **Image cache**: Two-tier (memory LRU 2000 entries + disk 500MB max) in `~/.local/share/livestream-list-qt/emote_cache/`
- **User emotes**: Twitch subscriber emotes from other channels fetched via `/chat/emotes/user` (requires `user:read:emotes` scope)
- **Stale-while-revalidate**: Cached user emotes used immediately, fresh emotes fetched in background. If changed, UI updates automatically.
- **Manual refresh**: Chat menu → "Refresh Emotes" (Ctrl+Shift+E) clears cache and re-fetches

**Whisper/DM system**: EventSub WebSocket for receiving whispers, Helix API for sending. Whisper conversations persisted via `WhisperStore` in data dir.

**Reply threading**: Twitch uses `@reply-parent-msg-id` IRC tag, Kick uses `reply_to_original_message_id` API field. `ChatMessage` has `reply_parent_display_name` and `reply_parent_text` fields for rendering reply context.

**Per-channel badge caching**: `_badge_url_map` is `dict[str, dict[str, tuple[str, str]]]` (channel_key → badge_id → (url, title)). Badge images are cached per-channel to avoid showing wrong channel's sub badges.

**Scroll pause**: `_trim_paused` flag on `ChatMessageModel` defers buffer trimming when user has scrolled up. Flush on scroll-to-bottom. Auto-resumes after 5 minutes.

**Recent messages**: robotty.de API loads ~50 recent messages on Twitch channel join, parsed through the same IRC message handler.

**Spellcheck**: hunspell-based via `spellchecker` library, custom dictionary stored in data dir. Correction popup via `SpellCompleter`.

**Channel Socials Banner**: Displays clickable social links (Discord, Twitter, etc.) below the stream title banner. Fetched via `SocialsFetchWorker` (QThread) when chat opens.

- **Twitch**: GraphQL query for `channel.socialMedias` array (returns name + URL directly)
- **YouTube**: Scrapes `/about` page HTML, extracts `ytInitialData` JSON, navigates to `aboutChannelViewModel.links`. URL format varies by channel ID type:
  - `UC...` IDs → `https://www.youtube.com/channel/UC.../about`
  - `@handle` → `https://www.youtube.com/@handle/about`
  - External links use YouTube redirect with `q=` param containing actual URL
  - Internal YouTube links (e.g., second channel) have direct URLs without redirect
- **Kick**: REST API `GET /api/v2/channels/{id}`, extracts social usernames from `user` object, constructs full URLs

### Dismissible Banner Pattern

For any banner with a dismiss/close button, use the **overlay X button** approach:

```python
class DismissibleBanner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel()
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)

        # Close button as overlay (NOT in layout)
        self._close_btn = QPushButton("×", self)
        self._close_btn.setFixedSize(20, 20)
        self._close_btn.clicked.connect(self.hide)
        self._close_btn.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Position in top-right corner
        self._close_btn.move(self.width() - 24, 4)
```

**Key points:**
- Button is a child of the banner but NOT added to the layout
- Use `resizeEvent` to reposition button when banner resizes
- Add right padding to label (`padding: 6px 28px 6px 8px`) to avoid text overlap
- Style button with semi-transparent background: `background-color: rgba(0, 0, 0, 0.3); border-radius: 10px;`
- This avoids layout issues where the button column shows the wrong background color

### Key Files

| File | Purpose |
|------|---------|
| `src/livestream_list/gui/app.py` | Main QApplication, AsyncWorker, signal-based threading |
| `src/livestream_list/gui/main_window.py` | QMainWindow, StreamRow widget, all dialogs |
| `src/livestream_list/gui/tray.py` | QSystemTrayIcon for system tray |
| `src/livestream_list/gui/chat/chat_window.py` | Chat QMainWindow with tabbed channels, popout support |
| `src/livestream_list/gui/chat/chat_widget.py` | Single-channel chat widget (message list + input) |
| `src/livestream_list/gui/chat/message_delegate.py` | Custom delegate for rendering chat messages |
| `src/livestream_list/chat/manager.py` | ChatManager - connection lifecycle, emote loading, message routing |
| `src/livestream_list/chat/connections/twitch.py` | Twitch IRC WebSocket connection |
| `src/livestream_list/chat/connections/kick.py` | Kick Pusher WebSocket + public API message sending |
| `src/livestream_list/chat/connections/youtube.py` | YouTube pytchat + InnerTube connection |
| `src/livestream_list/chat/auth/kick_auth.py` | Kick OAuth 2.1 + PKCE flow |
| `src/livestream_list/chat/models.py` | ChatMessage, ChatUser, ChatEmote, ChatBadge dataclasses |
| `src/livestream_list/chat/emotes/cache.py` | Shared emote/badge pixmap cache |
| `src/livestream_list/api/oauth_server.py` | Local HTTP server for OAuth callbacks (both implicit + code flows) |
| `src/livestream_list/core/monitor.py` | StreamMonitor - channel tracking, refresh logic |
| `src/livestream_list/core/settings.py` | Settings persistence (JSON) |
| `src/livestream_list/api/twitch.py` | Twitch Helix + GraphQL client |
| `src/livestream_list/api/base.py` | Abstract base class for API clients with retry logic |
| `src/livestream_list/notifications/notifier.py` | Desktop notifications with Watch button |
| `src/livestream_list/core/chat.py` | Chat launcher for opening stream chat in browser |
| `src/livestream_list/__version__.py` | Single source of truth for version |
| `src/livestream_list/core/models.py` | Data classes: Channel, Livestream, StreamPlatform |
| `src/livestream_list/core/streamlink.py` | Stream launch via streamlink or yt-dlp |
| `src/livestream_list/chat/whisper_store.py` | Whisper conversation persistence |
| `src/livestream_list/gui/chat/spell_completer.py` | Spellcheck correction popup |
| `src/livestream_list/gui/chat/mention_completer.py` | @mention autocomplete |
| `src/livestream_list/chat/spellcheck/checker.py` | Spellcheck engine |
| `src/livestream_list/gui/chat/message_model.py` | Chat message list model with deferred trim |

### Versioning

Version is defined in `src/livestream_list/__version__.py`. Update `__version__ = "x.y.z"` before release.

### Configuration Paths

- Settings: `~/.config/livestream-list-qt/settings.json`
- Channels: `~/.config/livestream-list-qt/channels.json`
- Data dir: `~/.local/share/livestream-list-qt/`

### Key Data Structures

- **Channel**: `channel_id`, `platform` (enum), `display_name`, `favorite`, `dont_notify`
- **Livestream**: Wraps Channel with live status, `viewers`, `title`, `game`, `start_time`, `last_live_time`
- **unique_key**: `"{platform}:{channel_id}"` - used as dict key throughout

## Known Pitfalls

| Issue | Solution |
|-------|----------|
| QThread destroyed while running | Always pass `parent=self` to AsyncWorker |
| aiohttp session attached to different loop | Set `_session = None` before creating new event loop in thread |
| Notification Watch button does nothing | Use Qt Signal to marshal callback to main thread |
| Port 65432 already in use (OAuth) | `ReuseAddrHTTPServer` with `allow_reuse_address = True` |
| offset-naive/offset-aware datetime mismatch | Use `datetime.now(timezone.utc)` |
| Kick wrong duration | Use `start_time` field, add UTC timezone to parsed datetime |
| OAuth login UI not updating | Use Qt Signal instead of QTimer.singleShot from background thread |
| Kick chat send 401 | Token expired; auto-refresh handles it. If persists, re-login (check `chat:write` scope in Dev Portal) |
| auth_state_changed affects wrong platform | Handler must check each widget's platform, not apply blindly |
| Kick shows duplicate messages on send | Don't use local echo for Kick (it echoes via websocket unlike Twitch) |
| `livestream.platform` AttributeError | Use `livestream.channel.platform` (Livestream wraps Channel) |
| YouTube socials 404 | UC channel IDs need `/channel/UC.../about` URL format, not `/@UC.../about` |
| YouTube chat send requires cookies | Copy cookies from browser (SID, HSID, SSID, APISID, SAPISID) into Preferences > Accounts |
| Badge images showing wrong channel's sub badges | Per-channel `_badge_url_map` with channel-scoped cache keys |
| Chat scrolls even when user scrolled up | Defer buffer trimming with `_trim_paused` flag, flush on scroll-to-bottom |

## CI/CD - Self-Hosted Runner

Releases use a self-hosted GitHub Actions runner on `docker01.dd.local`.

**Runner location**: `docker01.dd.local:/share/bsv/docker-compose/github-runner/`

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

## Git Commits

Never include in commit messages:
- "Generated with Claude Code"
- "Co-Authored-By: Claude"
- Any reference to AI, Claude, or automated generation
