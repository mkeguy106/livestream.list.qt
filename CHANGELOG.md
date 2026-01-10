# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.18] - 2026-01-10

### Fixed
- File > Quit and Ctrl+Q now properly quit the app instead of minimizing to tray

## [0.5.17] - 2026-01-10

### Fixed
- Window position now preserved when minimizing to tray and restoring
  - Uses minimize instead of hide to work around Wayland compositor limitations
  - Window geometry (position and size) saved to settings on close

## [0.5.16] - 2026-01-10

### Fixed
- Preferences "Run in background when closed" and "Launch on login" not saving
  - PySide6 enum comparison issue: `Qt.Checked` vs `Qt.CheckState.Checked.value`
- Window not coming to focus when restoring from system tray
  - Now properly restores minimized state and activates window

## [0.5.15] - 2026-01-09

### Fixed
- YouTube browser button now opens the live stream instead of channel landing page
  - URLs now use `/live` suffix (e.g., `youtube.com/@channel/live`)

## [0.5.14] - 2026-01-09

### Fixed
- Notifications not appearing when sound is enabled
  - Fixed invalid hint format: `int:sound-file:default` -> `string:sound-name:message-new-instant`

## [0.5.13] - 2026-01-09

### Fixed
- YouTube stream launching for channels using handles (e.g., `@username`)
  - Stream URL now correctly uses handle format instead of channel ID format

## [0.5.12] - 2026-01-09

### Fixed
- CI/CD build pipeline for Docker-in-Docker environments
  - Uses `docker cp` instead of volume mounts for self-hosted runners

## [0.5.11] - 2026-01-09

### Fixed
- Kick channel lookup now works (upgraded to KDE Platform 6.9 with newer OpenSSL)
- Test notification button works in Flatpak sandbox (uses `flatpak-spawn --host`)

### Changed
- Renamed app from "Livestream List Qt" to "Livestream List (Qt)" everywhere

## [0.5.10] - 2026-01-09

### Fixed
- Kick stream duration now displays correctly (uses `start_time` field)
- Notification Watch button works in Flatpak (marshals callback to main thread via Qt Signal)
- OAuth login UI updates properly during authentication flow

## [0.5.9] - 2026-01-09

### Fixed
- OAuth port conflict (uses `ReuseAddrHTTPServer` with `allow_reuse_address`)

## [0.5.8] - 2026-01-09

### Fixed
- Datetime comparison errors (consistent use of timezone-aware datetimes)

## [0.5.7] - 2026-01-09

### Fixed
- QThread garbage collection crashes (AsyncWorker now uses `parent=self`)
- aiohttp session errors in threads (reset `_session` before creating new event loop)

## [0.5.6] - 2026-01-09

### Added
- Initial Qt release - complete rewrite from GTK4/Libadwaita to PySide6/Qt6
- Full feature parity with GTK version
- All core functionality preserved:
  - Multi-platform support (Twitch, YouTube, Kick)
  - Desktop notifications with Watch button
  - System tray icon
  - Streamlink integration
  - Chat integration
  - Import Twitch follows
  - Export/Import channels and settings
  - Favorites and filtering
  - Multiple UI compact modes

### Changed
- UI framework from GTK4/Libadwaita to PySide6/Qt6
- Configuration directory from `~/.config/livestream-list` to `~/.config/livestream-list-qt`
- Flatpak runtime from GNOME to KDE Platform

[Unreleased]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.18...HEAD
[0.5.18]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.17...v0.5.18
[0.5.17]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.16...v0.5.17
[0.5.16]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.15...v0.5.16
[0.5.15]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.14...v0.5.15
[0.5.14]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.13...v0.5.14
[0.5.13]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.12...v0.5.13
[0.5.12]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.11...v0.5.12
[0.5.11]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.10...v0.5.11
[0.5.10]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.9...v0.5.10
[0.5.9]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.8...v0.5.9
[0.5.8]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.7...v0.5.8
[0.5.7]: https://github.com/mkeguy106/livestream.list.qt/compare/v0.5.6...v0.5.7
[0.5.6]: https://github.com/mkeguy106/livestream.list.qt/releases/tag/v0.5.6
