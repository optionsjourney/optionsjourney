# How to hand a trade to a bot

*You opened a trade yourself, and you want one of your bots to look after it: its stops, profit targets, timed close and the rest.*

**Before you start**
- The trade: options, up to 4 legs, **opened today**, with nothing closed or rolled yet.
- The bot: on the same account and underlying, **Live** (a Paper bot places nothing), with legs of the same shape (each a buy or a sell, a call or a put, at the same ratio). Its strikes don't have to match. It doesn't need to be armed.
- **No working order on the trade**, such as a stop you placed yourself at your broker. Cancel it first: the app never cancels your orders for you.

1. Open **Journal**. Click the gear (⚙) at the end of the trade's row. A panel slides in from the right.
2. Press **Hand this trade to a bot…**. (It's also under **Automation** on the trade's own page.)
3. The page **Hand to a bot** lists your legs and their fill prices. Pick the bot under **Bot** and press **Check**. This reads your broker and the live prices, and places nothing.
4. Read **What (bot name) would place**. For each of the bot's groups: which of your legs go in it, its **Stop**, what the sold option costs **Now**, its **Profit target**, and what **rests at the broker**. A yellow box under a group says what this trade changes.
5. If it looks right, press **Hand to (bot name)** and confirm. Everything is checked again, then the bot places its stops.

## What you'll see
- The trade gets the bot's badge in your Journal, and its gear now opens the bot's **Manage this trade** boxes.
- From now on the bot manages it as if it had opened it. It counts as one of the bot's open trades, but not as one of its entries for the day.

**If it says no**, nothing is done and the page says why: for example a working order on the trade, a stop that would fire at once because the market has moved, no fresh price (try again in a moment), or a bot whose legs don't match. **Bots that can't take it, and why** lists the others.

More detail: [Handing a trade you opened to a bot](automation.md#handing-a-trade-you-opened-to-a-bot).
