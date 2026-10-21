# Releasing

Work happens in a private repository. Each release copies a clean snapshot to the public repository, [optionsjourney/optionsjourney](https://github.com/optionsjourney/optionsjourney) (decision 0093).

## Steps
1. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new version heading, and set `version` in `pyproject.toml` to match.
2. Commit, then tag: `git tag v0.2.0`.
3. Build and test the public copy: `uv run python scripts/publish_public.py v0.2.0`. It prints where the copy is, so you can look through it.
4. Publish: `uv run python scripts/publish_public.py v0.2.0 --push`. This commits `Release v0.2.0` to the public repo, tags it and pushes.
5. On GitHub, create the Release from the tag (`gh release create v0.2.0 -R optionsjourney/optionsjourney --notes-file <notes>`), using the changelog section as the notes. The Release's zip is what the README tells people to download.

## What goes public
`scripts/publish_public.py` keeps an allowlist: the standard root files (not `CLAUDE.md`), `src/`, `tests/`, `scripts/`, `docs/guide/`, `docs/developer/`, `docs/decisions/` and `docs/README.md`. The roadmap, the working journal and `docs/superpowers/` stay private. Anything new is private until it's added to the allowlist.

In the Markdown it copies, a link to a file that stays private, or to the private repo's issues and project, becomes plain text. A list item that starts with such a link is dropped. The test suite runs inside the copy, and nothing is pushed if it fails.

## Pull requests on the public repo
They are applied here by hand: `gh pr diff <n> -R optionsjourney/optionsjourney | git apply`, committed with a `Co-authored-by:` line, then the pull request is closed with a note naming the release (see `CONTRIBUTING.md`).
