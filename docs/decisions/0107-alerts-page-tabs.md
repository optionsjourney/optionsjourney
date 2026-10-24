# 0107. The Alerts page has Price alerts and Trades tabs

- **Status:** Accepted
- **Date:** 2026-10-23

## Context
The Alerts page ran as one long column: what needs attention, Recent, then "Your alerts" (a short form and a plain list of armed alerts), then the trades of the last day. The alerts that were already set were easy to miss. That included a bot's own alerts, such as "+50% on the trade". The list also showed no live value and no state. The trades list had started to feel out of place on an alerts page.

Tracker2's Alerts page has two tabs: Transactions (the trade events) and Price alerts (every alert with its condition, live value, state and when it fired, with filters).

## Decision
- **The top stays the same:** what needs attention, then Recent.
- **Below it, two tabs as in Tracker2.**
  - **Price alerts** comes first. It has the form, filters (Armed + fired today, Armed, Fired, All) and a table of every alert, the user's and the bots'. Each row shows its condition, the live value, its state (armed, went off or off) and when it went off, with Turn off / Set again and Delete.
  - **Trades** holds the trade events list as before.
- **Trades keeps its name.** Tracker2 calls the tab Transactions, but this app already has a Transactions page for the broker's records.
- **The trades stay on the Alerts page.** They're the history of the trade popups, which are alerts.
- The live value comes from the same function the 15-second check uses, so the table and the check always agree.

## Why
- One place shows every alert that is watching, with how close it is, and that includes the bots' alerts.
- The tabs keep the page short without moving anything people already know where to find.

## Consequences
- The tab is chosen by the address's `#price` / `#trades`, so links can open either one. Without JavaScript both show.
