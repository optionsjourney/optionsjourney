# 0100. Demo mode runs the real app on a pretend broker and a made-up market

- **Status:** Accepted
- **Date:** 2026-10-22

## Context
People who haven't installed OptionsJourney can't see what it does, and the author can't show it without real accounts (#263). A demo has to look and behave like the app: bots entering, stops resting and filling, alerts, a journal with history. It must also be unable to touch real money or leave the process.

## Decision
- **The real app, a pretend outside.** `BROKER_CREDENTIALS=demo` starts the same app. Only the things it talks to are pretend: a TastyTrade and a Schwab (`demo/broker.py`), a made-up market (`demo/market.py`), quotes and candles fed into the app's own quote hub and chart feed (`demo/feeds.py`), and made-up news headlines.
- **One fixed day.** Wednesday 21 October 2026, starting at 9:40 New York time. The clock is moved for the whole process with `time-machine`, and keeps ticking at normal speed. Skip ahead moves it 15 minutes or to the close. Each demo copy is its own process, so this is safe, and none of the app's many `now()` calls change.
- **The past is made by the app.** The seed writes about thirteen weeks of trades into the pretend accounts as each broker's own raw records, and the app's real sync brings them in. Three bots are saved through the bot store: SPX 0DTE condor, Weekly calendar and QQQ put spread. Past runs and exits that the app records only as a bot trades live are written as rows directly.
- **A director stages two moments.** About a minute after the 0DTE condor fills, SPX is steered up so its call-side stop fills. Within about six minutes the calendar's target fills. The director only moves the market. The bot and the pretend broker do the rest as on any day. No staged move is faster than 30 SPX points a minute.
- **The outside world is off, with two guards, neither removable.** The offline guard (`demo/offline.py`) refuses every `httpx` request and `websockets` connection, and logs each attempt. The trading guard from gateway mode is installed too. Backups, update checks, notifications, broker setup and Import a bot show "Not in the demo".
- **A demo database never becomes a real one.** Demo mode builds its seed only into an empty data folder and refuses a folder holding any other database. Local and gateway mode refuse a database marked as a demo's. The demo's pre-ticked risk boxes and armed Live bot can't carry over.
- **The test fakes stay in `tests/`.** The pretend broker is written new in `src/`. It does much more (a market, matching, transactions, chains), and moving the fakes would put test-only behaviour, such as forced rejects, into the shipped app.
- **Trading happens at the pretend TastyTrade.** The pretend Schwab holds history, stock holdings and chart history.
- **A runner.** `python -m optionsjourney.demo` runs a day in a child process on a fresh folder. Start a new day makes the app exit with a code the runner answers by starting again.

## Why
- Running the real code means the demo can't drift from the app, and every change to the app shows up in the demo.
- A fixed day makes the demo repeatable and lets the director stage the moments that sell it, without a live market.
- Two guards, and the refusals both ways, because a mistake here would be real orders or a polluted real journal.
- Every demo branch sits behind `settings.is_demo`, so local and gateway mode don't change.

## Consequences
- The seed and director are tuned to the bots' own exit math. If that math changes, the demo's tests fail and the constants are retuned.
- Rows the seed writes directly can drift from the app's schema. The demo tests render every page to catch it.
- Without the runner, Start a new day just stops the app.
- Seats, `/stats`, the visitor tour and Railway deployment come next, as separate work.
