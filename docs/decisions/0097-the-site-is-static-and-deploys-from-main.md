# 0097. The website is static, built from the guide on main, and deployed by Railway on push

- **Status:** Accepted, deployment replaced by 0101
- **Date:** 2026-10-21

## Context
OptionsJourney needs a public site at optionsjourney.com: what the app does, how to install it, and what to try when it won't open, since the in-app Help can't be reached then (#252, design in `docs/superpowers/specs/2026-10-21-optionsjourney-site-design.md`). The author deploys by pushing to the repo and letting Railway redeploy.

## Decision
- A static site: Home, Install, Troubleshooting and Disclaimer. No server code per request, no analytics, cookies or forms.
- The three guide pages are rendered from `docs/guide/` by the app's own `web/guide.py`, so their text is written once. Links to other guide pages go to GitHub.
- `scripts/build_site.py` builds it; `site/Dockerfile` runs that build on Railway and serves the result with Caddy. Railway redeploys on each push to `main` that touches the paths in `site/railway.toml`.
- The guide pages come from `main`, not from the latest release tag.
- `site/`, the build script and its test stay in the working repo only (`publish_public.py`'s private files).

## Why
- Push-to-deploy is how the author already works, with nothing extra to set up (no CI workflow or deploy token).
- Railway's build has no git history, so building from a release tag would need a separate CI step.
- Reusing the guide renderer keeps one copy of the install and troubleshooting text, shared with the app's Help.

## Consequences
- Between a guide change on `main` and the next release, the site can describe the next version's installer. Releases follow guide changes closely, so this is accepted. Hold back an installer-steps change if a release isn't near.
- The hero shows no version number, since `main` can be ahead of the release.
- The demo, when demo mode exists, is a separate Railway service and deploys from the latest release tag (decision 0093).
