# Help and tooltips

How the in-app Help pages and the ⓘ tips work. Decision: [0088](../decisions/0088-help-inside-the-app-renders-the-guide.md).

## Adding a tip in a template
Import the macro and put it beside the label:

```jinja
{% from '_tooltip.html' import info %}
Backup folder {{ info('settings-backup-folder', 'What the app does with it.', 'Backup folder') }}
```

- The first argument is the id, `<page>-<thing>` in kebab-case. The second is the text, the third is what the screen reader says it is about.
- To link to the longer explanation, add `more='/help/<page>#<heading-anchor>'`. The link test fails if the page or heading doesn't exist.
- For a button or other control, wrap both in `<span class="tip">`, give the control `aria-describedby="tip-<id>"` and add `{{ tip_text('<id>', '...') }}`.
- Settings and Setup fields take their text from `tip=` in `core/settings.py`.
- **An id must be unique on the page.** A tip inside a loop adds the loop's key to its id: `info('run-stop-off-' ~ side.position, ...)` with `aria-describedby="tip-run-stop-off-{{ side.position }}"`. The chain's heading tips, shown on the call and the put side, are `trade-call-<key>` and `trade-put-<key>`.
- **Learn more opens a new tab** (`target="_blank" rel="noopener"`), so the screen the person was on keeps its place. Both the macro and `tipHtml` write it.
- A tip used in two templates goes in its own macro file, as `_missing_exits_tip.html`.

## Adding a tip in JavaScript
Screens built in JavaScript use `tipHtml(id, text, about, { more, end })` from `static/js/tip.js`. It gives the same markup as the macro. Keep each screen's tip text in its own tips module next to the screen, not inside the render code. Hover, focus and tap behaviour is in `static/js/tooltips.js` and the styles are in `static/css/tooltips.css`.

A screen that rebuilds its markup on a timer or a quote push skips the rebuild while `tipEngaged(root)` is true, so a tip the person is reading isn't torn down (the Trade strip; `accounts.js` waits the same way). "Engaged" means shown: `TIP_SHOWN` in `tip.js`, the same states as the CSS display rule (`.tip--open`, `:hover`, `.tip--hover`, `:has(:focus-visible)`). Not `:focus-within`: an ⓘ clicked closed keeps the focus, and the screen would stop updating.

## How a tip is placed
`tooltips.css` puts a tip's text above its ⓘ with `position: absolute`. That is only the fallback for when the script hasn't run. `tooltips.js` (a module, loaded by `base.html`, `tracker_base.html` and `chart_bare_base.html`) places every shown tip itself:

- When a tip shows (`mouseover`, `focusin`, a tap on its ⓘ), it is measured hidden at 0,0, then given `tip__text--placed`: `position: fixed`, with the left and top from `placeTip(anchor, size, viewport, { align })` in `tip.js`. That is above the anchor when it fits, else below (`tip__text--below`), lined up by `tip--start`, `tip--end` or the middle, and kept 8px inside the screen. Its maximum width is `min(18rem, 100vw - 16px)` (26rem and 34rem on the bot form).
- Because it is fixed, a scroll box, a drawer or a table's `overflow` can't cut it off, so no screen needs a workaround for its tips.
- A scroll (caught in the capture phase, so inner scroll boxes count) or a resize moves shown tips with their anchors; a tip whose anchor has left the screen closes.
- Hover: `tip--hover` shows the tip and stays 250ms after the pointer leaves, and a shown tip takes the pointer (an invisible bridge covers the gap), so a button's Learn more can be clicked.

**The containing-block caveat.** An ancestor with `transform`, `filter`, `perspective`, `backdrop-filter`, `contain: paint` or `will-change: transform` makes `position: fixed` relative to itself, not the screen. `tooltips.js` measures where 0,0 lands and takes that off, so the position is still right, but such an ancestor with `overflow` hidden or scrolling would clip the tip again. Checked: the drawers (`.drawer__panel`) only have a transform while they slide (`translate-x-full`), the Charts indicators drawer only during its 0.14s opening animation, and the risk graph drawer is an iframe whose page (`tracker_base.html`) loads `tooltips.js` and places its tips inside the frame. Don't add a lasting transform or filter around tips.

## Where Help comes from
`web/routes/help_pages.py` serves `/help`, `/help/<page>` and `/help/images/<file>`. `web/guide.py` finds the guide folder (the bundled `optionsjourney/guide` first, then `docs/guide` in a source checkout) and renders each Markdown file with markdown-it-py:

- headings get GitHub's anchors (`slug`);
- `page.md#x` links become `/help/page#x`, and `README.md` becomes `/help`;
- links outside the guide (`../...`) become plain text;
- raw HTML is shown as plain text and never run.

## Searching Help
`/help/search?q=...` (`help_search` in `help_pages.py`, the box at the top of the Help sidebar) calls `search(root, query)` in `web/guide.py`. It splits every guide page into sections at its headings and keeps the sections whose heading or text holds every word typed (any case, part of a word counts). Ranking: a word in the heading is worth 10, each time a word appears in the text 1 (up to 3, so long sections don't win by length), and for two or more words the words together as typed are worth 20 in the heading and 10 each in the text (up to 3). Each hit links to `/help/<page>#<anchor>` and carries a snippet of about 200 characters around the phrase (else the first word), split into `(text, marked)` parts so the template marks the words with `<mark>` and Jinja still escapes everything. At most 50 hits; the pages are read on each search (the guide is small).

The box searches as you type (`static/js/help_search.js`): 250 ms after the last key, with two letters or more, it fetches that same `/help/search` page, swaps in its `.help__body` and updates the address bar, so Enter and typing show the same thing. A reply to an older search than the latest is dropped. Clearing the box on a Help page puts that page back; on the search page it shows the hint. Without JavaScript, Enter still works.

Images go in `docs/guide/images/` and are written as `images/name.png`. If that folder doesn't exist, `/help/images/...` answers 404.

## The link tests
`tests/web/test_help_pages.py`:

- `test_every_guide_link_resolves`: every link between guide pages and every `#anchor` in the rendered guide points at a page and a heading that exist.
- `test_every_learn_more_link_resolves`: every `/help/<page>#<anchor>` written in a template or script under `web/` (not `vendor/`) does too.
- `test_index_lists_every_page`: every guide page is linked from `docs/guide/README.md`.
- `help_links(text)` finds `/help/...` addresses in a string; reuse it in other tests.

If you rename a heading, the tests show you every link to fix.

## How the guide ships
`pyproject.toml` bundles `docs/guide` into the wheel as `optionsjourney/guide`. Nothing is copied by hand: the build does it, so the guide in the app is always the guide on GitHub.
