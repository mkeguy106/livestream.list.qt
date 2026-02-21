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

## Architecture

### Module Structure

```
src/livestream_list/
├── api/                  # Platform API clients (Twitch Helix+GraphQL, YouTube/yt-dlp, Kick REST)
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
│   ├── chat/             # Chat UI widgets (ChatWidget, ChatWindow, delegate, emote picker, etc.)
│   ├── dialogs/          # Preferences, theme editor, add channel, import/export dialogs
│   ├── stream_list/      # Stream list model and custom delegate
│   ├── app.py            # Main QApplication, AsyncWorker
│   ├── main_window.py    # QMainWindow, toolbar, stream list container
│   └── theme.py          # ThemeManager singleton, stylesheet generation
└── notifications/        # Desktop notification integration
```

### Crash Diagnostics

`faulthandler` is enabled in `main.py` at startup. On SIGSEGV/SIGFPE/SIGABRT, Python prints a traceback to stderr instead of silently crashing. This is always-on and zero-cost unless a crash occurs.

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

**Kick OAuth**: OAuth 2.1 + PKCE flow in `chat/auth/kick_auth.py`. Auto-refreshes expired tokens on 401.

**YouTube chat**: pytchat for reading. Message sending uses InnerTube API with SAPISIDHASH authentication (requires browser cookies copied into Preferences > Accounts). Auto-refreshes expired cookies from the same browser if imported via "Import from Browser" (`CookieRefreshWorker` in manager.py, `extract_cookies_headless()` in youtube_login.py).

**Emotes**: Supports Twitch, 7TV, BTTV, FFZ. Kick emotes are parsed inline from `[emote:ID:name]` tokens in chat messages (not fetched via a provider API). Loaded async per-channel. Rendered inline via `EmoteCache` (shared pixmap dict). Tab-completion via `EmoteCompleter`.

**Emote Caching**: Two-tier (memory LRU 2000 entries + disk 500MB max). User emotes use stale-while-revalidate pattern. Manual refresh via Ctrl+Shift+E.

**Whisper/DM system**: EventSub WebSocket (`WhisperEventSubWorker` in manager.py) for receiving whispers, Helix API for sending. Persisted via `WhisperStore`.

**Reply threading**: Twitch uses `@reply-parent-msg-id` IRC tag, Kick uses `reply_to_original_message_id` API field.

**Per-channel badge caching**: `_badge_url_map` is `dict[str, dict[str, tuple[str, str]]]` (channel_key → badge_id → (url, title)). Badge images are cached per-channel to avoid showing wrong channel's sub badges.

**Banners**: `DismissibleBanner` in `chat_widget.py` uses overlay X button pattern (button as child widget, NOT in layout, repositioned in `resizeEvent`). Used for title banner, socials banner. Hype Chat banner is a simpler inline widget.

**Socials Banner**: Fetched via `SocialsFetchWorker`. Twitch uses GraphQL, YouTube scrapes `/about` page (note: `UC...` IDs need `/channel/UC.../about` format), Kick uses REST API.

**Spellcheck & Autocorrect**: `SpellChecker` in `chat/spellcheck/checker.py` wraps hunspell (system dictionary) with chat-aware skip rules (emotes, URLs, mentions, all-caps). Red wavy underlines drawn in `ChatInput.paintEvent()`. `SpellCompleter` shows click-to-correct popup. Autocorrect (`get_confident_correction`) auto-replaces obvious typos when the user moves past a misspelled word (space + next letter). Confidence rule: correct if apostrophe expansion matches (dont→don't), only 1 suggestion, or top suggestion is within Damerau-Levenshtein distance 1. Bundled adult word list (`data/adult.txt`) prevents profanity from being flagged. Custom dictionary additions sync to hunspell runtime via callback. Corrected words show a green underline for 3 seconds. Both features togglable in Preferences > Chat and the gear menu.

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
| `gui/dialogs/preferences.py` | Preferences dialog (accounts, chat, notifications, themes) |
| `gui/chat/chat_widget.py` | Single-channel chat widget (message list, input, banners) |
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
- **Livestream**: Wraps Channel with live status, `viewers`, `title`, `game`, `start_time`, `last_live_time`, `video_id` (YouTube), `chatroom_id` (Kick), `thumbnail_url`
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
| `gui/dialogs/preferences.py` | Hides "notify-send" notification backend option |

**Distribution**: PyInstaller `--onedir` build, wrapped by Inno Setup `.exe` installer. Bundles `yt-dlp.exe`. Spellcheck unavailable on Windows (`hunspell` C extension is Linux-only). Users install streamlink and mpv separately.

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
| YouTube chat send requires cookies | Copy cookies from browser (SID, HSID, SSID, APISID, SAPISID) into Preferences > Accounts |
| YouTube cookie auto-refresh loops | Guard flag `_yt_cookie_auto_refresh_attempted` must NOT reset in `reconnect_youtube()` — only one attempt per session |
| Badge images showing wrong channel's sub badges | Per-channel `_badge_url_map` with channel-scoped cache keys |
| Chat scrolls even when user scrolled up | Defer buffer trimming with `_trim_paused` flag, flush on scroll-to-bottom |
| Twitch Turbo token "invalid" | Must use browser `auth-token` cookie (not OAuth access token) with `Authorization=OAuth` prefix. Token is client-ID-bound. |
| Streamlink args dropping values like `debug` | `_validate_additional_args` must allow non-flag values after flags (e.g., `--loglevel debug`) |
| Streamlink `--record-and-play` doesn't exist | Use `--record PATH` (plays AND records when a player is configured). `--record-and-play` is not a valid flag; `--record-and-pipe` (`-R`) exists but is deprecated. |
| Twitch shows own messages twice | `TwitchChatConnection` must skip messages from `self._nick` since `ChatManager.send_message()` creates a local echo (Twitch IRC doesn't echo back, but if it does, skip it) |
| `start_new_session=True` on Windows | Use `creationflags=CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW` instead (see `core/streamlink.py`) |
| `os.chmod(0o600)` on Windows | No-op on Windows — skip with `IS_WINDOWS` guard (see `core/credential_store.py`) |
| `notify-send` on Windows | Doesn't exist — `desktop-notifier` handles Windows toast notifications. Hidden from Preferences backend list. |
| `hunspell` package on Windows | C extension can't compile — `hunspell` is Linux-only in pyproject.toml. Spellcheck gracefully disabled on Windows. |
| PyInstaller bundled data files | Use `sys._MEIPASS` for base path in frozen builds vs `__file__` in dev (see `chat/spellcheck/checker.py`) |

## CI/CD

The release workflow (`.github/workflows/release.yml`) runs 3 jobs on tag push (`v*`):

1. **build-flatpak** — Self-hosted Linux runner (`docker01.dd.local`), builds Flatpak in Docker
2. **build-windows** — GitHub-hosted `windows-latest`, builds PyInstaller exe + Inno Setup installer
3. **release** — Collects both artifacts and creates the GitHub Release

**Flatpak runner location**: `docker01.dd.local:/share/bsv/docker-compose/github-runner/`

**Windows build files**: `livestream-list-qt.spec` (PyInstaller), `installer/livestream-list-qt.iss` (Inno Setup)

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
