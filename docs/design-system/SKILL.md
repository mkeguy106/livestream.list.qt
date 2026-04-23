---
name: livestream-list-design
description: Use this skill to generate well-branded interfaces and assets for Livestream List, either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.
If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.
If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

Key files:
- `README.md` — voice, content fundamentals, visual foundations, iconography
- `colors_and_type.css` — canonical tokens (Dark + Light themes, platform colors, type scale, spacing, radii)
- `preview/` — small HTML specimen cards for every design concept
- `../../data/app.livestreamlist.LivestreamListQt.svg`, `../../data/app.livestreamlist.LivestreamListQt-symbolic.svg` — the app icon (full-color + tray symbolic)
- `../screenshots/` — reference screenshots from the real Qt app
- `../../src/livestream_list/gui/theme.py` — canonical source of truth for Qt color tokens (this design system is derived from it)
