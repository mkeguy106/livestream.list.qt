# Livestream List — Design System

A design system distilled from **Livestream List (Qt)**, a PySide6/Qt6 desktop app for monitoring livestreams across **Twitch, YouTube, Kick, and Chaturbate**. The app lives in one dense window: a striped list of streamer rows on the left, a chat dock on the right, and a terse toolbar above.

The visual identity is **deep navy + Twitch purple**. Rows are tight, text is small, color is used almost exclusively to encode *state* — live/offline, platform, mention, raid, subscription — not decoration.

---

## Sources

This document is a distillation of the living Qt implementation. The **canonical source of truth is the code**; if this document drifts from `theme.py` or the Qt widgets, the code wins.

- [`src/livestream_list/gui/theme.py`](../../src/livestream_list/gui/theme.py) — the canonical color tokens (Dark / Light / High-Contrast palettes, platform colors, stylesheet generator)
- [`src/livestream_list/gui/`](../../src/livestream_list/gui/) — Qt widgets, the source of layout, density, and component coverage
- [`data/app.livestreamlist.LivestreamListQt.svg`](../../data/app.livestreamlist.LivestreamListQt.svg) — app icon
- [`docs/screenshots/`](../screenshots/) — dark/light/compact main window, chat window, preferences tabs

**Sibling implementations (not covered here):** `livestream.list.xpf` (Avalonia C# cross-platform fork), `livestream-list-gtk` (GTK4 Linux fork). This design system describes the **Qt** version specifically.

---

## The product in one paragraph

Livestream List is an open-source Linux desktop utility for people who follow streamers across multiple platforms. You paste a channel URL, it pings the platform APIs on an interval, and when someone goes live a row turns green, a desktop notification fires, and a tray icon updates. Double-click a row and it hands off to `mpv` via `streamlink`; click the chat icon and a native Qt chat panel opens with full emote/badge rendering (Twitch, Kick, 7TV, BTTV, FFZ), reply threads, emote picker, hype-train banner, raid banner. It is utilitarian, for power users, on Linux — the look is Qt-native dark mode, not a marketing site.

---

## Product surfaces

There is **one surface**: the desktop app. Inside it, these are the primary views the UI kit recreates:

| View | Purpose |
|---|---|
| **Main window** | Stream list with toolbar, filters, per-row action buttons |
| **Chat window** | Multi-tab chat dock with banner, message list, composer |
| **Preferences** | Tabbed settings: General / Playback / Chat / Appearance / Accounts |
| **Add channel dialog** | Single input, URL auto-detect |
| **Theme editor** | Live-editable color pickers for every token |

A marketing website does **not** exist. There are `README.md` and `CHANGELOG.md`, and that's it.

---

## CONTENT FUNDAMENTALS

**Voice: plain, capable, self-deprecating.** The README opens with a literal *"this project was entirely vibe-coded with Claude AI. I'm just some guy who threw prompts at an AI until something worked. Use at your own risk."* That sets the tone — honest, first-person, no marketing. Error states and tooltips are terse and functional; feature names are named for what they do.

**Person & address.**
- Feature descriptions: declarative, no pronoun (*"Import followed channels from Twitch and Chaturbate"*, *"Launch streams in mpv with streamlink integration"*).
- Instructions to the user: second-person, imperative (*"Paste a channel URL"*, *"Click the + button or press Ctrl+N"*).
- Empty / status strings: minimal, often a single noun or phrase — "Ready", "Not logged in — Twitch chat is read-only", "12 live / 20 total".

**Casing.**
- **Title Case** for menus, dialog titles, buttons, tab labels: *"Add Channel"*, *"Import Follows"*, *"Always on Top"*, *"Open Log Directory"*.
- **Sentence case** for descriptions and body copy in preferences: *"Run in background when closed"*, *"Enable file logging"*.
- **UPPERCASE** is reserved for chat system messages and subscription/raid events (*"LET'S GO SHROUD"* is user-generated — the UI itself doesn't shout).

**Numbers & time.**
- Relative time on rows (*"2 hours 15 minutes"*, *"6h ago"*, *"3d ago"*) — expanded form when live, short form when last-seen.
- Viewer counts abbreviated (*"127.5K"*, *"24.9K"*, *"1.2K"*) with one decimal.
- Chat timestamps are 24h by default (*"12:27"*), toggleable to 12h in Preferences.
- Character counter is a bare number: *"487 / 500"*.

**Tone specimens (lift these verbatim when writing new copy):**

| Context | Copy |
|---|---|
| Tagline | *"A Qt6 application for monitoring livestreams on Twitch, YouTube, and Kick"* |
| Status bar | *"Ready"*, *"12 live / 20 total"* |
| Empty chat | *"Log in to Twitch to chat"* |
| Sub-only tooltip | *"Subscribe to use"* |
| Read-only notice | *"Not logged in — Twitch chat is read-only"* |
| Sub event | *"PixelWarrior subscribed at Tier 1. They've subscribed for 6 months!"* |
| Badge tooltip | *"6-Month Subscriber"* |
| Quiet-hours setting | *"Quiet hours scheduling (e.g., 22:00 to 08:00)"* |
| CLI flag help | `-m, --allow-multiple — Allow multiple instances to run simultaneously` |

**Emoji.** Never in app chrome. Frequent in user-generated chat content (rendered as real emoji) and in stream titles the user imports — but the product itself doesn't use emoji in buttons, menus, headings, or copy. The visual vocabulary prefers a tight glyph (★, ▶, +) or a single-letter glyph (B, C, A) on each row button.

**Unicode as iconography.** The row action buttons in the main window are literally the letters **B C ★ A ▶** — Browser, Chat, Favorite, Auto-play, Play. This is a deliberate house style: one-character affordances in a 20×20 box. When you need an icon the app doesn't ship, pick one letter, don't draw an SVG.

---

## VISUAL FOUNDATIONS

### Palette
Three canonical themes ship in `theme.py`: **Dark** (default, the hero), **Light**, **High Contrast**. A custom-theme system lets users override every token from a live Theme Editor. There is also per-chat *tab color* customization.

- **Dark is the canonical identity.** Window background `#0e1525` (deep navy, never `#000`). Widget background `#1a1a2e` (one step lighter). Input/tab background `#16213e` (a cooler, bluer surface). Primary text `#eeeeee`, secondary `#cccccc`, muted `#999999`.
- **Accent is Twitch purple:** dark `#7b5cbf`, light `#6441a5`. Hover states shift *lighter* (`#9171d6`) rather than desaturating.
- **Platform colors are immutable across themes:** Twitch `#9146FF`, YouTube `#FF0000`, Kick `#53FC18`, Chaturbate `#F47321`. These color the *channel name text* on each row.
- **Status:** Live `#4CAF50` (green dot), Offline `#999999` (grey dot), Error `#f44336`, Info `#2196F3`.
- **Chat accents:** URLs `#58a6ff` (blue), system/sub messages `#be96ff` (pale purple), mention highlight `#33ff8800` (20% orange overlay), hype-train banner purple, raid banner orange.

### Type
- **System font stack only** — Qt renders with the OS default (Cantarell / Noto Sans / Segoe UI). There is **no bundled webfont**. For HTML mockups, use a system-sans fallback and JetBrains Mono (Google Fonts) for the rare monospace moments.
- **Size discipline is tight.** The app is dense: 12–14 px is the normal range. Channel name 14 px bold. Secondary row 12 px regular. Timestamp 11 px. Viewer count 14 px semibold with tabular-nums. Compact UI styles (Compact 1/2/3) scale everything down together.
- **Weight:** Channel names are **bold** (700). Section headers semibold (600). Everything else regular (400). No italics anywhere except *"Replying to @…"* reply-context lines and sub-event banners.
- **Numerics:** `font-variant-numeric: tabular-nums` on viewer counts and timestamps so alignment stays stable as values change.

### Spacing & density
- Spacing scale: **2 / 4 / 6 / 8 / 12 / 16 / 24 / 32** — mostly 4 and 8. This is a dense Qt app, not a modern web dashboard.
- Row height in main window ≈ **48 px** default, **28 px** in Compact 3. Two lines per row (primary + secondary).
- Chat message rows have **no fixed height** — they wrap on content. Alt-row striping is a 5–10% white overlay.
- Toolbar buttons are **28 × 28 px** squares with a 1 px border.
- The app never uses generous whitespace. Everything is flush.

### Backgrounds
**Flat fills, no gradients, no imagery.** Every surface is one of three solid colors (`window_bg` / `widget_bg` / `input_bg`). Banners for hype train and raid are solid accent fills with a subtle 1px border — not gradients. There are no background images, textures, illustrations, blurs, or protection gradients anywhere in the app. The only "image" in the whole app is the 128×128 app icon.

### Borders & separators
- **1 px solid** borders are the primary divider everywhere (`--ll-border: #444` dark, `#ccc` light).
- Group boxes use a 1 px border with 4 px radius and a title label that sits *on* the top edge (native Qt `QGroupBox` rendering).
- Tabs are *connected* to their pane — selected tab shares the pane's background, unselected tabs show the input color, bottom border is removed at the join.
- No double borders, no shadow borders.

### Corner radii
- **3 px** for inner panels
- **4 px** default for inputs, buttons, tabs, chips — **this is the "radius of the app"**
- **6 px** for the app-icon inner window frame
- **24 px** for the app-icon outer rounded square
- No fully pill-shaped buttons. No 8+ px rounded cards.

### Shadows
- **Mostly flat.** Qt native widgets are drawn without shadow in the app window itself.
- **Popouts / menus / tooltips:** a single subtle drop shadow supplied by the OS window manager, not CSS.
- **Dialogs:** modal backdrop + OS shadow.
- No inner shadows. No long/colored shadows. No glow.

### Transparency & blur
- **Row stripe alternation is the only use of transparency**: `#1affffff` = 10% white over the base, `#0dffffff` = ~5%. It's done with rgba so the stripe composites cleanly on top of any background theme.
- Mention highlight is a 20 % orange fill (`#33ff8800`) laid on top of the chat row bg.
- **No backdrop-filter blurs anywhere.** The Qt version does not use Wayland blur.

### Animation
- **None.** Chat auto-scrolls with an instant `scrollToBottom()` (smooth scrolling is on the roadmap, not implemented). Theme swaps are instant — no fade. Row state changes are instant. Tooltips fade in with the default Qt delay (~500 ms) and that's all the motion in the product.
- **For HTML mockups:** keep animations to a minimum. When in doubt, don't animate. If you must, use a 120 ms linear opacity fade — nothing bouncy, nothing spring-based.

### Hover & press states
- **Button hover:** background becomes `--ll-accent-hover` (a *lighter* purple), text turns white. This is `QPushButton:hover` in the stylesheet.
- **Button press:** background becomes `--ll-accent` (the base purple).
- **Disabled:** background becomes `--ll-border` (grey), text becomes `--ll-text-muted`.
- **List/menu hover:** background becomes `--ll-popup-hover` (a blue-grey).
- **Input focus:** border becomes `--ll-accent`.
- **Rows in the stream list do not hover-highlight**; they rely on alt-row striping for readability. Action buttons inside the row hover independently.

### Layout rules
- **Window-level:** toolbar pinned top (28 px tall), status bar pinned bottom (Ready / live count). Everything else is scrollable.
- **Main window is a single list**, not split columns — one row per channel, all states co-exist in the same list (live rows sort to top, offline rows faded at bottom).
- **Chat window** can dock next to main or pop out as a standalone window. Tabs along the top, banner below tabs, message list in middle, composer pinned to bottom.
- **Preferences dialog** uses a horizontal tab strip (General / Playback / Chat / Appearance / Accounts). Each tab scrolls independently. Dialog has a single Close button bottom-right.
- **Context menus and popovers** open at the click site, never animate in.

### Card pattern
There is no "card" in the web sense. The closest equivalent is the **QGroupBox** in Preferences — a 1 px bordered rectangle with a label sitting on the top edge. No radius-above-8 px, no shadow, no hover. That's the card.

### Imagery vibe
- The **app icon** (purple square with a white monitor, play triangle, red dot, and two stacked caption bars) is the only brand illustration. It reads as "recorded video + live indicator".
- Stream preview thumbnails (when shown on hover) are raw Twitch/YouTube/Kick thumbs — no filter, no frame, no overlay.
- Chat emotes render at their source resolution (7TV/BTTV/FFZ — pixel art, WebP, GIF) with no correction applied. The brand lets third-party assets speak in their native language.
- **No stock photography. No illustrations. No gradients pretending to be art.** If you need a picture, use a real screenshot.

---

## ICONOGRAPHY

**There is no icon font and there are almost no icon SVGs.** The entire row-action UI is built from **single letters** in a 20×20 button: `B` (browser), `C` (chat), `★` / `☆` (favorite, filled or outline), `A` (auto-play), `▶` (play). Toolbar: `+` (add), `⟳` (refresh — actually the Qt standard refresh glyph), a solid rectangle (selection mode), an eye (hide offline), `A` (always-on-top). The status LED is a 10 px **filled circle**: green (`#4CAF50`) live, grey (`#999`) offline.

**Platform badges** on each row are single capital letters colored by platform:
- `T` in `#9146FF` → Twitch
- `Y` in `#FF0000` → YouTube
- `K` in `#53FC18` → Kick
- `C` in `#F47321` → Chaturbate

**Assets shipped (in the repo, not duplicated here):**
- [`data/app.livestreamlist.LivestreamListQt.svg`](../../data/app.livestreamlist.LivestreamListQt.svg) — the 128×128 app icon (purple rounded-square, white monitor, red dot).
- [`data/app.livestreamlist.LivestreamListQt-symbolic.svg`](../../data/app.livestreamlist.LivestreamListQt-symbolic.svg) — the stripped-down symbolic variant used for the system tray (Linux symbolic icons are monochrome, filled by the panel theme).

**What to do when you need an icon the app doesn't have:**
1. **First preference — use a single Unicode glyph.** Matches the house style exactly. Examples used in production: `★ ☆ ▶ + ⟳ × ✓ •`.
2. **Second preference — use a single capital letter** color-coded to its meaning (as platform badges do).
3. **Third — Lucide icons from CDN** (`https://unpkg.com/lucide-static@latest/icons/<name>.svg`) at 16 px, 1.5 px stroke, currentColor. Flag this as a substitution when using — the product doesn't ship Lucide, but it's the closest match to the spartan stroke style if something truly needs to be iconographic.
4. **Never draw a custom SVG.** The brand has none, and inventing one dilutes the "plain letters and dots" visual vocabulary.

**Emoji.** Never in app chrome. User-generated chat content can contain any emoji and is rendered at system resolution — don't restyle it.

---

## FONT SUBSTITUTIONS

The Qt app uses **the system default sans** — Cantarell on GNOME, Noto Sans on KDE, Segoe UI on Windows builds, etc. There is no shipped webfont.

For HTML mockups, the web surrogate is **Inter** (400/500/600/700), with the native Qt system stack as the fallback. This is loaded in [`colors_and_type.css`](./colors_and_type.css) and matches the grotesque, UI-oriented feel of the Qt default. **The Qt app itself is not affected** — it continues to render with the OS default sans.

---

## Index — what's in this folder

```
README.md               ← you are here
SKILL.md                ← agent-invocable skill manifest
colors_and_type.css     ← every token: colors, type, spacing, radii
preview/                ← HTML specimen cards for every design concept
```

Canonical source of truth for Qt tokens: [`src/livestream_list/gui/theme.py`](../../src/livestream_list/gui/theme.py).
Reference screenshots: [`docs/screenshots/`](../screenshots/).

