# 0006. Development borrows tokens from the author's server

- **Status:** Accepted
- **Date:** 2026-10-09
- **Applies to:** development only. Regular users never need this.

## Context
The author's broker logins live on his always-on Tracker2 server. To build and test OptionsJourney against real accounts and real market data, the development machine needs broker access. It shouldn't do that by logging in a second time, because logging in from another machine can disrupt the server, and Schwab allows only one streaming connection per login.

Tracker2 already solves this for its own development setup. The server lends **short-lived access tokens** to development machines on the home network, and never hands over the refresh tokens that would let a machine log in on its own.

## Decision
OptionsJourney has one setting that chooses where broker credentials come from:

```bash
BROKER_CREDENTIALS=local     # default: your own broker logins, stored on your computer
BROKER_CREDENTIALS=gateway   # development: borrow tokens from the Tracker2 server
GATEWAY_URL=...              # gateway mode only
GATEWAY_SECRET=...           # gateway mode only (never commit this)
```

When `gateway` is set:
- **Trading is blocked.** Real orders and token refreshes are blocked at the network layer. Order *previews* (dry runs) are still allowed, so bots can be tested from start to finish without sending anything.
- **No Schwab streaming.** The app never opens a Schwab streaming connection, because that would knock the server's own connection off.
- **No changes to the server:** it uses the endpoint Tracker2 already provides, as it is.

## Why
- **Real data, no risk:** the author can develop against real accounts and real market data with no chance of placing a trade by mistake.
- **No second set of logins:** broker credentials stay in one place.
- **Proven approach:** it reuses something Tracker2 has already run safely.

## Consequences
- **Home network only:** gateway mode works only on the author's home network.
- **Depends on the server:** if the server's Schwab login expires, development loses Schwab data until it's renewed on the server.
- **Off by default:** the gateway code ships in the app, but it does nothing unless this setting is turned on.
