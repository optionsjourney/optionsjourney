# Broker credentials and the trading guard

## Providers
Broker clients never log in themselves. Before each request they ask a **credential provider** for a short-lived access token (`brokers/credentials/`):

```python
class CredentialProvider(Protocol):
    def tastytrade_token(self) -> AccessToken: ...
    def schwab_token(self) -> AccessToken: ...
```

`BROKER_CREDENTIALS` chooses which provider the app uses:

| Mode | Provider | Used by |
| --- | --- | --- |
| `local` | `LocalProvider`: the user's own logins on this computer | Everyone. *Connecting a broker arrives with the setup wizard (Phase 4); until then it reports "not connected".* |
| `gateway` | `GatewayProvider`: tokens leased from the author's Tracker2 server | Development only ([0006](../decisions/0006-dev-gateway-credentials.md)) |

A provider raises `CredentialsUnavailable` with a message fit to show the user when it has no token. The pages show that message instead of failing.

## Broker clients
- **The clients:** `brokers/tastytrade.py` and `brokers/schwab.py` are small REST clients ([0012](../decisions/0012-broker-apis-over-plain-http.md)), each with `accounts()` and `balance(account_number)`.
- **Shared HTTP handling:** every request goes through `brokers/http.get_json`, which turns failures into a `BrokerError` with a message fit to show the user.
- **Schwab account hashes:** Schwab addresses accounts by an opaque hash rather than the account number. `SchwabClient.accounts()` learns the number → hash mapping that `balance()` needs.
- **Cash accounts:** for Schwab cash accounts, which may leave `liquidationValue` empty, the balance falls back to `totalCash`.

## Where local logins are kept (`brokers/credentials/store.py`)
In `local` mode, the user's broker secrets (TastyTrade's client secret and refresh token, Schwab's app key and secret and refresh token) are kept in the **system keychain** through the `keyring` library: macOS Keychain or Windows Credential Manager. They're never stored in the database or a file, so backups and copies of the database never carry them.
- **Layout:** one JSON item per broker, with service `OptionsJourney` and the broker as the account name.
- **Tests** use `MemoryStore` and never touch the real keychain.

## Local mode: TastyTrade (`brokers/credentials/local.py`)
- **What's kept:** the user's OAuth client secret and refresh token (and whether it's a sandbox account).
- **Access tokens:** each short-lived access token (15 minutes) comes from `POST /oauth/token` with `grant_type=refresh_token`. It's cached, and renewed when under a minute is left.
- **A second computer:** TastyTrade's refresh token doesn't change when used, so using it on another computer doesn't sign the first one out. Tracker2 keeps working.
- **Connecting:** `connect_tastytrade` checks the login with TastyTrade before keeping it. Settings → Brokers has the form and Disconnect. In gateway mode that section only explains where the logins come from.
- **Locks and back-off:** each broker has its own lock, so one broker's slow token server never holds up the other. A failed refresh is remembered for 30 seconds and its message repeated without asking again; reconnecting clears it. A token reply that isn't JSON, or has no access token, counts as a failed refresh ("sent a login reply the app couldn't read").

## Local mode: Schwab
- **Setting up:** the user registers an app on developer.schwab.com (Accounts and Trading plus Market Data, callback `https://127.0.0.1`). Schwab takes days to approve it.
- **`start_schwab`:** keeps the app key, secret and callback URL, and returns Schwab's authorize link. Blank fields reuse the kept ones, so **Reconnect** needs no typing.
- **The login:** after logging in, Schwab sends the browser to the callback with `?code=…`. Nothing listens there, so the user pastes the address, and `finish_schwab` trades the code (it lasts about 30 seconds) for tokens: `POST /v1/oauth/token`, HTTP Basic with key:secret, `grant_type=authorization_code`. This is Tracker2's dependable "manual" method, without schwab-py's local HTTPS listener.
- **Access tokens:** 30 minutes, each from the refresh token. If Schwab returns a new refresh token, it's kept.
- **7 days:** the login lasts 7 days from `logged_in_at`. After that `schwab_token` asks for a new login, and an alert reminds the user a day before (`core/alerts.schwab_login_alert`).
- **Tracker2:** whether a new login with the same app ends Tracker2's session is untested. See #17 before testing with production keys.

## The setup wizard (`web/routes/setup.py`, `services/setup.py`)
- **When it opens:** the home page sends to `/setup` on the first start in local mode, never in gateway mode. Reaching the last step marks setup finished (an internal `_setup_completed` row in `settings`).
- **Steps:** the broker steps appear only for the brokers the user ticked. They reuse the connect cards from `templates/_brokers.html`, which post to the Settings routes with `next` (a path in this app only) so the user comes back to the wizard.
- **Trading style:** the step creates a tag for each picked style and removes unpicked starter tags that are on no trade and that no bot files under (`setup.apply_styles`).

## Gateway mode
- **The lease:** `GatewayProvider` calls `GET {GATEWAY_URL}/gateway/credentials` with `Authorization: Bearer {GATEWAY_SECRET}`. The server only answers requests from the home network.
- **What it holds:** the lease contains **access tokens only**, for TastyTrade and, if the server is logged in, Schwab. It never contains refresh tokens, so this machine can't log in or extend its own access.
- **Caching:** the lease is cached, and fetched again once a token has less than 2 minutes left.
- **The server's side:** it's Tracker2's existing `/gateway/credentials` endpoint (`src/blueprints/gateway_bp.py` and `src/services/gateway_credentials.py` in Tracker2). Nothing on the server changes for OptionsJourney.

To use it, copy `GATEWAY_URL` and `GATEWAY_SECRET` from Tracker2's dev `.env` into this repo's `.env`, and set `BROKER_CREDENTIALS=gateway`.

## The trading guard
`brokers/guard.py` patches `httpx.Client.send` and `httpx.AsyncClient.send`, which sit under every HTTP call the app makes. When installed, it answers these with a synthetic **403** without sending them:

- **TastyTrade:** any non-GET to `/accounts/...` (orders, cancels, replaces) and `/oauth/token`
- **Schwab:** any non-GET to `/trader/v1/accounts/...` and `/v1/oauth/token`
- **Schwab's streamer login:** `/trader/v1/userPreference`, GETs included. It holds the streamer's address and login (decision 0052).

These are still allowed: all other GETs, TastyTrade `.../orders/dry-run`, and Schwab `.../previewOrder`. Order previews let bots run end to end without sending anything.

**How it gets switched on:**
- `create_app` installs it in gateway mode before any broker client exists.
- `GatewayProvider` installs it again, as a safety net.
- Once installed it stays on for the life of the process. There is no public way to remove it; tests use `_uninstall_for_tests()`.
- `/health` reports `"trading_guard": true`.

When adding a broker endpoint that changes anything, add a case to `tests/brokers/test_guard.py`.

## Rules for gateway mode
- **Never open a Schwab streaming connection.** Schwab allows one per login, and ours would knock the server's off. Three things enforce it (decision 0052): `make_hub` builds no Schwab stream in gateway mode, `SchwabStream` won't connect while the guard is installed, and the guard refuses `/userPreference`. The guard can't see websockets, so any new streaming code must check `guard.is_installed()` itself.
- **Stop preview servers when you're done.** Each one shares the server's logins; don't leave one running across days.
- **Never refresh a token.** The guard blocks it anyway.
