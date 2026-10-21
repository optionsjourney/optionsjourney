# Alerts

The **bell** in the top bar shows a red number when something needs your attention. Click it to see what, and use the button next to each alert to go and fix it.

Alerts clear themselves once their cause is fixed. You don't need to dismiss them.

The app tells you when:
- **an account didn't sync**, along with the broker's message;
- **a broker hasn't synced for more than 3 days**, so recent trades are missing;
- **a scheduled backup didn't work**, for example because the backup folder is on a USB drive that isn't plugged in;
- **Schwab's weekly login is about to run out** (at 48 hours and again at 12 hours left), or has run out.

## Unread alerts
When a profit or loss number the app shows changes, for example when your broker's official figure for a trade that expired differs from the app's first estimate, the app raises an **unread alert** and adds it to the number on the bell. It updates by itself, with no page reload.

Open the Alerts page and they appear at the top under **Unread**, highlighted, with the time and a **Look** link that takes you to the trade. Once you've seen the page they count as read and the number on the bell goes down. They stay listed (no longer highlighted) so you can find them again.

## Trade popups
When a trade opens, rolls, partly closes or closes, a popup shows at the top right of whatever page you're on, with a chime. That covers every trade in your journal, a bot's or one you placed yourself. It shows once your broker's fill is synced: within seconds for TastyTrade, and at the next sync for Schwab.

- A bot's stop on one side of a trade, then its wing sold, shows **Partly closed** then **Closed**.
- Each popup says the trade, its bot and account, and the cash: what you received or paid for those fills. Click the trade's name to open it in the journal.
- A **Closed** popup also lists the strikes the trade held when it closed, for example "Strikes: 5800/5790". If the trade was rolled, there's a line for each roll with its day and the strikes it moved from and to, like "Rolled: 2026-09-29 5800 → 5750". The Alerts page shows the same lines.
- A popup goes away after 20 seconds, even if the page reloads in between (the Journal does when new fills come in). Hovering over it keeps it up, and **×** closes it. At most 5 show at once; the rest are behind a "+N more" link to this page.
- Fills that come in late (after the app was off, say) are listed here but don't pop up.
- The chime needs a click or key press on the page first: browsers don't play sound before that, and the Journal's own reloads lock it again. A popup that came while sound was locked says **Click anywhere for sound**, and chimes when you do.

Everything from the last day is listed under **Trades** on the Alerts page, newest first, with buttons to show only one kind. To stop the popups, set **Trade popups** to Off in **Settings → Notifications**. The list still fills.

## On your phone: Discord and Telegram
Alerts always show in the app. To get them on your phone too, go to **Settings → Notifications**:
- **Discord:** in a Discord channel's settings, open **Integrations → Webhooks → New Webhook**, copy the webhook URL, and paste it in.
- **Telegram:** message **@BotFather**, send `/newbot` and copy the token; message **@userinfobot** for your chat id; then send your new bot any message once (Telegram bots can't start a chat).

Click **Send a test** to check it works. These are kept in the app's settings, which means they're also in your backups.

Each message is laid out to be read from your phone's lock screen: the first line says what happened (🔔 for your own alerts, 🔴 for a problem, ✅ for good news from a bot), the second line the detail. A bot's messages also show the trade, its legs and its stops; see [Automation → Alerts](automation.md#alerts), where each bot can also send its routine news to Discord only or Telegram only. Problems always go to every channel.

## Your own alerts
On the Alerts page, under **Your alerts**, set an alert for when a price goes above or below a level, for example "SPX goes below 7,700", with an optional note to yourself. To alert on a trade, open it from the journal and use **Alert me when its open P/L goes below…**, for example −$500.

On the [Trade](trade.md) page, **Add alert** sets a **spread alert** on the trade you built: a price for the spread, for example "below 0.20". When a price or spread alert goes off, its link opens the Trade page on that symbol. A spread's price is its legs' mids added up, as a positive number whether it's a credit or a debit. Spread alerts work for stocks and indexes, not futures.

OptionsJourney checks every 15 seconds from your broker's live prices while it's running. When an alert goes off, it appears at the top of the Alerts page and in the top bar's count. **Set again** turns it back on, and **Dismiss** clears it. Each alert goes off once until you set it again.
