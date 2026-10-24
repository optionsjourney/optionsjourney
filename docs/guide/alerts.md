# Alerts

The **bell** in the top bar shows a red number when there's something new, or a problem to fix. Click it to see a list of **recent alerts** right under it: problems and notes first, then the newest alerts, with the new ones highlighted. Click one to go to it, or **All alerts →** at the bottom to open the Alerts page. (Cmd-click or Ctrl-click the bell to open the page straight away.)

The number counts:
- **new alerts**, until you've seen them. Opening the bell's list, or the Alerts page, counts as seeing them, and the number goes down.
- **problems**, until they're fixed. You don't need to dismiss them; they clear themselves.

Notes, like a broker that hasn't synced for a few days, are listed but don't keep the number up.

The app tells you when:
- **an account didn't sync**, along with the broker's message (a problem);
- **a broker hasn't synced for more than 3 days**, so recent trades are missing (a note).

Both are only for brokers you've connected in Settings → Brokers. A broker you don't use, or an account you've closed, never raises them.

It also tells you when:
- **a scheduled backup didn't work**, for example because the backup folder is on a USB drive that isn't plugged in (a problem);
- **Schwab's weekly login is about to run out** (a note, at 48 hours and again at 12 hours left), or has run out (a problem).

## New alerts
When a profit or loss number the app shows changes, for example when your broker's official figure for a trade that expired differs from the app's first estimate, the app raises a **new alert** and adds it to the number on the bell. The same happens when one of your own price or trade alerts goes off. The number updates by itself, with no page reload.

Each new alert also **pops up** at the top right of whatever page you're on, with a chime, like a trade popup (with a red edge). It's always on: the **Trade popups** setting only covers trades.

They're highlighted in the bell's list and at the top of the Alerts page under **Recent**, with the time and a **Look** link that takes you to the trade. Once you've seen them they count as read and the number goes down. They stay listed (no longer highlighted) so you can find them again.

## Trade popups
When a trade opens, rolls, partly closes or closes, a popup shows at the top right of whatever page you're on, with a chime. That covers every trade in your journal, a bot's or one you placed yourself. It shows once your broker's fill is synced: within seconds for TastyTrade, and at the next sync for Schwab.

- A bot's stop on one side of a trade, then its wing sold, shows **Partly closed** then **Closed**.
- Each popup says the trade, its bot and account, and the cash: what you received or paid for those fills. Click the trade's name to open it in the journal.
- A **Closed** popup also lists the strikes the trade held when it closed, for example "Strikes: 5800/5790". If the trade was rolled, there's a line for each roll with its day and the strikes it moved from and to, like "Rolled: 2026-09-29 5800 → 5750". The Alerts page shows the same lines.
- A popup goes away after 20 seconds, even if the page reloads in between (the Journal does when new fills come in). Hovering over it keeps it up, and **×** closes it. At most 5 show at once; the rest are behind a "+N more" link to this page.
- Fills that come in late (after the app was off, say) are listed here but don't pop up.
- The chime needs a click or key press on the page first: browsers don't play sound before that, and the Journal's own reloads lock it again. A popup that came while sound was locked says **Click anywhere for sound**, and chimes when you do.

Everything from the last day is listed on the Alerts page's **Trades** tab, newest first, with buttons to show only one kind. To stop the popups, set **Trade popups** to Off in **Settings → Notifications**. The list still fills.

## On your phone: Discord and Telegram
Alerts always show in the app. To get them on your phone too, go to **Settings → Notifications**:
- **Discord:** in a Discord channel's settings, open **Integrations → Webhooks → New Webhook**, copy the webhook URL, and paste it in.
- **Telegram:** message **@BotFather**, send `/newbot` and copy the token; message **@userinfobot** for your chat id; then send your new bot any message once (Telegram bots can't start a chat).

Click **Send a test** to check it works. These are kept in the app's settings, which means they're also in your backups.

Each message is laid out to be read from your phone's lock screen: the first line says what happened (🔔 for your own alerts, 🔴 for a problem, ✅ for good news from a bot), the second line the detail. A bot's messages also show the trade, its legs and its stops; see [Automation → Alerts](automation.md#alerts), where each bot can also send its routine news to Discord only or Telegram only. Problems always go to every channel.

## Price alerts
Below the list of what needs your attention, the Alerts page has two tabs: **Price alerts** and **Trades**.

**Price alerts** lists every alert, yours and your bots', in a table:
- **Alert** is what it watches (a bot's alert also names its bot) and your note. Click it to open the symbol or the trade.
- **When it** is the condition, for example "goes below 7,700.00" or "open P/L goes above $250".
- **Now** is the live value, so you can see how close it is. A dash means there's no live price for it right now.
- **State** is **Armed** (watching), **Went off** (waiting for you to set it again or dismiss it) or **Off** (turned off, dismissed, or ended with its bot's trade).
- **Went off** says when, and at what value.

The buttons above the table choose what it shows: **Armed + fired today** (the starting view), **Armed**, **Fired** or **All**. On each row, **Turn off** stops an armed alert without deleting it, **Set again** turns one back on with the same level, and **Delete** removes it.

To set an alert, fill in the form at the top of the tab, for example "SPX goes below 7,700", with an optional note to yourself. To alert on a trade, open it from the journal and use **Alert me when its open P/L goes below…**, for example −$500.

On the [Trade](trade.md) page, **Add alert** sets a **spread alert** on the trade you built: a price for the spread, for example "below 0.20". When a price or spread alert goes off, its link opens the Trade page on that symbol. A spread's price is its legs' mids added up, as a positive number whether it's a credit or a debit. Spread alerts work for stocks and indexes, not futures.

OptionsJourney checks every 15 seconds from your broker's live prices while it's running. When an alert goes off, it counts on the bell until you've seen it, and stays at the top of the Alerts page until you choose: **Set again** turns it back on, and **Dismiss** clears it from there. Each alert goes off once until you set it again.
