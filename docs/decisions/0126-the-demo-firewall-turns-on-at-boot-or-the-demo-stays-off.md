# 0126. The demo's firewall turns itself on at boot, and the demo stays off without it

- **Status:** Accepted
- **Date:** 2026-11-02
- **Replaces:** 0103's consequence "after a restart the admin runs `sudo pfctl -E`". The rest of 0103 stands: the rules, the admin adding them to `/etc/pf.conf` once, `firewall-check`.

## Context
Decision 0103 fences the demo user `ojdemo` off from the Mac's other services with pf rules, the trading app's port included. macOS loads `/etc/pf.conf` at boot but leaves pf off until something turns it on, so after every restart the fence was down until the admin noticed `firewall: OFF` and ran `sudo pfctl -E`. The prod Mac restarts and logs in by itself (power cuts, macOS updates), and the demo starts at boot without a login, so after a restart the internet-facing demo could reach the trading app until someone looked. A macOS update can also rewrite `/etc/pf.conf` and drop the two lines that load the demo's rules, which turning pf on wouldn't notice. Issue #323.

## Decision
- **A boot job, as root.** `oj-demo install` adds a fourth LaunchDaemon, `com.optionsjourney.demo-firewall`: once at each boot, as root, it runs the root-owned copy of `oj-demo` (`firewall-boot`). If `/etc/pf.conf` still loads the demo's rules, it loads the file and turns pf on (`pfctl -f`, `pfctl -E`), as the admin's own step does.
- **Pass means pf on with the demo's rules loaded** (the same check as `oj-demo status`), not just pf on.
- **Fail closed.** If the check fails, the job stops the demo: Caddy first (the only part the tunnel reaches), then the seat manager and the update job. They stay stopped through later reboots until the admin fixes the firewall and runs `oj-demo start`.
- **`oj-demo start` refuses while the firewall is off**, so the menu bar's Start can't bring the demo up unfenced.
- The job runs only root-owned files, under the same privilege rule as the rest of `oj-demo`. Its log is root's. It isn't in the sudoers rule.

## Why
- The fence matters most right after an unattended restart, which is when no one is watching the menu bar.
- Stopping the demo is safe: a visitor sees the demo isn't available. An unfenced demo could reach the trading app.
- `install` still doesn't edit `/etc/pf.conf`. The admin adds the two lines once (0103), and the boot job only repeats the admin's own "load and turn on" step.
- Turned down: having the proxy wait for the check before it starts. launchd has no simple "start after" between daemons, and a wrapper would need root to read pf's state inside a daemon that runs as `ojdemo`. Turned down too: stopping with `bootout` alone so the next boot retries. A failed check usually means `/etc/pf.conf` needs fixing, so the demo should stay off until a person has looked.

## Consequences
- At boot the demo's daemons and the boot job start independently, so the demo may answer for a few seconds before the job finishes. If the check passes, nothing changes. If it fails, the demo goes down a moment later.
- After a failed boot check, `oj-demo status` shows the demo stopped ("stays stopped after a reboot") and `firewall: OFF`. The fix is to put the two lines back in `/etc/pf.conf`, run `sudo pfctl -f /etc/pf.conf && sudo pfctl -E`, then `sudo oj-demo firewall-check <port>` and `oj-demo start`.
- On a first install, before the admin has done 0103's steps, the boot job stops the demo right away. `install` lists `oj-demo start` as the last step.
