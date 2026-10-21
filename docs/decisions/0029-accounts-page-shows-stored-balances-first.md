# 0029. The Accounts page shows stored balances first

- **Status:** Accepted
- **Date:** 2026-10-11
- **Issue:** #74

## Context
The Accounts page asked every broker for every account's balance on each visit, one call after another, before it showed anything. Schwab took 2 calls plus one per account, and TastyTrade took 1 plus one per account. With ten or so accounts, the page sat blank for a second or two on every visit.

## Decision
- **A `latest_balances` table** keeps each account's balance as last read: net liquidating value, cash, buying power, the account's name, and when it was read. Each live read overwrites it. Accounts the broker no longer lists are removed.
- **`/accounts` reads only that table.** It makes no broker calls. Each broker's card says "As of" the oldest balance shown. On the first visit, with nothing stored yet, the cards say "Loading balances…".
- **`/accounts/live` asks the brokers**, all brokers at once, and saves what it reads. It returns just the total and the cards, which `accounts.js` swaps in once when the page opens. The page doesn't poll.
- **A failed read keeps the last balance showing**, with the reason and when that balance was read. This applies to a whole broker or to one account.
- **Schwab's balances come from its `/accounts` answer**, which already carries them, so it no longer makes one call per account.

## Why
- **Not `account_values`:** that table is end-of-day history. It has no buying power, and its TastyTrade rows are yesterday's close. Writing intraday TastyTrade reads into it would change the history chart.
- **Not a short in-memory cache:** a cache still leaves the first visit after a restart blank, and it can't show anything when a broker is down.
- Schwab's daily value is still recorded from each live read ([0023](0023-manual-entry-backups-account-values.md)). Balances shown only because a read failed are not recorded.
