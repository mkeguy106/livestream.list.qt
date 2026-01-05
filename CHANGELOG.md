# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.6] - 2026-01-04

### Fixed
- "Open in New Window" now works correctly in Flatpak
  - Uses flatpak-spawn to call browser with --new-window flag on host
  - Auto-detects available browsers (Firefox, Chrome, Chromium, Edge)
  - Falls back to xdg-open if no browser found

## [0.5.5] - 2026-01-04

### Fixed
- Notification "Watch" button now works
  - Launches stream with full UI feedback (status messages, Playing indicator)
  - Opens chat automatically if auto-open is enabled
  - Properly handles callback from notification thread via GLib.idle_add

## [0.5.4] - 2026-01-04

### Fixed
- Launch on Login now works correctly in Flatpak
  - Uses `flatpak run` command instead of direct executable
  - Added filesystem permission for ~/.config/autostart

## [0.5.3] - 2026-01-04

### Changed
- Taskbar icon now uses symbolic format with `currentColor` for theme matching
  - Honors desktop environment theme colors automatically
  - Works across KDE, GNOME, and other DEs that support symbolic icons

## [0.5.2] - 2026-01-04

### Changed
- System tray icon now uses IconPixmap for theme-matching colors
  - Reads KDE panel color from theme settings
  - Falls back to light blue that matches Breeze icons
  - Icon matches other system tray icons on KDE Plasma

## [0.5.1] - 2026-01-04

### Fixed
- Flatpak: Added D-Bus permission for StatusNotifierWatcher (system tray support)

## [0.5.0] - 2026-01-04

### Added
- System Tray Icon: Minimize to tray with click to restore and right-click menu
  - Menu includes Open, Notifications toggle, and Quit
  - Uses StatusNotifierItem D-Bus protocol (works on KDE, XFCE, Cinnamon, Budgie, LXQt, MATE, GNOME with extension)
- Run in Background: Keep running when window is closed to receive notifications
  - First-launch prompt to choose behavior (quit or run in background)
  - Toggle in Preferences → General → Startup
- Launch on Startup: Option to start automatically when you log in
  - Creates/removes XDG autostart desktop file
  - Toggle in Preferences → General → Startup

### Changed
- YouTube account message in Preferences now matches Kick (simplified, no yt-dlp status)

## [0.4.2] - 2026-01-03

### Changed
- Live duration format: Now uses human-readable format
  - Under 5 minutes: "Under 5 minutes"
  - Minutes only: "X minute" or "X minutes"
  - With hours: "X hour Y minutes" or "X hours Y minutes"
  - With days: "X day Y hours Z minutes" (with proper plurals)
  - Removed "Live:" prefix

## [0.4.1] - 2026-01-03

### Changed
- About dialog: Now uses custom dialog with "Check for Updates" button
- Check for Updates moved from menu to About dialog only

### Fixed
- Update check error handling: Better messages for network errors and no releases

## [0.4.0] - 2026-01-03

### Added
- Live Duration: Shows how long streams have been live (hh:mm or dd:hh:mm format)
  - Displayed to the right of channel name for live streams
  - Updated on every refresh
- Sort by Time Live: New sort option to order by how long streams have been live
- Channel Information settings: New preferences section under General
  - Toggle to show/hide Live Duration
  - Toggle to show/hide Viewer Count
- Check for Updates: New menu item to check GitHub for the latest release
  - Shows message if running latest version
  - Prompts to visit download page if update available

### Changed
- About dialog: Removed "What's New" section (release notes)
- Preferences: Channel Information section now appears above Channel Icons

## [0.3.9] - 2026-01-03

### Added
- Platform Icon toggle: New option in Preferences → Channel Icons to show/hide the T/Y/K platform indicator
- About dialog improvements:
  - Credits section for bundled dependencies (yt-dlp, aiohttp, desktop-notifier)
  - "Check for Updates" link to GitHub releases

## [0.3.8] - 2026-01-03

### Added
- Play button: New icon on each channel row to launch streams (same as double-click)
- Channel Icons settings: New preferences section to show/hide each icon type
  - Play button, Favorite button, Chat button, Browser button
  - All icons shown by default

### Changed
- Icon order: Rearranged icons from right to left as Play, Favorite, Chat, Browser
- Stop button now appears in place of Play button when stream is playing

## [0.3.7] - 2026-01-03

### Added
- Open channel in browser: New button on each channel row opens the channel page in your browser
  - Uses the same browser setting as chat (System Default or configured browser)
  - Opens in new window when enabled in Chat settings

### Fixed
- Automatic refresh timer: Refresh now works correctly every 60 seconds
  - Previously the refresh loop was not running after initial load
  - Notifications now trigger properly when streams go live

## [0.3.6] - 2026-01-03

### Fixed
- Empty list feedback: Show "Checking stream status..." during initial load when filters hide all channels
- After load completes with Favorites + Hide Offline: Show "No live favorites to show" message

## [0.3.5] - 2026-01-03

### Added
- Kick last seen: Offline Kick channels now show when they last streamed
  - Uses Kick videos API to fetch most recent VOD start time

## [0.3.4] - 2026-01-03

### Added
- Sort by Last Seen: New sort option to order offline channels by when they last streamed

### Changed
- Updated Flatpak to GNOME 48 runtime
- Selection mode button now uses delete icon (edit-delete-symbolic)
- Renamed "Selection Mode" tooltip to "Delete Channels"

## [0.3.3] - 2026-01-03

### Fixed
- Python 3.11 compatibility: Reset aiohttp sessions in async context to fix Flatpak

## [0.3.2] - 2026-01-03

### Added
- Last seen time for offline channels (Twitch and YouTube)
  - Shows time since last stream to right of channel name
  - Twitch: Uses GraphQL lastBroadcast data
  - YouTube: Fetches most recent video upload date via yt-dlp

### Changed
- Default UI style is now more compact (reduced row padding)
- Removed spacing between channel name and stream title
- App icon updated to dark grey and white color scheme

## [0.3.1] - 2026-01-03

### Added
- YouTube stream detection using yt-dlp (no API key required)
  - yt-dlp bundled as dependency for Flatpak
  - Detects live streams, title, viewers, video ID
- YouTube live chat support
  - Opens `youtube.com/live_chat?v={video_id}` when stream is live
- YouTube URL parsing: `youtube.com/username` auto-detects platform

### Changed
- All toolbar elements now scale in compact modes (dropdowns, search entry)

### Fixed
- Notification error when buttons parameter was None
- YouTube chat now uses correct video ID instead of channel ID

## [0.3.0] - 2026-01-03

### Added
- Multi-platform support: Add channels from Twitch, YouTube, and Kick
- URL parsing: Paste channel URLs to auto-detect platform
- Clipboard auto-paste: URLs in clipboard are auto-populated when adding channels
- Platform filter: Filter by platform (All, Twitch, YouTube, Kick) in toolbar
- Platform colors: Color channel names by platform (Twitch purple, Kick green, YouTube red)
- Export/Import: Backup and restore channels and settings to JSON files
  - Includes schema version and app version for compatibility tracking
  - Option to import channels only or channels + settings
- Kick chat support: Opens Kick chatroom in browser

### Changed
- Chat launcher now supports multiple platforms (Twitch and Kick)

## [0.2.2] - 2026-01-03

### Added
- Chat integration: Open Twitch chat in browser
  - Browser selection: System Default, Chrome, Chromium, Edge, Firefox
  - Chat URL types: Popout (recommended), Embedded, Default (legacy)
  - Auto-open chat when launching streams (configurable)
  - Chat button on stream rows (scales with compact modes)
- Last Seen: Offline channels show when they were last live (e.g., "2h ago", "3d ago")
- Batched GraphQL queries for better Twitch API performance (35 channels per request)
- Smart notifications: Only notify for streams transitioning from offline to online
- Startup notification suppression: No notification flurry on app launch
- Auto-refresh list when starting stream if sorted by Playing

### Fixed
- GLib.idle_add infinite loop bug causing hundreds of chat windows to open

## [0.2.1] - 2025-01-02

### Added
- Header bar icons scale in compact modes

## [0.2.0] - 2025-01-02

### Added
- Favorite channels with star button toggle
- Favorites filter to show only favorite channels
- Window size persistence (saved on close, restored on launch)
- Window size settings in Preferences
- Compact 3 UI style (most compact)
- Toolbar elements scale with compact modes
- Streamlink/player path and arguments in Preferences with example hints

### Changed
- Moved "Hide Offline" checkbox to left of filter
- Stop button and favorite star scale appropriately in compact modes

## [0.1.2] - 2024-01-02

### Fixed
- Flatpak: Use `flatpak-spawn --host` to run streamlink/mpv on host system

## [0.1.1] - 2024-01-02

### Fixed
- Flatpak: Fixed application ID mismatch causing D-Bus registration failure

## [0.1.0] - 2024-01-02

### Added
- Initial release
- Twitch channel monitoring with live status detection
- Desktop notifications when streams go live
- Streamlink integration for launching streams in external players
- Import followed channels from Twitch
- Name filter with wildcard support
- Sort by Name, Viewers, or Playing status
- Hide Offline filter
- UI styles: Default, Compact 1, Compact 2
- Stream playback tracking with stop button
- Selection mode for bulk channel deletion
- Flatpak build support

[Unreleased]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.5.6...HEAD
[0.5.6]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.5.5...v0.5.6
[0.5.5]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.9...v0.4.0
[0.3.9]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.8...v0.3.9
[0.3.8]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.7...v0.3.8
[0.3.7]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/mkeguy106/livestream-list-linux/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mkeguy106/livestream-list-linux/releases/tag/v0.1.0
