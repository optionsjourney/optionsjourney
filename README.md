# OptionsJourney

**An options-trading journal and bot app that runs on your own computer.**

OptionsJourney connects to your **TastyTrade** and/or **Schwab** account, keeps a journal of every trade, shows how each kind of trade is doing, and can run trading **bots** for you, starting in Paper so you can watch them before any real money is involved. Your trades and your broker logins stay on your computer.

> **Not financial advice. You are responsible for every order, including bot orders.** Read the [disclaimer](DISCLAIMER.md) before connecting a broker.

> **Early days.** OptionsJourney is used by its author and a few friends. Expect rough edges, and start with Paper.

## What it does

- **Journal:** every trade from your brokers, grouped into positions, with P/L, win rate and tags to compare strategies. Live open P/L updates every few seconds.
- **Bots:** set entry times, legs, stops and profit targets; run them in **Paper** (pretend fills) first, then switch to **Live**. Stops rest at your broker where it can hold them.
- **Trade:** an option chain, an order ticket and a risk graph, with a preview before anything is sent.
- **Charts:** a grid of candlestick charts with EMAs, VWAP and news.
- **Alerts:** price and P/L alerts in the app, or on your phone via Discord or Telegram.
- **Safety switches:** **No new trades** and **Freeze all orders** stop every bot at once, and every bot order goes through one central check with size limits.

## Is it safe?

- It runs only on your computer. Your trades and account details go only to your broker (and to Discord or Telegram, if you turn on phone alerts). The app also reads public data such as news and the economic calendar.
- Your broker logins are kept in your computer's own password store.
- No account to create, no subscription, no tracking.

## Install

1. Download the latest version from [Releases](https://github.com/optionsjourney/optionsjourney/releases/latest) and **unzip it** (on Windows: right-click → Extract All).
2. Open the **scripts** folder and double-click **Install OptionsJourney.command** (Mac) or **Install OptionsJourney.bat** (Windows).
3. The app installs into your user folder, opens in your browser, and a setup guide walks you through connecting your broker. Afterwards, start it from the Start menu (Windows) or Spotlight (Mac).

Step-by-step instructions, with what to do if your computer warns you: [Installing OptionsJourney](docs/guide/installing.md).

## Help

- [User guide](docs/guide/README.md): every page of the app, in plain language. It's also inside the app under **Help**.
- Questions and ideas: [Discussions](https://github.com/optionsjourney/optionsjourney/discussions).

## For developers

How the code is organized: [docs/developer/](docs/developer/). Why it works the way it does: [docs/decisions/](docs/decisions/). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Free for personal, non-commercial use under the [PolyForm Noncommercial License 1.0.0](LICENSE). Selling it, or offering it as a paid service, is not allowed. See also the [disclaimer](DISCLAIMER.md).
