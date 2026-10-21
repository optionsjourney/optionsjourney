# 0094. The installer puts the app in a fixed folder; logs go to a file

- **Status:** Accepted
- **Date:** 2026-10-21
- **Amends:** 0007 (where the installer leaves the app), 0008 (the menu bar app's items)

## Context
Testing v0.1.0 on a fresh Windows 11 machine (#253) showed:
- The app ran from wherever the zip was unzipped, usually Downloads, so tidying Downloads would delete it, and every version left another copy.
- The app's console window had to stay open: closing it stopped the app.
- An error showed a bare "Internal Server Error", and the log went only to that console, so a user had nothing to send.
- The menu bar app never started auto-sync or fill sync; only `uv run optionsjourney` did.
The author first suggested Documents, but OneDrive (Windows) and iCloud (Mac) often sync Documents, and syncing or offloading the thousands of files in `.venv` breaks the app.

## Decision
- **Install location:** `%LOCALAPPDATA%\Programs\OptionsJourney` on Windows (where per-user apps like VS Code go), with a Start menu entry; `~/Applications/OptionsJourney` on a Mac, with an `OptionsJourney.app` next to it for Spotlight and Launchpad. The installer copies the unzipped folder there (`robocopy /MIR`, `rsync --delete`, keeping `.venv`), so running a newer installer is the update. It stops a running copy first and points an existing Start at login entry at the new copy. `OJ_INSTALL_DIR` overrides the folder for testing.
- **No console window on Windows:** the launcher, Start menu entry, Start at login and Restart all use `pythonw.exe`.
- **Logs:** `optionsjourney/logs.py` adds `logs/optionsjourney.log` in the data folder (1 MB, 3 older files kept), for both ways of starting.
- **Menu:** Show app in Finder/Explorer, Open logs, and Restart OptionsJourney (quit, free the port, start a fresh copy).
- **Error page:** an unexpected error shows a standalone page saying the data is safe and what to do (try again, Restart, send the log to hello@optionsjourney.com); `/api/` gets JSON.
- **Background jobs:** `services/background.start_all` is the one list both ways of starting use.

## Why
- A fixed folder outside synced folders is what installed apps use; the data folder was already separate, so updates can replace the app freely.
- A user can only report what they can find: a log file and a menu item to open it.

## Consequences
- The downloaded folder is only an installer and can be deleted.
- An older copy unzipped elsewhere still has its own `.venv` until the user deletes it.
- The installers aren't covered by the test suite; they're checked on fresh machines (#253).
