# Releasing

Work happens in a private repository. Each release copies a clean snapshot to the public repository, [optionsjourney/optionsjourney](https://github.com/optionsjourney/optionsjourney) (decision 0093).

## Steps
1. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new version heading, and set `version` in `pyproject.toml` to match (the only place it is set: the app reads it from there).
2. Commit, then tag: `git tag v0.2.0`.
3. Build and test the public copy: `uv run python scripts/publish_public.py v0.2.0`. It prints where the copy is, so you can look through it.
4. Publish: `uv run python scripts/publish_public.py v0.2.0 --push`. This commits `Release v0.2.0` to the public repo, tags it and pushes. Then it builds the Release file, `dist/v0.2.0/OptionsJourney.zip` (the public repo at the tag, in one folder `OptionsJourney-v0.2.0/`), and prints the commands for step 5. To build only that file from a tag already in the public repo: `uv run python scripts/publish_public.py v0.2.0 --zip-only`.
5. On GitHub, create the Release from the tag, with `OptionsJourney.zip` attached (decision 0116). Generate its notes first: `uv run python scripts/announce_release.py v0.2.0 --notes > notes.md` prints the version's changelog section, then a **Screenshots** section showing each `docs/guide/images/v0.2.0-*.png` from the public repo at that tag (step 4 copied them: `docs/guide/` is public). Then create it as a draft, upload the file, and publish:
   ```
   gh release create v0.2.0 -R optionsjourney/optionsjourney --draft --title v0.2.0 --notes-file notes.md
   gh release upload v0.2.0 dist/v0.2.0/OptionsJourney.zip -R optionsjourney/optionsjourney
   gh release edit v0.2.0 -R optionsjourney/optionsjourney --draft=false
   ```
   Draft first, because the app's update check ignores drafts: nobody is offered the release before its file is there. The file must be named exactly `OptionsJourney.zip` (gh uploads it under the file's own name; a `#label` only changes the label shown). The app downloads that file when it updates, and the README and the guide link to `https://github.com/optionsjourney/optionsjourney/releases/latest/download/OptionsJourney.zip`, which always gets the newest release's file.
6. Announce it on Discord: `uv run python scripts/announce_release.py v0.2.0` posts the version's changelog section (Added, Fixed, Changed) with a link to the Release, and attaches the version's screenshots, `docs/guide/images/v0.2.0-*.png` in their numbered order (at most 10, Discord's limit). Check it first with `--dry-run`, which prints the message and the images it would attach. The webhook is `DISCORD_RELEASE_WEBHOOK` in `.env`, never in the repo, and the script stays private.

## Download counts
GitHub counts downloads of an uploaded Release file, not of the automatic "Source code (zip)". To see them:

```
gh release view v0.2.0 -R optionsjourney/optionsjourney --json assets -q '.assets[] | "\(.name) \(.downloadCount)"'
```

The count includes downloads from the README and guide links and app updates. Apps on v0.5.0 or earlier still update from the automatic zip, which isn't counted, so app updates only start counting once people run the release after v0.5.0, i.e. from the release after next.

## Screenshots
Take a release's screenshots in demo mode only (`uv run python -m optionsjourney.demo`, see [demo.md](demo.md)): they become public, so made-up data only, never a real account. Save them as `docs/guide/images/vX-<n>-<what>.png` (for example `v0.5.0-1-ideas.png`), numbered in the order they should appear, each under about 1 MB, and commit them before tagging.

## Updates
A published GitHub Release is what the app's daily update check offers (it reads the latest release and ignores drafts and pre-releases). Publishing the Release is what makes people see "Download vX" in their OJ menu. It downloads the Release file `OptionsJourney.zip` (only from `github.com/optionsjourney/optionsjourney/releases/download/<tag>/`, then `release-assets.githubusercontent.com`), or GitHub's source zip for a release without one.

To test an update before the repo is public, set `OJ_UPDATE_URL` to a base URL you serve yourself. The app then reads `<base>/latest.json` instead of GitHub, and downloads only from that same host:
1. Make a folder holding `latest.json` with `tag_name` (for example `v9.9.9`), `html_url` and `zip_url` (or `zipball_url`), plus the release zip that it points to (for example one built with `--zip-only`).
2. Serve it: `python -m http.server 8765` in that folder.
3. Start the app with `OJ_UPDATE_URL=http://127.0.0.1:8765`. The OJ menu offers the update. The installer's notes go to `logs/update.log`.

This switch is for developers only and stays out of the guide.

## What goes public
`scripts/publish_public.py` keeps an allowlist: the standard root files (not `CLAUDE.md`), `src/`, `tests/`, `scripts/`, `docs/guide/`, `docs/developer/`, `docs/decisions/` and `docs/README.md`. This script itself stays private: users don't need it. The roadmap, the working journal and `docs/superpowers/` stay private. Anything new is private until it's added to the allowlist.

In the Markdown it copies, a link to a file that stays private, or to the private repo's issues and project, becomes plain text. A list item that starts with such a link is dropped. The test suite runs inside the copy, and nothing is pushed if it fails.

## Pull requests on the public repo
They are applied here by hand: `gh pr diff <n> -R optionsjourney/optionsjourney | git apply`, committed with a `Co-authored-by:` line, then the pull request is closed with a note naming the release (see `CONTRIBUTING.md`).
