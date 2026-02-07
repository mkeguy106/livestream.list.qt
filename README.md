# Livestream List (Qt)

A PySide6/Qt6 application for monitoring livestreams on Twitch, YouTube, and Kick. Inspired by [Livestream.Monitor](https://github.com/laurencee/Livestream.Monitor) for Windows.

This is a Qt port of [livestream.list.linux](https://github.com/mkeguy106/livestream-list-linux) (GTK4/Libadwaita version).

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
  - Low-latency defaults for Twitch streams
- **Built-in Chat Client** - Native multi-channel chat with tabbed interface
  - Twitch and Kick chat with full emote and badge rendering
  - Emote support: Twitch, Kick native, 7TV, BTTV, FFZ
  - Tab-completion for emotes (press Tab while typing)
  - Tooltips on emotes and badges (shows name and provider)
  - Click username to view user's chat history
  - Right-click for user actions (block, open channel)
  - Sub/resub/raid/gift alerts (USERNOTICE) with themed styling
  - Hype Chat (paid pinned messages) with dismissable banner
  - Pop-out chat tabs into standalone windows
  - Customizable tab colors via color picker
  - Twitch OAuth login for sending messages
  - Kick OAuth 2.1 + PKCE login for sending messages
  - Twitch whispers/DMs (send and receive direct messages)
  - Reply to messages (right-click → Reply, with visual indicator)
  - @mention autocomplete (type @ to suggest usernames)
  - @mention highlighting with tab flashing for notifications
  - Conversation view (click @mention or reply to see back-and-forth)
  - Real-time spellcheck with custom dictionary
  - Chat scroll pause in busy channels (auto-resumes after 5 min)
  - Recent chat history on channel join (Twitch)
  - Badge tooltips showing descriptive titles (e.g., "6-Month Subscriber")
  - Copy messages with Ctrl+C
- **Browser Chat** - Open stream chat in browser (Twitch, Kick, YouTube)
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
  - Shift+click range selection
  - Trash bin for soft-delete with restore
- **UI Styles** - Default, Compact 1, Compact 2, Compact 3 layouts
  - All UI elements scale with compact modes (buttons, icons, toolbar)
- **Stream Playback Tracking** - Shows "Playing" indicator with stop button
- **Window Size Persistence** - Remembers window size between sessions

## Requirements

- Python 3.10+
- PySide6 (Qt6)
- yt-dlp (bundled in Flatpak, for YouTube stream detection)
- Streamlink (optional, for launching streams)
- mpv (optional, default player)

## Installation

### Flatpak (Recommended)

Download `livestreamListQt.flatpak` from [Releases](https://github.com/mkeguy106/livestream.list.qt/releases).

```bash
# Install (will also install KDE runtime dependencies from Flathub)
flatpak install --user ~/Downloads/livestreamListQt-v*.flatpak

# Run
flatpak run life.covert.livestreamListQt

# Or launch from your application menu: "Livestream List (Qt)"
```

### From Source

```bash
# Clone and install
git clone https://github.com/mkeguy106/livestream.list.qt.git
cd livestream.list.qt
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
livestream-list-qt
# or
python -m livestream_list
```

### Keyboard Shortcuts

- `Ctrl+N` - Add channel
- `Ctrl+R` / `F5` - Refresh
- `Ctrl+,` - Preferences
- `Ctrl+Q` - Quit
- `Ctrl+Shift+E` - Refresh emotes (chat)
- `Ctrl+C` - Copy message (chat)
- `Escape` - Cancel reply / close popup (chat)

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

### Chat

The app supports two chat modes (configurable in Preferences > Chat):

**Built-in Chat** (recommended):
- Click the chat icon on any live stream to open the built-in chat window
- Tabbed interface for multiple channels simultaneously
- Login to Twitch or Kick in Preferences > Accounts to send messages
- Right-click tab to pop out into standalone window
- Settings: font size, timestamps, emote providers, badge visibility, tab colors, max messages

**Browser Chat**:
- Opens chat in your system browser (Chrome, Firefox, etc.)
- **Chat URL Type**: Popout (recommended), Embedded, or Default (legacy) - Twitch only
- **Auto-open**: Automatically open chat when launching streams
- Supported: Twitch (popout/embedded/default), Kick (chatroom), YouTube (live chat)

## Configuration

Settings are stored in `~/.config/livestream-list-qt/settings.json`

Channels are stored in `~/.config/livestream-list-qt/channels.json`

### Streamlink Settings

Configure in Preferences > Streamlink:
- **Streamlink Path**: `streamlink` or full path
- **Streamlink Arguments**: e.g., `--twitch-low-latency --twitch-disable-ads`
- **Player**: `mpv` (default), `vlc`, or any video player
- **Player Args**: e.g., `--fullscreen --volume=80`

## Uninstall

```bash
# Flatpak
flatpak uninstall life.covert.livestreamListQt

# Manual installation
rm -rf ~/.config/livestream-list-qt
```

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Roadmap

Planned features for future releases:

- [ ] User ignore list — right-click to hide a user's messages, with a settings page to review and unblock
- [ ] Custom highlight keywords — trigger mention-style highlights for specific words/phrases
- [ ] Chat mode indicators — show sub-only, emote-only, slow mode, followers-only status
- [ ] Message character counter — show remaining characters near the input
- [ ] User card popup — click a username to see account age, follow date, channel history
- [ ] Split view — view two chats side by side in the same window
- [ ] Chat log export — save chat history to a text file
- [ ] Timestamp format option — 12h vs 24h toggle
- [ ] Auto-complete recent emotes first — sort emote suggestions by usage frequency
- [ ] Hype train banner — show active Twitch hype train progress in the chat banner area (requires EventSub or GraphQL; EventSub needs broadcaster auth with `channel:read:hype_train` scope, GraphQL is unofficial but works for any channel)
- [ ] Higher resolution emotes — research fetching higher-res emote variants (2x/3x) from providers for sharper rendering on HiDPI displays
- [ ] Discover Channels — find new streamers to watch with browsable categories:
  - Top Streamers (highest viewer counts across platforms)
  - New Streamers (recently started streaming / rising channels)
  - Trending (channels with unusual viewer growth or spikes)
  - By Game/Category (browse streamers by what they're playing)
  - Recommended (based on channels you already follow)
  - Just Went Live (channels that started streaming in the last few minutes)
  - Rising Stars (small streamers with growing audiences)

## License

GPL-2.0 - See [LICENSE](LICENSE) for details.

## Acknowledgments

- Inspired by [Livestream.Monitor](https://github.com/laurencee/Livestream.Monitor) for Windows.
- Original GTK4 version: [livestream.list.linux](https://github.com/mkeguy106/livestream-list-linux)
