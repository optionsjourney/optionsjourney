# How to send a trade

*Building an options order on the Trade page, having your broker check it, then sending it.*

Example: a put credit spread on SPX, 1 contract, for a `1.00` credit. Your first time, use **1 contract**.

1. Open **Trade** in the top bar.
2. Type the symbol (`SPX`) in the box at the top left and press **Go**. The option chain loads with live bids and asks.
3. Pick an expiration from the row of dates along the top of the chain.
4. Click a **Bid** to sell that option, and an **Ask** to buy one. For the spread: click the bid of the put you want to sell, then the ask of the put below it. Each leg appears in the **Order ticket** on the right. Click the same price again to take a leg out.
5. In the ticket, set each leg's quantity with **−** and **+**. Then check:
   - **Limit (per unit):** it starts at the middle price. Type your own, for example `1.00`.
   - **Credit or debit:** the ticket picks the usual one for your legs.
   - **Account:** which account the order is for.
   - **Time in force:** **Day** ends at the close; **GTC** stays until it fills or you cancel it. On a TastyTrade account, index options also offer **GTC overnight**, which keeps working through the overnight hours.
6. *(Optional)* **Risk graph** slides out a graph of the trade at your limit.
7. Press **Review order**. Your broker checks the order; nothing is sent to the market. You see what it costs (the credit, commission, fees and the buying power it uses), or why it was refused.
8. If the broker accepts it, an amber box shows exactly what will be sent, for example "$1.00 credit · Day" and each leg on its own line. Press **Send order** to send it for real.

## What you'll see
- A green line: "Sent: order #…". The order shows under **Working orders** below the ticket, with **Replace** (a new price) and **Cancel**, until it fills.
- When it fills, the trade comes into your Journal by itself, and a popup says it opened.

**Good to know**
- **Send order** disappears if you change anything on the ticket, and a review is good for 5 minutes and one use. Review again to get it back.
- A ticket that closes one position and opens another (a roll) is refused: "Rolls aren't supported yet: close and open separately."
- If a message says the outcome is **unclear**, look under **Working orders** and at your broker before trying again, so you don't place it twice.

## Having it looked after
Under **Send order** you can choose who looks after the trade once it fills:
- **Manage with:** pick one of your manual bots and press **Send order**. Once it fills, that manual bot's profit target, stop and exit time are attached to the trade. See [Manage with](trade.md#manage-with-a-manual-bots-rules).
- **Or manage it with a bot:** pick a live bot whose legs match and press **Send as (bot name)'s entry**. The bot sends the order instead, chases the price itself and places its own stops and targets. See [Manage with a bot](trade.md#manage-with-a-bot).

More detail: [Trade](trade.md), [Review order](trade.md#review-order), [Working orders](trade.md#working-orders).
