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
  - Custom notification sound (WAV/OGG/MP3/FLAC)
  - Urgency levels (low/normal/critical)
  - Configurable timeout
  - Per-platform filter (Twitch, YouTube, Kick)
  - Quiet hours scheduling (e.g., 22:00 to 08:00)
  - Raid notifications for open chat channels
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
  - User card popup — click username to see account age, badges, notes, and history
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
  - Up/down arrow to cycle through previously sent messages
  - Message character counter (500 for Twitch/Kick, 200 for YouTube)
  - Configurable timestamp format (12-hour or 24-hour)
  - Chat mode indicators (sub-only, slow mode, emote-only, followers-only, R9K)
  - Slow mode countdown timer in input box
  - Deleted message options (strikethrough, truncated, or hidden)
  - Chat log export to text file
  - Emote autocomplete prioritizes recently used emotes
  - Emote picker popup (Ctrl+E) — searchable grid organized by provider, channel emotes first with separator
  - Emote picker animates emotes with viewport culling (only visible emotes animate)
  - Emote picker filter dropdown: All / Animated / Static
  - Emote picker auto-downloads missing emotes on open (channel emotes prioritized)
  - Sub-only channel emotes greyed out with "Subscribe to use" tooltip
  - In-chat search with predicates (Ctrl+F) — from:user, has:link, is:sub, is:mod
  - Link tooltip previews — hover URLs to see page title
  - Zero-width emotes — 7TV overlay emotes that stack on other emotes
  - User card on hover — hover username to see profile image, bio, followers, follow age, pronouns
  - User card text selectable and copyable (Ctrl+C or right-click)
  - Hype Train banner — purple-themed progress bar with level, goal, and countdown timer
  - Raid banner — orange-themed banner with raider name and viewer count, 120s countdown timer
  - Chat logging — persistent JSONL/text per-channel logs with disk rotation
  - Chat history — load recent messages from disk logs when opening a channel
  - Always on Top toggle in gear menu (persisted, works on Wayland via KWin scripting)
- **Always on Top** - Keep main window above other windows (View menu toggle, persisted)
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
- **Theme System** - 6 built-in themes (Dark, Light, High Contrast, Nord, Monokai, Solarized) plus custom themes
  - Theme editor in Preferences with per-color customization
  - Import/export themes as JSON
  - Quick cycle through themes via toolbar button
- **UI Styles** - Default, Compact 1, Compact 2, Compact 3 layouts
  - All UI elements scale with compact modes (buttons, icons, toolbar)
- **Alternating Row Colors** - Stream list and chat support alternating row backgrounds
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

- [x] User ignore list — right-click to hide a user's messages, with a settings page to review and unblock
- [x] Custom highlight keywords — trigger mention-style highlights for specific words/phrases
- [x] Chat mode indicators — show sub-only, emote-only, slow mode, followers-only status
- [x] Message character counter — show remaining characters near the input
- [x] User card popup — click a username to see account age, follow date, channel history
- [ ] Split view — view two chats side by side in the same window
- [x] Chat log export — save chat history to a text file
- [x] Timestamp format option — 12h vs 24h toggle
- [x] Auto-complete recent emotes first — sort emote suggestions by usage frequency
- [x] Hype train banner — show active Twitch hype train progress in the chat banner area (requires EventSub or GraphQL; EventSub needs broadcaster auth with `channel:read.hype_train` scope, GraphQL is unofficial but works for any channel)
- [x] Higher resolution emotes — HiDPI-aware emote rendering using 2x/3x variants from providers for sharp display on high-DPI screens
- [x] In-chat search — Ctrl+F to search within a channel's messages, with predicates like `from:user`, `has:link`, `is:sub`
- [x] Emote picker popup — resizable grid of available emotes with search, browsable by category/provider
- [x] Deleted message options — configurable handling of deleted messages: strikethrough, truncated, or fully hidden
- [x] Slow mode countdown — show countdown timer in input box indicating when you can send your next message
- [x] Link tooltip previews — hover over links to see rich previews (YouTube thumbnails, Twitter embeds, etc.)
- [x] Zero-width emotes — support 7TV/BTTV overlay emotes that stack on top of other emotes (e.g., slide, rainbow)
- [ ] Smooth scrolling — animated scroll on new messages instead of jumping. Qt defers QListView layout so `scrollbar.maximum()` is stale at call time. Possible fixes: (1) `QTimer.singleShot(0, ...)` to defer animation until after layout pass, (2) use `scrollbar.rangeChanged` signal to trigger animation when range updates, (3) hybrid — instant `scrollToBottom()` for auto-scroll, animate only on "New messages" button click where layout is stable
- [ ] Streamer mode — auto-detect OBS/streaming software and hide usernames/whispers for privacy
- [x] Reply thread popup — open full reply threads in a dedicated popup window
- [x] User nicknames — assign custom local display names to other users
- [x] User notes — attach notes to users, visible on their user card
- [ ] Multiple accounts — quick account switcher popup for managing multiple logins per platform
- [ ] Configurable mod timeout buttons — quick mod-action buttons with customizable durations
- [x] Previous message cycling — up/down arrows to cycle through previously sent messages
- [ ] Live emote updates — 7TV EventAPI for real-time emote add/remove without manual refresh
- [x] Custom themes — full theme customization via theme editor with import/export, 6 built-in themes
- [x] Pronouns display — show user pronouns on user cards
- [x] Always-on-top — separate toggles for main window (View menu) and chat window (gear menu), with KWin scripting on KDE Plasma for Wayland compatibility
- [x] Prediction badge details — show picked outcome in prediction badges
- [ ] Discover Channels — find new streamers to watch with browsable categories:
  - Top Streamers (highest viewer counts across platforms)
  - New Streamers (recently started streaming / rising channels)
  - Trending (channels with unusual viewer growth or spikes)
  - By Game/Category (browse streamers by what they're playing)
  - Recommended (based on channels you already follow)
  - Just Went Live (channels that started streaming in the last few minutes)
  - Rising Stars (small streamers with growing audiences)
- [ ] VOD chat replay — play back chat messages from recorded Twitch VODs synced to the video timeline
- [x] Raid notifications — desktop notification + in-chat orange banner when a Twitch channel is raided (requires chat open)
- [x] Chat logging — persistent JSONL/text per-channel logging with configurable disk rotation and history loading on chat open
- [x] Notification improvements — custom sound, urgency, timeout, per-platform filter, and quiet hours scheduling

## License

GPL-2.0 - See [LICENSE](LICENSE) for details.

## Acknowledgments

- Inspired by [Livestream.Monitor](https://github.com/laurencee/Livestream.Monitor) for Windows.
- Original GTK4 version: [livestream.list.linux](https://github.com/mkeguy106/livestream-list-linux)
