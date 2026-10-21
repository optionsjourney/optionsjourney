# How to get alerts on your phone

*Alerts always show in the app. To get them on your phone too, connect Discord, Telegram, or both.*

## Discord
1. In Discord, open the settings of a channel you want the alerts in. Go to **Integrations → Webhooks → New Webhook**, then **Copy Webhook URL**.
2. In the app, open **Settings** (the icon at the right of the top bar) and go to **Notifications**.
3. Paste it in **Discord webhook URL** and press **Save**.

## Telegram
1. In Telegram, message **@BotFather**, send `/newbot`, follow its questions, and copy the token it gives you.
2. Message **@userinfobot**. It replies with your id, a number like `123456789`.
3. Send your new Telegram bot any message once. A Telegram bot can only message people who've messaged it first.
4. In the app's **Settings → Notifications**, paste the token in **Telegram bot token** and the number in **Telegram chat id**, and press **Save**.

## Check it works
Press **Send a test** under Notifications. The page says, for example, "Discord: test sent. Check that it arrived." Then look on your phone. If a box is wrong, the page says what it expects, and nothing is saved until it's right.

## What you'll get
- Each message is written for your phone's lock screen: the first line says what happened (🔴 a problem, ✅ good news from a bot, ⚠️ worth a look, 🔔 one of your own alerts), the second line the detail.
- Bot alerts about a trade also show its legs and each side's stop and target.
- **Problems always go to every channel** you've set up. A bot's routine news (a close or target that filled) can go to just one: set **Routine alerts go to** in the bot's **Alerts** section.
- To stop sending to one, clear its box and press **Save**.

The token and the link are kept in the app's settings, so they're in your backups too.

More detail: [Alerts](alerts.md#on-your-phone-discord-and-telegram), [a bot's alerts](automation.md#alerts).
