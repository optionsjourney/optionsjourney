# Trade

**Trade** is where you build an options trade and ask your broker to check it. It replaced the old Analyze page; old Analyze links and bookmarks now open Trade.

**Review order** checks the order with your broker and sends nothing. If the broker accepts it, an amber **Send order** button appears. Pressing it sends the order for real.

## Picking a symbol
Type a stock, ETF or index (SPX, SPY, AAPL …) in the ticker box. The option chain loads with live bids and asks, and the row nearest the current price is outlined. Pick an expiration along the top to see its strikes.

The chain comes from the broker of the account on the ticket, and its header says which ("Chain: Schwab" or "Chain: TastyTrade"). If that broker can't give it, the other one does, and the note turns amber. Futures options always come from TastyTrade: Schwab doesn't offer them. Schwab doesn't give open interest for SPX; when a chain has no open interest, that column is hidden.

## What you already hold
The **Pos** column, next to the delta on each side, shows the contracts you already hold at that strike, from your open trades in the Journal. A short position shows as a minus number (**-2** means two sold), a long one with a plus (**+1**).
- **Each account has its own colour.** If you hold the same strike in two accounts, you see two coloured boxes. Hover over them to see the account names.
- **Positions menu:** when you hold something on this symbol, a **Positions** menu appears at the top of the chain. It starts on **All accounts**; pick one account to see only what it holds.
- **Dots on the expirations:** an expiration where you hold something has a small coloured dot for each account holding it, so you can find your trades without clicking through every date.
- It checks again every 30 seconds, so a new trade shows up once the Journal has synced it. On a phone, the column shows only when you hold something on the symbol.

## Building the trade
- **Click a bid** to sell that option. **Click an ask** to buy it. The legs appear in the ticket.
- Click the same bid or ask again to take that leg out.
- Change a leg's quantity in the ticket. The trade is sized by the legs you set, with no separate quantity box.

## The ticket
Across the top of the ticket, in large type, is the trade's **price for one unit**: green with **cr** if you're paid (a credit), red with **db** if you pay (a debit). It follows the live prices.
- **×2, ×3 …** If every leg has the same multiple (for example 3 of each), the price is for one of each, and **×3** says how many units the ticket holds. This is also how the **Limit** is priced.
- **Nat** (natural) is the price if you sold at the bid and bought at the ask: the price you'd most likely get filled at right away. **Opp** (opposite) is the other way round: the best you could hope for. The mid price sits between them.
- Next to it: **Delta**, **POP** (chance of any profit at expiration), **P50** (chance of reaching half the maximum profit), **Max Profit** and **Max Loss**.
- **BP Eff.** (buying power the trade uses; a minus number frees some), **BP After** and **Fees** (commission plus fees) fill in after **Review order**, from your broker's answer, and go back to a dash when you change the ticket. Schwab doesn't give the buying power effect.

- **Limit:** the price you're willing to trade at. It starts at the **mid price**, halfway between what you'd get and what you'd pay across all the legs. Type your own to change it. If a leg has no quote, the limit starts empty and you need to type one.
- **Credit or debit:** whether you're being paid for the trade (credit) or paying for it (debit). The ticket picks the usual one for your legs, and you can switch.
- **Account:** which of your accounts the order is for.
- **Time in force:** how long the order lasts. **Day** ends at the close; **GTC** stays until it fills or you cancel it. **GTC overnight** also works during the overnight hours of index options (SPX, XSP, VIX, RUT). It's offered only on a TastyTrade account and only with a limit price: Schwab doesn't take overnight orders (see [TastyTrade or Schwab](setup.md#tastytrade-or-schwab-whats-different)).

## The risk graph
**Risk graph** slides a drawer out from the right with the graph. Close it with **Close ×**, the Escape key, or **Risk graph** again. The green line is what the trade makes or loses at expiration, and the orange line is what it would make or lose today. The graph uses your limit as the price of the trade. Changing the limit or a quantity redraws it, and keeps the sliders where you put them. See [Your trade journal](journal.md) for how the same graph works on trades you already hold.

## Review order
**Review order** sends the ticket to your broker to check, and shows what the broker says (for example, how much buying power the trade would use). Nothing is sent to the market. The check shows in the [order log](pausing-trading.md) as a **preview**.

Under the broker's answer is what the order costs, as your broker works it out:
- **Trade:** the credit you take in or the debit you pay.
- **Commission** and **Fees**, then **Net**: what the trade comes to after them.
- **Buying power effect:** how much buying power the order uses (TastyTrade gives this; Schwab doesn't).
- **Buying power after:** what's left once it's placed. In red if it would go below zero.

You see these even when the broker refuses the order, so you can tell why (for example, buying power after below zero).

You'll be told plainly if the order is refused:
- **Too big:** a leg is over your limit (see below).
- **A roll:** a ticket that closes some positions and opens others. Close and open separately.
- **Closing more than you hold.**
- **A missing or zero price.**
- **No new trades** or **Freeze all orders** is on (see [Pausing trading](pausing-trading.md)).

## Send (Confirm)
After a good review, a box spells out exactly what will be sent: the price, credit or debit and time in force ("$5.20 credit · Day"), then each leg on its own line (for example "Sell 1 SPXW 16 Oct 7775P" and "Buy 1 SPXW 16 Oct 7755P"). Under it is an amber **Send order** button, with a note that this sends a live order to your broker.
- **It only appears after Review order.** It sends the order that was reviewed, not whatever is on the ticket now.
- **It disappears if you change anything** (a leg, the limit, the account, credit or debit, time in force), or press **Clear**. Review again to get it back.
- **A review is good for 5 minutes and one use.** If it ran out, or you already pressed Send, you'll see "That review has already been used or has expired. Review again."
- **Your first time,** start with **1 contract**.

### When the order doesn't go out
The message under **Send** says what happened. Two kinds of message mean nothing was sent:
- **Stopped before sending.** The app or your broker's check stopped it, so the broker never got the order. For example:
  - "Your positions changed since the review. Review again."
  - "That review has already been used or has expired. Review again."
  - "No new trades is on…", "Orders are frozen…", or "… is over your limit of …" (see [Pausing trading](pausing-trading.md) and [Your order limit](#your-order-limit)).
  - "The broker's check refused it: …", followed by your broker's reason.
- **Sent, and the broker turned it down.** The message starts with **Not sent:** and gives your broker's reason.

Either way, fix the cause and review again. If a message says the outcome is **unclear**, or you aren't sure whether the order went out, look in **Working orders** below and at your broker before trying again, so you don't place it twice. The [order log](pausing-trading.md) shows every attempt.

## Manage with (a manual bot's rules)
Under **Send** there's a **Manage with** choice. It starts on **Place only**: Send then sends your order and nothing else, as always.

Pick one of your manual bots instead, and Send still sends exactly the same order. The difference comes after: once the order fills, the app attaches that manual bot's rules (its profit target, stop loss, exit time and alerts) to the new trade in your Journal, and places the exit orders for you.

- **The rules are copied when you press Send.** Editing the manual bot afterwards doesn't change what this order gets.
- **A partial fill gets the rules too,** for the part that filled. The rest is protected once the order finishes (fills completely, is cancelled or expires): then the exits are replaced with ones for the whole trade. There's a short moment while they're re-placed when the trade has no exit orders.
- **If the order is cancelled, rejected or expires before anything fills,** nothing is attached.
- **Closing the app is fine.** The app remembers, and attaches the rules when it next sees the fill. It reads fills from your Journal, so they show up once the Journal has synced them.
- **If the app stops right after you press Send,** check the trade in the Journal: if it has no rules, use **Manage this trade** there.
- **If the order is over but its fill doesn't show in your Journal within about a day, or the app can't read the order at your broker,** the app stops waiting and sends you an alert.
- **If the rules can't be attached** (for example the trade is bigger than a bot can manage), you get an alert and the trade stays open without them. You can still use **Manage this trade** in the Journal.

## Manage with a bot
After a good review, a box **Or manage it with a bot** appears under **Send**. It lists your live bots on this account whose legs match the ticket. Pick one and press **Send as (bot name)'s entry**: instead of your order, the bot sends the ticket as its own entry, chases the price with its own settings and places its stops and targets on the fill. Pressing twice sends once. See [Sending a trade from the Trade page as a bot's entry](automation.md#sending-a-trade-from-the-trade-page-as-a-bots-entry).

## Working orders

The list shows working orders on all your accounts. When more than one account has some, a menu next to the heading picks **All accounts** or one account (each with its count), and each order names its account. Ten orders show at a time; **Show more** adds the next ten. Each order shows its price, credit or debit, and its status, then each leg on its own line (for example "Sell 1 SPXW 23 Oct 7645C").
The list under the ticket shows the account's orders still waiting at the broker.
- **Orders you sent from this page** have **Replace** (type a new price and press it; the legs and quantity stay the same, and a credit stays a credit) and **Cancel**.
- **A partly filled order can only be cancelled.** Replacing it would send the full size again, so Replace isn't offered. Cancel it, then review a new order for the rest.
- **If your broker didn't give back an order number when you sent it,** the order shows here without Replace or Cancel. Cancel it at your broker.
- **Orders placed by a bot, or at your broker, are shown but can't be changed here.** That keeps a bot's exits from being broken by accident. Change those where they came from, or pause the bot (see [Pausing trading](pausing-trading.md)).

## Alerts
**Add alert** sets an alert on the price of the spread you built, for example "tell me when it goes below 2.50". It shows on the [Alerts](alerts.md) page, where you can delete it, and goes to Discord or Telegram if you've set those up.

## Your order limit
In [Settings](settings.md) under **Your orders**, **Largest order you can place** is the most contracts you can put in one leg (10 to start). It's a safety net against a typing mistake, such as an extra zero.

## Futures
You can look at a futures chain (/ES, /GC …), graph a trade and **Review order** it: TastyTrade checks it, priced on that futures option's own price steps. Futures need TastyTrade: Schwab doesn't offer them.

A reviewed futures ticket can only be sent **as a futures bot's entry** for now, under **Or manage it with a bot**: there's no plain **Send** for it yet. The bot rests your limit at TastyTrade and, once it fills, rests a profit target. **Futures: profit target only, no stop.** See [Futures-options bots](automation.md#futures-options-bots).
