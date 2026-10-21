# 0088. Help inside the app renders the guide

- **Status:** Accepted
- **Date:** 2026-10-20

## Context
The plain-language guide in `docs/guide` is written for people who aren't programmers, but nothing in the app points to it. You had to know it was on GitHub, and the repo is private. Tracker2 has a Docs link in its nav. Issue #231.

## Decision
- `/help` renders `docs/guide` with markdown-it-py, and a **Help** link in the nav opens it. The side list shows every page; `/help/<page>` shows one.
- Headings get GitHub's anchors, so a link such as `#the-ticket` works on GitHub and in the app.
- Links that leave the guide (to the roadmap, decisions or code) become plain text while the repo is private. Links to other guide pages become `/help/<page>`.
- The guide is bundled in the wheel as `optionsjourney/guide`. From a source checkout the app reads `docs/guide` directly.
- Raw HTML in the guide is not passed through.

## Why
- One source for GitHub and the app: no second copy to keep in step.
- It's what Tracker2 does, and it lets a tip link to the longer explanation ("Learn more").

## Consequences
- A new dependency (markdown-it-py and its anchors plugin).
- Link tests now guard the guide: every link between guide pages, every `#anchor` and every "Learn more" link in a template or script must point at a page and heading that exist. Renaming a heading means fixing the links to it.
