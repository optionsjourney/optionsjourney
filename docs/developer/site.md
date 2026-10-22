# The website (optionsjourney.com)

A static site: the Home page and three guide pages (Install, Troubleshooting, Disclaimer). It lives in the working repo only; the public copy doesn't include it (decision 0097).

## How it's built
- `scripts/build_site.py` writes plain files to `build/site/`:
  - `index.html` from `site/templates/home.html`;
  - `install.html`, `troubleshooting.html` and `disclaimer.html` from `docs/guide/installing.md`, `troubleshooting.md` and `disclaimer.md`, rendered by the same code as the app's Help pages (`web/guide.py`);
  - `404.html`, the CSS from `site/static/`, the guide's screenshots (under `guide/images/`) and the logo and favicon from `src/optionsjourney/web/static/images/`.
- Links between the three guide pages stay on the site (`installing.md` becomes `/install`). Links to any other guide page go to that page on GitHub, at `main`.
- The text on those three pages is the guide's own, so it's written once. The only site-only text is the short disclaimer in the footer and at the top of Install.
- The build stops if one of the three guide pages is missing, so Railway keeps the last good version.
- The build never runs git, because Railway's build has no `.git` folder.

## Trying it locally
```bash
uv run python scripts/build_site.py
python3 -m http.server 8765 --directory build/site
```
Open http://localhost:8765/install.html. The local server needs the `.html`; on Railway, Caddy serves `/install` too.

Tests: `uv run pytest tests/scripts/test_build_site.py`. They check that every page builds, every link and image on the site resolves, the disclaimer is on every page, and no page mentions the app's predecessor by name.

## How it's deployed
Railway redeploys the `site` service on each push to `main` that touches one of the paths in `site/railway.toml`: the site itself, the build script, the guide, the guide renderer or the app's images. `site/Dockerfile` runs the build script in a uv image, then serves `build/site/` with Caddy (`site/Caddyfile`: clean URLs, the 404 page, gzip).

Because the site builds from `main`, a guide change shows on the site before it's in a release. Releases follow guide changes closely, so this is accepted (decision 0097).

## One-time setup (the author)
1. In Railway, add a service named `site` from the GitHub repo (branch `main`), and set its **Config file path** to `/site/railway.toml`.
2. Wait for the first deploy, then open the Railway URL.
3. In Railway, add the custom domains `optionsjourney.com` and `www.optionsjourney.com`.
4. In Cloudflare, add `@` (flattened CNAME) and `www` pointing to Railway's targets. Keep them DNS-only (grey cloud), or use SSL Full (strict) if proxied; otherwise redirect loops or failed certificates.
5. On launch day, the same day the public repo is made public: a Cloudflare redirect rule from optionjourney.com (no s) to https://optionsjourney.com, every path, 301.
