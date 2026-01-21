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

# Type check
mypy src/

# Run tests (currently empty)
pytest tests/

# Run single test
pytest tests/test_file.py::test_name -v
```

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

### Key Files

| File | Purpose |
|------|---------|
| `src/livestream_list/gui/app.py` | Main QApplication, AsyncWorker, signal-based threading |
| `src/livestream_list/gui/main_window.py` | QMainWindow, StreamRow widget, all dialogs |
| `src/livestream_list/gui/tray.py` | QSystemTrayIcon for system tray |
| `src/livestream_list/core/monitor.py` | StreamMonitor - channel tracking, refresh logic |
| `src/livestream_list/core/settings.py` | Settings persistence (JSON) |
| `src/livestream_list/api/twitch.py` | Twitch Helix + GraphQL client |
| `src/livestream_list/api/base.py` | Abstract base class for API clients with retry logic |
| `src/livestream_list/notifications/notifier.py` | Desktop notifications with Watch button |
| `src/livestream_list/core/chat.py` | Chat launcher for opening stream chat in browser |
| `src/livestream_list/__version__.py` | Single source of truth for version |
| `src/livestream_list/core/models.py` | Data classes: Channel, Livestream, StreamPlatform |
| `src/livestream_list/core/streamlink.py` | Stream launch via streamlink or yt-dlp |

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

## Git Commits

Never include in commit messages:
- "Generated with Claude Code"
- "Co-Authored-By: Claude"
- Any reference to AI, Claude, or automated generation
