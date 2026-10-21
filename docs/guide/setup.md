# Setting up

The first time you open OptionsJourney, a short setup guide walks you through getting started. You can run it again any time from **Settings → Run setup again**, for example to add Schwab once Schwab has approved your app.

1. **Welcome:** what the app does. Everything stays on your computer. Tick the box saying the app isn't financial advice and you're responsible for your orders (see the [disclaimer](disclaimer.md)), then press **Start**. You're asked once.
2. **Your brokers:** tick TastyTrade, Schwab, or neither (then you'll add trades by hand).
3. **Connect TastyTrade:** create an API application on tastytrade.com and paste its client secret and refresh token. They're checked with TastyTrade before they're kept, in your computer's keychain.
4. **Connect Schwab:** register an app on developer.schwab.com (Schwab takes a few days to approve it), then log in. If your app isn't approved yet, click **Finish later**.
5. **How you trade:** tick what you trade. Each one becomes a tag you can put on your trades, to filter and compare them. Starter tags you untick are removed if no trade has them.
6. **Backups:** where copies of your journal go, and how often.
7. **All set:** click **Sync now** to bring in your trades. Your history starts on the day of the app's first sync: trades from before it aren't brought in (see [Troubleshooting](troubleshooting.md#why-is-a-trade-missing-from-the-journal)).

## TastyTrade or Schwab: what's different
Most of the app works the same with either broker. Here's where they differ, so you know which one to use for what.

| | TastyTrade | Schwab |
|---|---|---|
| **Futures and futures options** (/ES, /GC …): chains, orders, futures bots | Yes | No. Schwab doesn't offer futures options to the app |
| **Futures charts** | Yes | No |
| **Overnight orders** (**GTC overnight**, see [The ticket](trade.md#the-ticket)) | Yes, for index options (SPX, XSP, VIX, RUT) with a limit price | No |
| **Live chart candles** | Yes | History only, without live updates |
| **Buying power effect** on **Review order** | Yes | No |
| **Open interest** on the SPX chain | Yes | No |
| **Stock and ETF bots** | Yes | Only with TastyTrade connected as well, for the price steps (see [Which underlying](automation.md#which-underlying)) |
| **Fills reported straight away** | Always | While the app is showing live prices |
| **Account value history** | The last year comes in on the first sync | Starts on the first sync |
| **Logging in again** | Not needed | Every 7 days, on one computer at a time (below) |

## Schwab: one computer at a time
Schwab allows one login per developer app. If you log in to Schwab from OptionsJourney on another computer (or from another program that uses the same Schwab app), the first one is signed out and needs **Reconnect Schwab**.
