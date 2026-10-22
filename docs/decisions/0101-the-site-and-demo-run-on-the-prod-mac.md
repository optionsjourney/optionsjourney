# 0101. The site, and later the demo, run on the prod Mac behind its Cloudflare tunnel

- **Status:** Accepted
- **Date:** 2026-10-22

## Context
Decision 0097 deployed optionsjourney.com on Railway, and the demo's design planned a second Railway service. On 2026-10-22 Railway paused all builds for a platform outage, so the site never went live. The author already runs an always-on Mac for their own trading app, with its own Cloudflare tunnel, auto-login after a reboot, and room to spare (10 cores, 32 GB, mostly idle). The author's conditions: the site can be stopped and started easily, and it comes back by itself when the Mac restarts (#252).

## Decision
- **The site is served from the prod Mac.** Caddy runs as a LaunchAgent on `127.0.0.1:8090`, so the site is reached only through the tunnel, which carries `optionsjourney.com` and `www.optionsjourney.com`. Caddy sends www to the apex.
- **It still deploys on push to main.** A second LaunchAgent checks `origin/main` every 5 minutes over a read-only deploy key. When main has moved, it builds with `scripts/build_site.py` into a new folder and points Caddy's `current` link at it. A failed build leaves the last good site up.
- **One command runs it:** `site install | start | stop | restart | status | update | logs` (`site/deploy/site`), and a menu bar item with the same buttons. `stop` disables the LaunchAgents, so the site stays stopped through a reboot until `start`.
- **The demo goes there too**, when parts B and C are built, in place of a Railway service: under its own macOS user with no access to the trading account's files, as a LaunchDaemon so it starts at boot without a login, and at low CPU priority so the trading app comes first. The 10-seat cap stays.
- Everything else in 0097 stands: a static site, built from the guide on main, and private to the working repo.

## Why
- Railway's outage blocked the launch; the prod Mac is already up, already tunnelled and already watched by Healthchecks.
- A pull every 5 minutes keeps push-to-deploy without a CI workflow, a token, or an inbound port.
- Binding to 127.0.0.1 keeps the site off the home network.
- Turned down: Cloudflare Pages. It suits a static site, but builds would need a CI step with a token (the build needs uv and Python 3.14), and it can't host the demo.

## Consequences
- If the house loses power or internet, the site and demo go down with the trading app's remote access. Cloudflare's cache covers short blips for the static pages.
- The demo is a full app open to the internet, so before it goes on this Mac the trading account's home folder must be closed to other users and the demo user checked for access to it.
- Adding a hostname means editing the prod tunnel's config and restarting cloudflared, which drops the trading app's remote access for a few seconds. Do it outside market hours.
- `site/Dockerfile`, `site/railway.toml` and the Railway service are gone.
