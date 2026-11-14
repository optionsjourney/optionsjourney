# 0128. The site runs on the Linux prod box; the demo is off until it moves there

- **Status:** Accepted
- **Date:** 2026-11-10

## Context
Decision 0101 put the site (and the demo) on the prod Mac behind the trading app's Cloudflare tunnel. On 2026-11-10 the trading app's prod moves to a Dell laptop running Ubuntu 26.04: Linux suits an always-on server better (no lid-closed sleep, systemd supervision), and the Mac goes back to being a spare. The tunnel moves with the trading app, so whatever the tunnel carries has to run on the Dell.

## Decision
- **The site moves to the Dell, unchanged in shape.** Caddy on `127.0.0.1:8090`, reached only through the tunnel; the 5-minute pull-and-build from `origin/main` over a read-only deploy key; the `current` link Caddy serves.
- **On Linux, `site` drives systemd user units** (lingering on, so they start at boot without a login): `optionsjourney-site.service` (Caddy) and `optionsjourney-site-update.timer` (the updater). `site stop` disables them, so a stop survives a reboot, as on the Mac. `site install` writes the units; logs go to the journal (`site logs`). macOS keeps its LaunchAgents.
- **The demo is off from the switch until it moves to the Dell.** Its fence (the `ojdemo` user, pf rules, root-owned `oj-demo`, decisions 0103 and 0126) is macOS-specific; it gets a Linux equivalent (its own user, nftables rules, a root-owned launcher) and the same checks before it is exposed again. Until then `demo.optionsjourney.com` answers with an error.
- Everything else in 0101 stands.

## Why
- One tunnel, one machine: two machines running the same tunnel split its traffic.
- Rushing the demo's fence onto a new OS would expose a full app to the internet with an untested firewall; a few days without the demo costs less.

## Consequences
- The demo needs its Linux port before it can come back (users, firewall, boot job, checks).
- The Mac's LaunchAgents and LaunchDaemons for the site and demo are disabled at the switch, not deleted, so the Mac can take them back if the Dell fails in the first days.
