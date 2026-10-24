# 0106. Quieter alerts: no daily settled-P/L alert, and sync alerts only for brokers in use

- **Status:** Accepted
- **Date:** 2026-10-23
- **Amends:** [0078](0078-pl-at-the-close-from-either-broker.md)

## Context
- Each evening the 4:15 settle run raised an alert like "Today's expirations settled: P/L … (prelim)". It said nothing that needed doing, and it counted on the bell as new every day.
- "<Broker> last synced N days ago" also showed for a broker that wasn't connected (the user never set it up, or disconnected it), and it would show for an account that had been closed. There was nothing the user could do to clear it.

## Decision
- **The settle run is quiet.** 0078's summary alert is dropped. The other two alerts stay, so the user still hears when something differs:
  - "P/L corrected": the official close moved a trade's P/L by $1.00 or more.
  - "Your broker's record differs": the broker's own record is $1.00 or more from the app's.
- **Sync alerts only for brokers in use.** `core.alerts.sync_alerts` takes a `connected(broker)` check, which comes from the credential provider. A provider that can't tell (gateway, demo) counts every broker as connected.
  - It looks only at the accounts in the broker's latest sync: an account whose last attempt is more than a day behind the broker's newest is one the broker stopped returning.
  - It notes a broker as stale only when the broker as a whole has stopped syncing (its newest account sync is over 3 days old).

## Why
- An alert should mean "look at this". A normal close and a broker the user doesn't use aren't that.
- A connected broker that really has stopped syncing (a token that no longer works, a failing account list) still gets its note. So does an account whose sync fails.

## Consequences
- Old "settled" alerts stay in the Recent history; no new ones are made.
- A closed account's old sync error no longer shows once the broker's other accounts have synced past it.
