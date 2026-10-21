# 0010. Notes, decisions and docs live in the repo

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
The author wants anyone who downloads the project to be able to see how and why it was built, not just the final code. He also considers good documentation essential for people who aren't programmers.

## Decision
All documentation lives in `docs/` in this repository:

- **`decisions/`:** one file per important decision, with the reasons behind it. Decisions are never rewritten. If one changes, a new file replaces it.
- **`journal.md`:** a dated log of each working session.
- **`roadmap.md`:** the phases of the build and where we are.
- **`guide/`:** how to install and use the app, for non-technical users.
- **`developer/`:** how the code works.

**A feature isn't finished until its documentation is written or updated.**

## Why
- **The why stays visible.** The thought process is useful to anyone who wants to understand, trust or change the app.
- **Docs stay current.** Documentation kept next to the code gets updated along with it.

## Consequences
- **Nothing private goes in the docs:** no account numbers, keys, secrets, home network addresses or personal financial figures. Everything here is public to anyone with the repo.
- **Tracker2's old docs are a warning.** Many of its planning documents went stale, so here the guide and developer docs describe how things work *now*, and history belongs in the journal and decisions.
