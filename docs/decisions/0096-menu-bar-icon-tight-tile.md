# 0096. The menu bar icon: the logo on a tile cut close around it

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
Decision 0095 made the menu bar / tray icon the app icon scaled down. The app icon's dark tile has wide margins, so at menu-bar size (22 points on a Mac, 16 to 32 pixels on Windows) the mark was only about 60% of the icon and looked small (#226).

## Decision
- The menu bar / tray uses its own `web/static/images/tray-icon.svg`: the same mark, in the same colours, on a dark tile cut close around it, so the mark fills about 90% of the icon. `tray-icon.png` (64 × 64) is made from it.
- The app icon (`icon.svg`, the favicon and the PNG icons) is unchanged.

## Why
- The logo itself stays exactly as it is, white check included. The tile keeps it visible on light and dark menu bars.
- Compared with an all-green mark with no tile, which is more like other menu bar icons but changes the logo's colours, the author chose the true logo.

## Consequences
- Changing the mark means editing `tray-icon.svg` as well as `icon.svg` and `logo-mark.svg`, and re-exporting `tray-icon.png`.
