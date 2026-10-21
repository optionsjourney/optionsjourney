# 0052. No Schwab stream in gateway mode

- **Status:** Accepted
- **Date:** 2026-10-15

## Context
The quote hub subscribed both brokers' streams, so the backup was warm. In gateway mode the Schwab login is the Tracker2 server's, and Schwab allows one streamer session per login. A gateway-mode instance that opened a quote view logged in to Schwab's streamer as the real account and knocked the server's stream off, about 6 seconds after the server subscribed. On 2026-10-15 two preview instances left running did this repeatedly during market hours, and the server's Schwab stream went down (#117).

The trading guard didn't stop it. It sits under httpx, and the streamer is a websocket. The rule "never open a Schwab streaming connection" was written down in the developer docs but not enforced.

## Decision
- **In gateway mode the hub has only TastyTrade's stream.** `make_hub` takes `gateway=` and adds no `SchwabStream`; `create_app` passes `settings.is_gateway`. TastyTrade's DXLink allows several sessions, so it is safe to share.
- **The guard refuses Schwab's `/trader/v1/userPreference`** for every method, GETs included. Only `streamer_info()` reads it, and it holds the streamer's address and login. Without it no code can find the streamer.
- **`SchwabStream` refuses to connect while the guard is installed**, as a third layer.
- Local mode is unchanged: both streams, TastyTrade first, Schwab as the warm backup.

## Why
- Three layers, any one of which is enough: the hub never builds the stream, the stream won't start under the guard, and the guard won't hand out the streamer's address.
- Each layer is checked by a test that fails without it.

## Consequences
- In gateway mode, Schwab quotes come only from Schwab's REST snapshots (option chains, quotes). There is no streamed Schwab backup if TastyTrade's stream drops.
- Settings → Brokers shows only TastyTrade's live-price stream in gateway mode.
- Gateway mode must still not run for days unattended: each instance shares the server's TastyTrade login and token.
