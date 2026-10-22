# 0095. A new vector logo: an O-ring with a check-mark arrow

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
The logo came from Tracker2 as bitmaps: a symbol of three candles, a curved arrow and a flag, plus "Options" in a thin white outline and "Journey" in green. At favicon and menu-bar sizes (16–32 px) the symbol turned to mush, and the menu bar app drew its own blue "OJ" circle instead (#259).

## Decision
- **The mark:** a green ring (the "O") with a white check mark whose long stroke leaves the ring as an arrow. It's picked from four directions and three O-ring variants, judged at 16, 32 and 64 px.
- **One source:** `web/static/images/logo-mark.svg` (the mark alone, for the nav) and `icon.svg` (the mark on a dark rounded tile). `favicon.ico`, `apple-touch-icon.png` and `icon-192/512.png` are made from `icon.svg`.
- **Wordmark as text:** the nav shows the SVG mark next to "Options" in white and "Journey" in bold green, in the app's own font, instead of an image of the name.
- **Menu bar / tray:** uses the app icon, whose dark tile shows on light and dark menu bars.

## Why
- Vector art stays sharp at every size, and a mark with three strokes still reads at 16 px.
- Green ring plus green "Journey" ties the mark to the name.

## Consequences
- Guide screenshots taken before this change show the old logo until they're retaken.
- Changing the icon means editing `icon.svg` and re-exporting the PNGs and favicon (any SVG renderer, e.g. `rsvg-convert`).
