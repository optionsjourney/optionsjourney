# What the app does on its own

While OptionsJourney is running, it does a number of things without you asking. This page says what, when and why. All times are New York time.

The short version: it reads your trades from your brokers, watches your open trades, saves a few end-of-day numbers, keeps the market calendar up to date, and makes backups. If you pause trading, the watching can stop; see [Pausing trading](pausing-trading.md).

## A trading day, hour by hour
| Time | What happens |
|---|---|
| 9:15 AM | **Keep awake** starts, if you use the menu bar icon and have it on. The computer won't go to sleep. |
| 9:20 AM | The **pre-open check**: every trade your bots held overnight is compared with your broker's records. |
| All day | The app's **consistency checks** run on every 30-second pass and tell you about anything that doesn't add up. **Bots' entry times** are checked every 10 seconds. **Profit targets and stops the app is watching** are checked every 2 seconds. **Orders at your broker** (resting entries, exits, timed closes) are checked every 30 seconds. **Your own price alerts** are checked every 15 seconds. |
| 3:50 PM | The **expiry warning** for bots holding options that can be exercised early into expiration. At 12:50 PM on half days. |
| 4:00 PM | The market closes (1:00 PM on half days). |
| Shortly after 4:15 PM | The app saves each open trade's **P/L Day** figure (it looks every 10 minutes). If you've turned on **Settle expirations at 4:15 PM**, it syncs your trades, then settles that day's expiring options (it looks every 5 minutes). Shortly after 1:15 PM on half days. |
| Shortly after 4:15 PM | The app also saves your **stock holdings** and their closing prices, for the chart on the Journal's Stocks tab (it looks every 10 minutes). Shortly after 1:15 PM on half days. It waits until every connected broker answers. |
| 4:15 PM | **Keep awake** ends. |
| Evening | If the 4:15 PM settling couldn't finish (the sync failed, or a closing price wasn't out yet), it tries again every few minutes until 8:00 PM. |
| Later | When your broker or the exchange publishes the **official close**, settled expirations are corrected to it. |

Weekends and market holidays have no entry times, so nothing is entered. The app also checks every minute from 9:20 AM to the close that all of this is still running, and sends an alert once a day if something has stopped.

### P/L Day
The 4:15 PM figure is what **P/L Day** is measured from the next morning. If the app couldn't save a trade's figure (it was off, say), it uses your broker's previous close instead and shows a **≈**. It tries again every 2 minutes for a close it couldn't read.

The figure never uses an option quote whose market has been pulled after the close. It uses that option's last real price instead, which the app also saves so it still has it after a restart.

### The stock save
The save for the Stocks tab's chart happens once a day, and only on days the app is running. If a broker doesn't answer, it tries again every 10 minutes. If a closing price is missing, it keeps trying until 8:00 PM, then saves without it. The price is normally the official close, or else the regular session's last price that day. A day the app was off is never saved afterwards, and if a broker is still not answering at midnight New York time, that day is lost. See [Your trade journal](journal.md#stocks).

### Expiry settling
The **Settle expirations at 4:15 PM** switch is on the Journal page, above the trade list, and it's **off unless you turn it on**. See [Your trade journal](journal.md).

Options that expire are settled at the closing price, so the trade closes that same evening. This is a first estimate. When your broker's own expiration, assignment or exercise record arrives, it replaces the estimate. If that changes the profit or loss, you get an unread alert. See [Alerts](alerts.md#unread-alerts).

## How trades get into the Journal
Trades come in from your brokers in two ways.

- **A timed sync.** In market hours it's every minute by default; at other times, every hour. You choose both in **Settings → Syncing your trades** (see [Settings](settings.md#syncing-your-trades)). The app looks every 15 seconds whether one is due.
- **A sync after a fill.** When an order fills, your broker tells the app, and it syncs about 3 seconds later. Several fills close together make one sync, and there are never two within 10 seconds. If the connection to your broker drops, the app reconnects after about 10 to 60 seconds. TastyTrade always reports fills. Schwab does once the app has started showing live prices (a page or a bot needing them starts it), and then keeps going. **When an order fills** in Settings can turn this sync off.

**Why a trade can be missing for a moment:** the sync after a fill takes a few seconds, and if your broker's fill notice doesn't arrive, the trade waits for the next timed sync. Press **Sync now** in the Journal to read them straight away.

*For now, if you started the app from the menu bar icon, press **Sync now** yourself.* The menu bar copy doesn't yet start the timed sync or the sync after a fill. This is a known problem and will be fixed.

## The economic and market calendar
Bots use these dates for their blackout days and filters: economic events, FOMC days, CPI days and market holidays. The app reads them from four places:

| Source | When it's read |
|---|---|
| ForexFactory (economic events) | Each new week, from Sunday 6:00 PM |
| The Federal Reserve (FOMC days) | About once a week |
| FRED (CPI days), if you've entered a key in Settings | About once a week |
| The NYSE calendar (holidays, early closes) | About once a week |

The app checks every 10 minutes whether one is due.

**If one fails**, the app keeps the dates it already has and tries that source again an hour later. The problem shows in **Settings → Market calendar**. Bots that rely on that list get one alert a day until it works. Once a day, the app also tells you if its built-in holiday table has run out for the current year.

**Blackout days** and the other date lists work from this calendar. See [Blackout days](automation.md#blackout-days-and-skip-a-day) and [The market calendar](automation.md#the-market-calendar).

## Broker logins
Schwab asks you to log in again every 7 days. The app checks every 10 minutes and sends an alert at **48 hours** left, again at **12 hours** left, and once more when the login **has run out**.

## Backups
The app checks when it starts and then every hour. If a backup is due, it makes one: daily or weekly, as you chose, and it keeps between 7 and 60 of them. A backup missed while the app was off is made at the next start. If one fails, you get an alert and **Settings → Backups** says why. See [Settings](settings.md).

## When the app or computer is off
Nothing in this page happens while the app isn't running or the computer is asleep.

- **Stops and limit orders placed at your broker** keep working: they stay there and can still fill.
- **Stops the app watches itself do nothing.** That is always the case for Schwab and often for other stops too. If the price passes one while the app is off or the computer sleeps, nothing closes the trade. The app tells you about the gap when it starts, then acts on any stop that is past its level.

When the app starts again, it catches up before anything else:
- it loads the market calendar;
- it closes out bot runs that were interrupted;
- it flags trades whose exits are missing;
- it picks up profit targets a crash left firing, and tells you if watched stops went unwatched, with the times;
- it checks the trades held overnight against your broker;
- it tells you if an update switched any bots off.

Trades filled while the app was off are read at the first sync. The ones more than 10 minutes old by then are listed in Alerts but don't pop up.

## Safety switches
**No new trades** and **Freeze all orders** pause what's described above. Freeze also stops the app watching your trades. See [Pausing trading](pausing-trading.md).
