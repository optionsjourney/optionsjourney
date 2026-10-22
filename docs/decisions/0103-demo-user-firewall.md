# 0103. The demo user is fenced off from the Mac's other services, and `oj-demo` lives where only root can change it

- **Status:** Accepted
- **Date:** 2026-10-22

## Context
Decision 0102 runs the public demo on the always-on prod Mac as the standard user `ojdemo`, which faces the internet: a visitor who found a hole in the app would run code as `ojdemo`. Closing the trading account's home folder (0101) keeps `ojdemo` out of its files, but not out of its network services. Anything listening on `127.0.0.1` answers every local user, and the author's own trading app listens there. A web app on loopback usually trusts whoever reaches it, so `ojdemo` reaching it would be one step from the real accounts.

Separately, 0102 put the root-owned copy of `oj-demo`, which a sudoers rule lets the admin run without a password, in `/usr/local/bin`. On many Macs an admin account or Homebrew can write there, so anything running as the admin could swap the file and get root without a password.

## Decision
- **pf rules for `ojdemo` alone.** `oj-demo install` writes `/etc/pf.anchors/optionsjourney-demo`. For connections `ojdemo` makes: on this computer, only the demo's own ports (Caddy 8095, the seat manager 8096, the seats 5101–5110); nothing at this computer's other addresses or on private and link-local ranges (the home network, 10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10, fc00::/7, fe80::/10), except DNS to the resolvers the computer had at install (`scutil --dns`, often the home router); elsewhere, only DNS (53), ssh (22) and https (443), which the update job's `git fetch` and `uv` need. Every other connection is refused. Replies to connections made to `ojdemo` (the tunnel reaching Caddy) aren't new connections, so they pass.
- **The admin turns pf on, once.** `install` doesn't enable pf or edit `/etc/pf.conf`, which other parts of macOS use too; it prints the two lines to add and the commands to load and enable it. `oj-demo firewall-check <port>` checks the result: pf on with the rules loaded, `ojdemo` reaching the seat manager, and `ojdemo` refused at `<port>` (the trading app's): only curl's "couldn't connect" counts as refused; any other failure is reported as not blocked or unclear. Root only asks the system whether something listens there; the connections are made as `ojdemo`. `oj-demo status`, and so the menu bar, shows whether the firewall is on.
- **`oj-demo` is installed to `/usr/local/libexec/optionsjourney-demo/`**, made root's by `install`, which refuses if that folder or any above it belongs to anyone but root, can be written by others, or has an access list allowing anyone anything. The sudoers rule and the menu bar item name that path.

## Why
- A rule per user is the narrowest fence: the trading app, the site and everything else on the Mac keep working as before, and only `ojdemo` is held to the few places the demo needs.
- Leaving pf's on switch to the admin keeps `install` from changing how the whole Mac's firewall behaves; the check makes a missed step visible instead.
- Turned down: moving the trading app off loopback or adding a password to it (it's the author's app, outside this repo); a separate Mac or a virtual machine for the demo (0101 chose this Mac to avoid more machines); putting `ojdemo` in a sandbox profile (`sandbox-exec` is deprecated and wouldn't cover Caddy and the update job as simply).

## Consequences
- After a restart macOS leaves pf off until something turns it on, so the admin runs `sudo pfctl -E` (and `firewall-check`) after each restart; `oj-demo status` says `firewall: OFF` until then.
- `oj-demo` isn't on the PATH any more; the admin uses the full path or a shell alias.
- New ports for the demo (another seat range, another service) need the anchor file changed and `install` run again; so does a change of DNS servers, if `ojdemo`'s own DNS lookups matter (the system's lookups for git, curl and uv go through macOS's resolver, which doesn't run as `ojdemo`). This computer's own addresses are read when the rules load.
