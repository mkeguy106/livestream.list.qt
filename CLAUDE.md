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
    progress = Signal(str, str)  # message, detail

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
                self.monitor.reset_all_sessions()
            result = loop.run_until_complete(self.coro_func())
            self.finished.emit(result)
            # Close sessions before closing loop
            if self.monitor:
                loop.run_until_complete(self.monitor.close_all_sessions())
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if self.monitor:
                self.monitor.reset_all_sessions()
            loop.close()
```

**Key points:**
- Always pass `parent=self` when creating AsyncWorker to prevent QThread garbage collection crashes
- Use Qt Signals for cross-thread communication (thread-safe)
- Call `monitor.reset_all_sessions()` before/after async operations in threads

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

**Emotes**: Supports Twitch, 7TV, BTTV, FFZ. Kick emotes are parsed inline from `[emote:ID:name]` tokens in chat messages (not fetched via a provider API). Loaded async per-channel. Rendered inline via `EmoteCache` (shared pixmap dict). Tab-completion via `EmoteCompleter`.

**Emote Caching**: Two-tier (memory LRU 2000 entries + disk 500MB max). User emotes use stale-while-revalidate pattern. Manual refresh via Ctrl+Shift+E.

**Whisper/DM system**: EventSub WebSocket (`WhisperEventSubWorker` in manager.py) for receiving whispers, Helix API for sending. Persisted via `WhisperStore`.

**Reply threading**: Twitch uses `@reply-parent-msg-id` IRC tag, Kick uses `reply_to_original_message_id` API field.

**Per-channel badge caching**: `_badge_url_map` is `dict[str, dict[str, tuple[str, str]]]` (channel_key → badge_id → (url, title)). Badge images are cached per-channel to avoid showing wrong channel's sub badges.

**Banners**: `DismissibleBanner` in `chat_widget.py` uses overlay X button pattern (button as child widget, NOT in layout, repositioned in `resizeEvent`). Used for title banner, socials banner. Hype Chat banner is a simpler inline widget.

**Socials Banner**: Fetched via `SocialsFetchWorker`. Twitch uses GraphQL, YouTube scrapes `/about` page (note: `UC...` IDs need `/channel/UC.../about` format), Kick uses REST API.

### Key Files

Core architecture files (most other files follow patterns established in these):

| File | Purpose |
|------|---------|
| `src/livestream_list/gui/app.py` | Main QApplication, AsyncWorker, signal-based threading |
| `src/livestream_list/gui/main_window.py` | QMainWindow, StreamRow widget, all dialogs |
| `src/livestream_list/gui/chat/chat_widget.py` | Single-channel chat widget (message list, input, banners) |
| `src/livestream_list/gui/chat/message_delegate.py` | Custom delegate for rendering chat messages (paint + hit-testing) |
| `src/livestream_list/gui/chat/message_model.py` | Chat message list model with deferred trim |
| `src/livestream_list/chat/manager.py` | ChatManager - connection lifecycle, emote loading, EventSub, message routing |
| `src/livestream_list/chat/connections/twitch.py` | Twitch IRC WebSocket connection |
| `src/livestream_list/chat/models.py` | ChatMessage, ChatUser, ChatEmote, ChatBadge dataclasses |
| `src/livestream_list/core/monitor.py` | StreamMonitor - channel tracking, refresh logic |
| `src/livestream_list/core/models.py` | Channel, Livestream, StreamPlatform data classes |
| `src/livestream_list/core/settings.py` | Settings persistence (JSON) |
| `src/livestream_list/api/twitch.py` | Twitch Helix + GraphQL client |

### Versioning

Version is defined in `src/livestream_list/__version__.py`. Update `__version__ = "x.y.z"` before release.

### Configuration Paths

- Settings: `~/.config/livestream-list-qt/settings.json`
- Channels: `~/.config/livestream-list-qt/channels.json`
- Data dir: `~/.local/share/livestream-list-qt/`

### Key Data Structures

- **Channel**: `channel_id`, `platform` (enum), `display_name`, `favorite`, `dont_notify`, `added_at`, `imported_by`
- **Livestream**: Wraps Channel with live status, `viewers`, `title`, `game`, `start_time`, `last_live_time`, `video_id` (YouTube), `chatroom_id` (Kick), `thumbnail_url`
- **unique_key**: `"{platform}:{channel_id}"` - used as dict key throughout

## Known Pitfalls

| Issue | Solution |
|-------|----------|
| QThread destroyed while running | Always pass `parent=self` to AsyncWorker |
| aiohttp session attached to different loop | Call `monitor.reset_all_sessions()` before creating new event loop in thread |
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

## Wiki Maintenance

When adding new features, shortcuts, settings, or making architecture changes, keep the wiki up to date.

### When to Update

- New features or settings added
- Keyboard shortcuts added or changed
- Architecture changes (new files, renamed modules, new patterns)
- API client changes (new endpoints, auth flow changes)
- Known pitfalls discovered

### How to Update

```bash
# Clone the wiki repo
git clone https://github.com/mkeguy106/livestream.list.qt.wiki.git /tmp/wiki
cd /tmp/wiki

# Edit relevant pages, then commit and push
git add -A && git commit -m "Update wiki for <feature>" && git push origin master
```

### Key Wiki Pages

| Page | Covers |
|------|--------|
| `Features.md` | Feature list and descriptions |
| `Preferences.md` | Settings tables and configuration |
| `Keyboard-Shortcuts.md` | All keyboard shortcuts |
| `Chat-System.md` | Chat architecture and data models |
| `API-Clients.md` | Platform API endpoints and patterns |
| `Contributing.md` | Project structure, pitfalls, dev guide |
| `FAQ.md` | Common user questions |
| `Streamlink.md` | Playback configuration |
