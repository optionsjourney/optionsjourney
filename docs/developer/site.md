# The website (optionsjourney.com)

A static site: the Home page and four guide pages (Install, Broker setup, Troubleshooting, Disclaimer). It lives in the working repo only; the public copy doesn't include it (decision 0097).

## How it's built
- `scripts/build_site.py` writes plain files to `build/site/`:
  - `index.html` from `site/templates/home.html`;
  - `install.html`, `brokers.html`, `troubleshooting.html` and `disclaimer.html` from `docs/guide/installing.md`, `broker-api-keys.md`, `troubleshooting.md` and `disclaimer.md`, rendered by the same code as the app's Help pages (`web/guide.py`);
  - `404.html`, the CSS from `site/static/`, the guide's screenshots (under `guide/images/`) and the logo and favicon from `src/optionsjourney/web/static/images/`.
- Links between the four guide pages stay on the site (`installing.md` becomes `/install`). Links to any other guide page go to that page on GitHub, at `main`.
- The text on those four pages is the guide's own, so it's written once. The only site-only text is the short disclaimer in the footer and at the top of Install.
- The build stops if one of the four guide pages is missing, so the last good version stays up.

## Trying it locally
```bash
uv run python scripts/build_site.py
python3 -m http.server 8765 --directory build/site
```
Open http://localhost:8765/install.html. The local server needs the `.html`; on the prod Mac, Caddy serves `/install` too.

Tests: `uv run pytest tests/scripts/test_build_site.py`. They check that every page builds, every link and image on the site resolves, the disclaimer is on every page, and no page mentions the app's predecessor by name.

## How it's deployed
The site runs on the always-on prod Mac, behind its Cloudflare tunnel (decision 0101). Two LaunchAgents, written by `site install`:
- `com.optionsjourney.site`: Caddy (`site/Caddyfile`: clean URLs, the 404 page, gzip, www to the apex) on `127.0.0.1:8090`, serving `~/.optionsjourney-site/current`. Only the tunnel reaches it.
- `com.optionsjourney.site-update`: every 5 minutes, `site update` fetches `origin/main` over a read-only deploy key. If main moved, it builds into `~/.optionsjourney-site/builds/<commit>/` and switches `current` to it. A failed build leaves the last good site up. The last three builds are kept.

Both run at login (the prod Mac logs in by itself after a reboot), and Caddy is restarted if it stops. Logs are in `~/Library/Logs/optionsjourney-site/`.

Because the site builds from `main`, a guide change shows on the site before it's in a release. Releases follow guide changes closely, so this is accepted (decision 0097).

## Day to day (on the prod Mac)
`site status`, `site stop`, `site start`, `site restart`, `site update` (build now instead of waiting), `site logs`. `site stop` keeps it stopped through a reboot until `site start`. The 🌐 menu bar item (SwiftBar) shows whether the site answers and has the same buttons.

## One-time setup (the author)
On the prod Mac:
1. `brew install caddy uv`
2. A read-only deploy key: `ssh-keygen -t ed25519 -N "" -f ~/.ssh/optionsjourney_site_deploy`, and in `~/.ssh/config` a `Host github-optionsjourney` entry (`HostName github.com`, `User git`, that `IdentityFile`, `IdentitiesOnly yes`). Add the public key to this repo's deploy keys, read-only.
3. `git clone git@github-optionsjourney:jtse9088/optionsjourney.git ~/Optionsjourney/optionsjourney-site`
4. `~/Optionsjourney/optionsjourney-site/site/deploy/site install`. It writes the LaunchAgents, builds, starts Caddy, puts `site` on the PATH and adds the menu bar item. `site status` should show both on and the local check answering 200.

Then the tunnel, outside market hours (restarting cloudflared drops the trading app's remote access for a few seconds):

5. In the prod tunnel's `~/.cloudflared/config.yml`, above the final 404 rule, add `optionsjourney.com` and `www.optionsjourney.com`, both to `http://localhost:8090`. Restart cloudflared and check `cloudflared tunnel info` shows connections.
6. Point DNS at the prod tunnel with `cloudflared tunnel route dns` for both hostnames, the same way as the trading app's hostname (its runbook has the exact command, including the empty `--config`). Remove any old Railway records first.
   On 2026-10-22 the apex record was created and shown as Proxied, but Cloudflare answered only IPv6 (AAAA) for it, no IPv4 (A), so most visitors couldn't reach it; `www` was fine. Saving the apex record again in the dashboard (adding a comment, since Save needs a change) made the A records appear within a minute. Check with `dig +short A optionsjourney.com @1.1.1.1`.
7. On launch day, the same day the public repo is made public: a Cloudflare redirect rule from optionjourney.com (no s) to https://optionsjourney.com, every path, 301.
