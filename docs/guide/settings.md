# Settings

Click the **gear** in the top bar. Pick a section on the left: **Brokers**, **Your orders**, **Backups**, **Notifications**, **This computer**, **Market calendar**, **Charts** or **Syncing your trades**. On a narrow window the sections are tabs across the top. **Run setup again**, under the sections, walks through the first-start setup again.

Each setting has a short line saying what it's for. For the full explanation, point at the **ⓘ** next to its name, or tap it. Buttons explain themselves the same way: point at one to see what it does before you click.

Click **Save** at the bottom of a section to keep its changes. Nothing is saved until every value in the section is valid. If something's wrong, the page goes back to that section, says what to fix and keeps what you typed.

## Brokers
Each broker shows whether it's **Connected**. For Schwab, the pill turns amber (**Log in again soon**) a day before Schwab's 7-day login runs out, and red (**Log in again**) once it has. Click **Reconnect Schwab** to log in again. **Live prices** shows whether each broker's price stream is running.

## Your orders
**Largest order you can place** is the most contracts you can put in one leg on the [Trade](trade.md) page. Orders over it are refused. The default is 10.

## Notifications
**Trade popups** (on or off): a popup on any page when a trade opens, rolls or closes; see [Alerts](alerts.md#trade-popups). Then where alerts are sent besides the app: a Discord webhook, and/or a Telegram bot and chat id. Blank means off. **Send a test** checks them. See [Alerts](alerts.md).

## Backups
- **Backup folder:** where copies of your journal go. Click **Browse…** to pick it. Picking a folder inside iCloud Drive, Dropbox, OneDrive or Google Drive keeps a copy off this computer.
- **Backups to keep:** 7, 14, 30 (the default) or 60. Backups are compressed, typically to a tenth of the journal's size.
- **Backups:** every day (the default), every week, or off. The app checks when it starts and every hour after, so a backup missed while it was off is made at the next start. Older ones beyond the number to keep are deleted. Other files in the folder are never touched.
- **Back up now** makes one straight away.
- **Restore** puts a backup back in place of your journal. Your journal as it is just before is backed up first, so if you change your mind, restore that one.
- If a backup fails (for example, a USB drive that isn't plugged in), the Backups section says so.

## This computer
- **Keep awake during market hours:** stops the computer going to sleep on weekdays from 9:15 to 4:15 New York time. It works with the [menu bar icon](menubar.md).
- **Open from other devices:** off (the default) or on. On, your other computers and your phone can open the app at this computer's address followed by `:2026`. There's no login, so anyone who can reach this computer can see your accounts and place orders: only turn it on for networks you trust. It takes effect after **Restart OptionsJourney** in the OJ menu. See [Can I use it from my other computers or my phone?](faq.md#can-i-use-it-from-my-other-computers-or-my-phone)
- **Check for new versions:** on (the default) or off. Once a day the app asks GitHub if a newer version is out. Nothing about you is sent. If there is one, the OJ menu offers it. See [Updating the app](updating.md).

## Syncing your trades
The app reads new trades from your brokers on its own, so they show in your journal without you doing anything. To read them straight away, press **Sync now**: it's here, at the top of the Journal, and on the Transactions page.
- **During market hours** (9:30 to 4:00 New York time on trading days): every minute (the default), every 2, 3, 4, 5 or 15 minutes, or off. Every minute means a new trade shows within about a minute.
- **Other times** (evenings, weekends and holidays): every hour (the default), every 15 minutes, or off. This catches futures trades and what your broker books after the close, such as expirations and assignments.
- **When an order fills:** on (the default) or off. Your broker tells the app the moment an order fills, and the app syncs a few seconds later, so the trade shows in your journal within seconds. TastyTrade always reports fills. Schwab reports them while the app is showing its live prices. The timed syncs above catch anything missed.
- **Settle expirations at 4:15 PM:** off (the default) or on. When it's on, at 4:15 PM New York time on trading days the app syncs, then closes trades whose options expired that day at the last price from your broker, so 0DTE trades count in your P/L the same day. When it's off, they wait for your broker's expiration record, usually the next day. See [Your trade journal](journal.md) for how settling works.

## Market calendar
The dates bots use for FOMC days, CPI days, the last trading day of the month and market holidays. It shows each list's next few days, where the dates come from and when each was last read, and any problem reading them. **Refresh now** reads them all again (reload the page after a minute). The optional **FRED API key** adds CPI days further ahead. See [The market calendar](automation.md#the-market-calendar).

## Charts
The optional **Finnhub API key** gives better-matched headlines in the Charts page's [News](charts.md#news) panel. Without it, News reads Yahoo Finance's free feed. Get a free key at [finnhub.io](https://finnhub.io), then paste it here.

## Buy me a coffee
At the bottom of every page there's a **buy me a coffee** link, if you'd like to support OptionsJourney. It opens the author's [Buy Me a Coffee page](https://buymeacoffee.com/jtse90881) in your browser. It's entirely optional, and the app doesn't send anything about you.
