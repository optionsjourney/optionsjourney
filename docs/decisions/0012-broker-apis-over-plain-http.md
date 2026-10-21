# 0012. Call broker APIs over plain HTTP, and use plain SQLAlchemy

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
Tracker2 talks to TastyTrade through the official `tastytrade` Python SDK. Version 12 of the SDK is async-only, so Tracker2 needs a dedicated event-loop thread (`tt_runtime.py`) just to make ordinary calls. Upgrades have been disruptive too: version 13 rewrites the order models.

Tracker2 also uses Flask-SQLAlchemy, which ties every database query to a Flask "app context". Its bots and background jobs have to fake one to touch the database.

## Decision
- **Broker REST calls:** use the broker's REST API directly, through `httpx`, with small clients of our own (`brokers/tastytrade.py`, and later `brokers/schwab.py`). An SDK can be added later for the one thing that's hard to do by hand, which is market-data streaming, and only for that.
- **Database:** use **plain SQLAlchemy 2** with our own engine and sessions (`db/`), not Flask-SQLAlchemy.

## Why
- **Fewer moving parts:** a plain synchronous HTTP call needs no event-loop thread, and the code is easy to read.
- **We control the surface:** we use only a handful of endpoints, and the SDK's frequent breaking changes stop being our problem.
- **The trading guard covers everything:** all broker traffic goes through `httpx`, which is exactly where the guard sits.
- **Database access without Flask:** bots, the menu bar app and background jobs can all use the database with no fake app context.

## Consequences
- **We parse broker responses ourselves,** so the tests pin the response shapes we rely on.
- **Broker API changes need our own fixes** rather than an SDK upgrade.
