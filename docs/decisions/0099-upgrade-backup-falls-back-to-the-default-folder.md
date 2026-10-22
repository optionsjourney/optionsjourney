# 0099. A backup before an upgrade falls back to the default folder

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
Before upgrading the data, the app makes a backup ([0098](0098-checking-for-updates.md)) in the backup folder chosen in Settings → Backups. That folder can be on a USB drive or in a cloud folder that isn't there. Then the backup fails, the data isn't upgraded and the app can't start, on every launch, and the page can't send people to Settings: the app that has Settings didn't start.

## Decision
- If the backup before an upgrade can't be made in the chosen folder, it is made in the default folder, Documents/OptionsJourney Backups. The log names both folders.
- Only if both fail is the upgrade refused. The page then asks people to make free space on the computer and restart from the OJ menu.
- Going back looks for the backup from before the upgrade in both folders.

## Why
- An unplugged drive shouldn't stop the app. The default folder is on the computer itself, so it is almost always usable.
- The backup still exists before the upgrade, which is what makes going back possible.

## Consequences
- A backup can be in a folder people didn't choose. The log says where.
- Scheduled backups still go only to the chosen folder, and say so in Settings when they fail.
