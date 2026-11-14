# 0127. A release runs security checks first, and a failed check stops it

- **Status:** Accepted
- **Date:** 2026-11-02
- **Adds to:** 0093 (the public copy) and 0116 (the Release file).

## Context
The public demo runs the public repo's `main` and updates itself within about 5 minutes (0102), so each release reaches the internet without anyone checking it first. Friends' apps follow the Releases too. Before this, nothing checked a release: there was no CI, no dependency audit and no scan for secrets. The first audit found 41 known holes in two locked packages: Pillow 12.3.0 (fixed in 13.0.0) and certifi (fixed in 2026.10.18). Pillow only opens the app's own menu bar icon, so a visitor couldn't reach it, but nothing would have caught a package that a visitor could. Issue #324.

## Decision
`scripts/publish_public.py` checks the public copy after building it and before the tests, and stops on any failure:
- **Secrets:** files that look like keys or `.env` files, and lines that look like tokens (GitHub, AWS, Google, Slack, Anthropic, OpenAI, private keys, real-length Discord webhooks). Only the start of a match is printed.
- **Known holes in what ships:** `uv export --frozen --no-dev` lists the lock's runtime packages, and `pip-audit` checks them against the advisory databases. A network or database error fails the release too: a check that couldn't run isn't a pass.
- **Risky code:** `bandit` on `src/`, medium severity and confidence and up. Each finding judged safe gets a `# nosec <id>` with its reason on the same line, before the marker.
- `pip-audit` and `bandit` are dev dependencies, run with `uv run --frozen` inside the copy, as the tests are. There's no flag to skip the checks.

## Why
- A check at release time catches problems before they go public, and the demo picks up the public `main` only from releases.
- Auditing the lock's runtime packages checks what friends and the demo actually install, not the tools on the author's computer.
- Bandit's low-severity findings (mostly `subprocess` calls with fixed arguments) would bury the real ones, and a check that always fails gets skipped. Medium and up found three findings, all judged safe and marked.
- Turned down: gitleaks (another tool to install; a short pattern list covers the keys this project could hold) and a CI service (the private repo has none, and the check belongs where the copy is made).

## Consequences
- A release needs network access for the advisory databases.
- A new advisory against a locked package blocks the next release until the package is upgraded (`uv lock --upgrade-package <name>`) or the advisory is shown not to apply.
- Dependabot alerts and CodeQL on the public repo are separate GitHub settings. Each release replaces the public repo's files, so a `.github/` folder committed there directly would be lost.
