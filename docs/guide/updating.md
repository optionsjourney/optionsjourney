# Updating the app

When a newer version is out, the OJ menu tells you. You can also update by hand.

## From the OJ menu
1. Once a day the app checks GitHub for a new version. When there is one, the [OJ menu](menubar.md) shows **Download v0.2.1** (the number will be the new version). Click it. It says **Downloading v0.2.1…** for a moment.
2. When it is ready, the item changes to **Install v0.2.1…**. Click it. The app opens the Install page.
3. The page lists what is live right now. Tick each box to say you know. If a number changes while you are on the page, tick that box again. Last, tick the box that says you understand the app stops for about a minute.
4. Click **Install now**. A window opens and runs the installer. Leave it open until it says it is done. OptionsJourney stops and comes back by itself in about a minute. Then press Return (Enter on Windows) to close the window.

What the warnings mean:
- **The market is open.** You can still install. Just know the app will be off for a minute.
- **Bots armed.** While the app is stopped, they won't enter, manage or exit.
- **Open trades have a stop the app watches, not your broker.** Nothing closes them while the app is stopped, even if the price moves through the stop.
- **Bot orders still working.** The app won't follow them while it is stopped.

If it goes wrong, the installer puts the old version back. The window stays open so you can read what happened. The installer's notes are in the file `update.log`, in the logs folder that **Open logs** in the OJ menu shows.

If the app doesn't start at all after an update was stopped half way (for example, the computer turned off), run the installer again by hand, as below. Use the zip you downloaded before (its **scripts** folder), or download it again from the [Releases page](https://github.com/optionsjourney/optionsjourney/releases/latest).

To stop the daily check, turn off **Check for new versions** in [Settings](settings.md) → This computer.

## By hand
1. Download the new version from the [Releases page](https://github.com/optionsjourney/optionsjourney/releases/latest) and unzip it, just like [installing](installing.md).
2. Open its **scripts** folder and double-click the installer again: **Install OptionsJourney for Mac.command** or **Install OptionsJourney for Windows.bat**.

The installer quits the running app, replaces it with the new version and starts it again. Your trades, bots and settings are kept: they live in a separate data folder. Then delete the downloaded files.

## Going back
Sometimes you want the older version again. Before every update that changes your data, the app makes a backup of it first. So you can go back. The backup goes in your backup folder. If that folder can't be reached (a USB drive that isn't plugged in), it goes in Documents/OptionsJourney Backups instead.

1. Go to the [Releases page](https://github.com/optionsjourney/optionsjourney/releases), pick the older version, and download its zip.
2. Unzip it and run its installer, as in "By hand" above.
3. The older app sees that your data is from a newer version and won't use it. It opens a page that says so, with a button: **Restore the backup from before the upgrade**. Click it. The app restarts on the older data.

Two things to know:
- Anything you changed after the upgrade is lost. That includes tags, notes and bot edits.
- Your trades come back with the next sync.

The oldest version you can go back to is v0.2.0.
