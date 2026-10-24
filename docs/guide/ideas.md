# Ideas

An **idea** is a trade you built on the [Trade](trade.md) page but didn't send. Save it, and the app shows you later how it would have done, with live prices while its options are still trading and the final result once they have expired. Nothing is ever sent to your broker from an idea until you choose to trade it.

## Saving an idea
Build the trade on the Trade page as usual, then press **Idea ☆** on the ticket. A small box opens with:
- **A name,** filled in for you with the symbol and the shape of the trade (for example "SPX iron condor"). Change it if you like.
- **A note,** optional.
- **A summary,** which you can't edit: the legs, the quantity and the entry price.

The **entry price** is the Limit you typed, if you typed one; it shows a 🔒 (locked), meaning it stays exactly as typed. Otherwise it's the live mid price when you save. Press **Save**, and it says **Saved to Ideas**.

An idea holds one trade. Trades with stock legs, VIX options or futures options can't be saved as ideas.

## The Ideas drawer
On the [Journal](journal.md), the violet **💡 Ideas** button shows how many you have. It opens a drawer with one card per idea, newest first:
- The symbol, the name, and a chip: **Live**, **Partly expired** or **Expired**. **Expired · settlement n/a** means the app couldn't read the official closing price, so there's no final result yet; it keeps trying.
- The idea's total P/L in dollars and as a percentage.
- When you saved it, how long ago, and the underlying's price then.
- Your note. The ✎ edits it.
- A table of the trade: **Card** (its name and quantity), **Entry** (🔒 when it's your limit price), **Now** and **P/L**. Prices read like "1.50 cr" (a credit) or "2.00 db" (a debit).

While the drawer is open, prices refresh every few seconds, and **↻** reloads the list now.

### How the P/L is worked out
It's what the trade would have made if you had sent it at the entry price and closed it at today's mid prices: a credit trade gains as the price falls, a debit trade as it rises, times the quantity and the contract size. Once the options have expired, the app uses their value at the official closing price, saves the result once, and never changes it again.

## Trade it
**Trade it** opens the Trade page with the idea's legs, quantity and price on the ticket, the price locked, and a banner: "Idea · name · saved … Trading it removes it from Ideas." It's only on ideas that are still **Live**: an idea with an expired leg can't be traded.

Sending works exactly as it does for any trade (see [Trade](trade.md#send-confirm)). **The idea leaves your list when your broker accepts the order.** If the order is refused, if you only preview it, or if the outcome is unclear, the idea stays. The confirmation says **Removed from Ideas.** when it goes. You can also send the ticket as a bot's entry, and the idea leaves once the bot takes it.

## Deleting an idea
**Delete** on a card asks **Confirm delete** or **Keep**. An idea doesn't delete itself when it expires: it stays so you can see how it ended. Only trading it or deleting it removes it.
