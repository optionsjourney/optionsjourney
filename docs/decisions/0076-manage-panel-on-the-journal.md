# 0076. A Manage panel on every journal row, built from the full pages' own sections

- **Status:** Accepted
- **Date:** 2026-10-19

## Context
Managing a trade meant leaving the journal: a bot's trade went to its run page (`#manage`), a trade with manual rules to the Manage this trade page, and a plain trade to its trade page first. Tracker2 has a gear on each row that opens a slide-in drawer over the journal (`#manage-bot-drawer`). Issue #174.

## Decision
- Every journal row, in every view, gets a gear button ("Manage this trade"). It opens a panel from the right over the journal; ✕, Esc or a click on the backdrop closes it. On a phone the panel is the full width.
- The panel is HTML from `GET /journal/trades/<key>/panel`, fetched on click. It is **made of the full pages' own sections**: the run page's facts, orders step by step, Manage this trade and this trade's settings, and the manage page's rules, moved into partials (`_run_facts.html`, `_run_steps.html`, `_run_manage.html`, `_run_settings.html`, `_run_cancel_close.html`, `_manage_rules.html`) that both include. The routes build what they show in one place each (`automation.run_context`, `journal._manage_context`).
- Forms in the panel are ordinary posts to the existing routes, with a hidden `next`: the journal view it came from plus `?panel=<key>`. Those routes go back to `next` (only a path in this app: `security.local_path`), carry it through a confirm step, and the journal opens the panel again on load with the page's messages copied into it. A form posted from a full page has no `next` and behaves exactly as before.
- Read-only for closed trades: a summary, and for a bot's trade its orders; no controls.

## Why
- Posting forms and coming back keeps one code path for every action, its checks and its flash messages, with no JavaScript copy of any business rule. Re-rendering the panel by fetch would have needed a JSON answer from each route.
- Partials keep the run page and the panel from drifting apart. The rendered full pages were compared before and after the change: identical.

## Consequences
- Sending a form reloads the journal (the panel reopens). Tracker2 updates its drawer in place.
- The panel follows the run page's order within its Manage section, so **Close entire trade** sits at the top of that section rather than in a footer as in Tracker2.
- Custom rules with a problem show the full manage page (with the problem beside the field) rather than the panel.
- Not built yet: Tracker2's overnight (GTC) resting exits and live polling of a close's status in the drawer (follow-up issues).
