# Livestream List for Linux

A GTK4/Libadwaita application for monitoring livestreams on Twitch, YouTube, and Kick. Inspired by [Livestream.Monitor](https://github.com/laurencee/Livestream.Monitor) for Windows.

---

> **Warning**
>
> This project was entirely vibe-coded with Claude AI. I'm just some guy who threw prompts at an AI until something worked. Use at your own risk.
>
> ![Claude Code is all you need](https://preview.redd.it/claude-code-is-all-you-need-v0-uizq4o7ae39f1.jpg?width=500&format=pjpg&auto=webp&s=ab076cd0c28db7d74c41b8fe86da8c03f505eb8b)

---

## Features

- **Multi-Platform Support** - Add channels from Twitch, YouTube, and Kick
  - Paste URLs to auto-detect platform (e.g., `https://twitch.tv/username`)
  - Clipboard auto-paste when opening add dialog
- **Desktop Notifications** - Get notified when streams go live (smart notifications - no flurry on startup)
- **System Tray Icon** - Minimize to tray, click to restore, right-click menu
  - Works on KDE, XFCE, Cinnamon, Budgie, LXQt, MATE, and GNOME (with AppIndicator extension)
- **Run in Background** - Keep running when window is closed to receive notifications
  - First-launch prompt to choose behavior (quit or run in background)
- **Launch on Startup** - Option to start automatically when you log in
- **Streamlink Integration** - Double-click to launch streams in mpv/VLC with playback tracking
- **Chat Integration** - Open stream chat in browser (Twitch, Kick, YouTube)
  - Auto-open chat when launching streams
  - Browser selection: System Default, Chrome, Chromium, Edge, Firefox
  - YouTube chat works when stream is live
- **Import Twitch Follows** - Login to Twitch and import your followed channels
- **Export/Import** - Backup and restore channels and settings
  - Includes app version for compatibility tracking
  - JSON format for easy inspection
- **Favorites** - Star your favorite channels and filter to show only favorites
- **Last Seen** - Offline channels show when they were last live (e.g., "2h ago", "3d ago")
- **Platform Filter** - Filter by platform (All, Twitch, YouTube, Kick)
- **Platform Colors** - Color channel names by platform (purple for Twitch, green for Kick)
- **Name Filter** - Filter channels by name with wildcard support (e.g., `*gaming*`)
- **Sort Options** - Sort by Name, Viewers, or Playing status
- **Hide Offline** - Toggle to show only live streams
- **Selection Mode** - Multi-select channels for bulk deletion
- **UI Styles** - Default, Compact 1, Compact 2, Compact 3 layouts
  - All UI elements scale with compact modes (buttons, icons, header bar)
- **Stream Playback Tracking** - Shows "Playing" indicator with stop button
- **Window Size Persistence** - Remembers window size between sessions

## Requirements

- Python 3.11+
- GTK 4.0
- Libadwaita 1.0
- yt-dlp (bundled in Flatpak, for YouTube stream detection)
- Streamlink (optional, for launching streams)
- mpv (optional, default player)

## Installation

### Flatpak (Recommended)

Download `livestreamList.flatpak` from [Releases](https://github.com/mkeguy106/livestream-list-linux/releases).

```bash
# Install (will also install GNOME runtime dependencies from Flathub)
flatpak install --user ~/Downloads/livestreamList.flatpak

# Run
flatpak run life.covert.livestreamList

# Or launch from your application menu: "Livestream List"
```

### Arch Linux

```bash
# Install system dependencies
sudo pacman -S python python-gobject gtk4 libadwaita streamlink mpv

# Clone and install
git clone https://github.com/mkeguy106/livestream-list-linux.git
cd livestream-list-linux
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Other Distributions

Install GTK4 and Libadwaita development packages for your distribution, then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
livestream-list
# or
python -m livestream_list
```

### Keyboard Shortcuts

- `Ctrl+N` - Add channel
- `Ctrl+R` / `F5` - Refresh
- `Ctrl+,` - Preferences
- `Ctrl+Q` - Quit

### Adding Channels

1. Click the + button or press `Ctrl+N`
2. Paste a channel URL (e.g., `https://twitch.tv/username` or `https://kick.com/channel`)
   - Platform is auto-detected from URLs
   - If you have a URL in clipboard, it's auto-pasted
3. Or enter a channel name and select platform
4. Click Add

### Importing Twitch Follows

1. Open Preferences (`Ctrl+,`)
2. Go to Accounts tab
3. Click "Login" to authorize with Twitch
4. Click "Import Follows" to import your followed channels

### Watching Streams

- Double-click a live stream to launch it in your player
- The row shows "Playing" indicator while the stream is open
- Click the stop button to close playback
- Chat can auto-open when launching streams (configure in Preferences > Chat)

### Chat Options

Configure in Preferences > Chat:
- **Browser**: System Default, Chrome, Chromium, Edge, or Firefox
- **Chat URL Type**: Popout (recommended), Embedded, or Default (legacy) - Twitch only
- **Auto-open**: Automatically open chat when launching streams

Supported platforms: Twitch (popout/embedded/default), Kick (chatroom), YouTube (live chat)

## Configuration

Settings are stored in `~/.config/livestream-list/settings.json`

Channels are stored in `~/.local/share/livestream-list/channels.json`

### Streamlink Settings

Configure in Preferences > Streamlink:
- **Streamlink Path**: `streamlink` or full path
- **Streamlink Arguments**: e.g., `--twitch-low-latency --twitch-disable-ads`
- **Player**: `mpv` (default), `vlc`, or any video player
- **Player Args**: e.g., `--fullscreen --volume=80`

## Uninstall

```bash
# Flatpak
flatpak uninstall life.covert.livestreamList

# Manual installation
rm -rf ~/.config/livestream-list
rm -rf ~/.local/share/livestream-list
```

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Roadmap

Planned features and improvements:

- [x] **Simplify YouTube Account Message** - Replace yt-dlp status in Preferences → Accounts → YouTube with the same message used for Kick
- [x] **Launch on Startup** - Option to start the application automatically on login
- [x] **Run in Background** - Keep running when window is closed, with first-launch prompt
- [x] **System Tray Icon** - Click to restore window, right-click menu with Open/Notifications/Quit
- [ ] **Responsive Channel Layout** - Preserve channel name visibility when resizing window; hide live duration before truncating channel name
- [x] **Notification Watch Button** - Make the "Watch" button in notifications functional (launches stream like double-click)
- [ ] **Chatterino/Chatty Support** - Integration with popular standalone chat clients
- [ ] **UI Scaling** - Ctrl+scroll zoom behavior for the channel list
- [ ] **Qt Port** - Possible rewrite or fork using Qt framework

## License

GPL-2.0 - See [LICENSE](LICENSE) for details.

## Acknowledgments

Inspired by [Livestream.Monitor](https://github.com/laurencee/Livestream.Monitor) for Windows.
