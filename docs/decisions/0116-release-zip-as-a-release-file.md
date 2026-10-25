# 0116. Each release carries its zip as a Release file, OptionsJourney.zip

- **Status:** Accepted
- **Date:** 2026-10-24

Issue #299.

## Context
People installed and updated from GitHub's automatic **Source code (zip)**. GitHub doesn't count downloads of that zip, so there was no way to know how many people download a release or update to it. A file uploaded to a Release does have a download count.

## Decision
- **Every Release after v0.5.0 carries an uploaded file named exactly `OptionsJourney.zip`:** the public repo at the tag, in one folder `OptionsJourney-<tag>/`. `scripts/publish_public.py` builds it after `--push` (or from an existing public tag with `--zip-only`) as `dist/<tag>/OptionsJourney.zip` and prints the `gh` commands to upload it. The upload stays a step done by hand ([releasing.md](../developer/releasing.md)).
- **The Release is created as a draft, the file uploaded, then the Release published.** The app's update check ignores drafts, so nobody is offered a release before its file is there.
- **The app's update check downloads that file** when the release has one: only a file named `OptionsJourney.zip` whose address starts with `https://github.com/optionsjourney/optionsjourney/releases/download/<tag>/`. Otherwise it uses the automatic source zip, as before. Release file downloads redirect to `release-assets.githubusercontent.com`, which is added to the hosts the download may go to (https, port 443 only).
- **The README and the guide link to** `https://github.com/optionsjourney/optionsjourney/releases/latest/download/OptionsJourney.zip`: the stable name means that link always gets the newest version.

## Why
- Download counts show whether people use it and update, without the app sending anything about the user.
- A stable name gives non-technical users one direct download link instead of a Releases page to find their way around.
- The name and address checks keep the update download as narrow as before: only this repo's file for this tag.

## Consequences
- See the counts with `gh release view <tag> -R optionsjourney/optionsjourney --json assets`.
- Apps on v0.5.0 or earlier still update from the automatic zip (not counted); app updates start counting once people run a release that has this check, i.e. from the release after next.
- A release published without the file still works (the app falls back to the source zip), but the README's direct link would then fail until the file is uploaded.
