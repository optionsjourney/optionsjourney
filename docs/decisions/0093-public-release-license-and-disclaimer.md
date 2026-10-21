# 0093. Public releases: PolyForm Noncommercial, a trading disclaimer, and a separate public repo

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
The author wants to share OptionsJourney beyond the working repo: a site at optionsjourney.com with a live demo (hosted on Railway) and a link to the code. The app places real orders, so users must clearly accept that they are responsible for them. The author does not want others selling the app. The working repo is private, and its issues hold working notes that should stay private; GitHub can't make issues private on a public repo.

## Decision
- **License:** PolyForm Noncommercial 1.0.0, with `Required Notice: Copyright (c) 2026 <author>`. Free for personal, non-commercial use; nobody else may sell or host it for money. The author keeps all rights, including selling it later.
- **Trading disclaimer**, separate from the license: not financial advice; options carry substantial risk; bots can misbehave; the user is solely responsible for every order, including bot orders; start with paper trading. It appears in `DISCLAIMER.md` (linked near the top of the README), the site footer and install page, a one-time first-run "I understand" screen, an acknowledgment the first time a bot goes Live or a real broker is connected, a footer on every app page, and the demo banner.
- **Two repos:** this repo stays private as the working repo, with its issues and project board. A separate public repo receives a clean snapshot at each release tag. The site, demo and install guide link to the public repo.
- **Contributions:** Discussions are on in the public repo. PRs are reviewed there and applied by hand to the private repo with a `Co-authored-by:` credit, then closed with a note naming the release. CONTRIBUTING.md says so, and that by contributing you grant the author the right to use the contribution under any license. Changes touching the order path, the trading guard or credentials get a line-by-line review.
- **Order:** this cleanup and a first tagged release (v0.1.0) come before the demo site. The demo deploys from the latest release tag.

## Why
- PolyForm Noncommercial lets friends use the app freely while stopping resale; MIT and GPL both allow selling.
- A license disclaims warranty for the code but says nothing about trading risk; an acknowledgment at first run and before Live is where the user actually accepts responsibility.
- A snapshot repo keeps issues and old history private and starts the public history clean.

## Consequences
- The app is "source-available", not open source; some developers may not contribute.
- Outside PRs are never merged directly; each takes a manual apply step.
- Every release needs a publish step to the public repo.
- The wording of the disclaimer is not legal advice; a lawyer's review is advised before a wide public launch.
