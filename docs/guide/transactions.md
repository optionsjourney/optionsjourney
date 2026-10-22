# Transactions

*Everything the app has read from your brokers, as your brokers sent it.*

Open **Transactions** in the top bar. The [Journal](journal.md) builds your trades from these records. This page shows the records themselves, newest first, so you can check what came in and match it with your broker's own app.

## Sync now
**Sync now** at the top reads your brokers straight away, the same sync the app runs on its own (see [How trades get into the Journal](behind-the-scenes.md#how-trades-get-into-the-journal)). It also refreshes your account values. When it's done, a line for each broker says how many accounts it read and how many records were **new** or **updated**, for example "TastyTrade: 2 accounts, 3 new, 1 updated." A problem, such as a Schwab login that has run out, shows in its place.

**Updated** records are normal. Each sync reads the last 7 days again, because a broker can change a record after the day it happened: TastyTrade, for example, settles its fees the next day.

Your history starts on the day of the app's first sync. Nothing from before that day is brought in, so a trade you already held then shows only its closing here.

## Picking what to see
- **Account:** one account, or **All accounts**.
- **Period:** the last **7 days**, **30 days** (where the page starts), **90 days** or **1 year**.
- **Show:** **Orders** puts the fills that share a broker order number on one row, with the legs listed under the trade's name. **Individual fills** lists every fill your broker reported, one row each.

The list shows 200 rows a page.

## The columns
- **Time:** when it filled, in New York time.
- **Account:** the account's nickname at your broker, and the last digits of its number.
- **Trade** (Orders) or **Instrument** (Individual fills): what was traded, like "SPX Iron Condor" with its legs, or one option such as "SPX 10/09/26 5800 Put". Shares and futures show only the symbol.
- **Action:** for an order, **Open** or **Close**, or what happened instead: **Expiration**, **Assignment**, **Exercise** or **Maturity** (a bond or Treasury bill paid out). For a fill, the full action, like **Sell to Open**.
- **Qty** and **Price**, for each fill.
- **Value:** the cash it moved. Positive is money you received, negative money you paid. A put spread sold for 1.20 on 2 contracts shows +$240.00.
- **Fees:** on their own, never taken out of Value.
- **Broker ID:** the numbers your broker gave these records, handy for finding them in its own app. **Order** is the order number; the other numbers are each fill's own.

## Last sync
At the bottom, one line per account: the time the app last read it through, and **OK** or the problem that stopped the last sync. Check it first if a trade seems to be missing (see [FAQ](faq.md#why-is-a-trade-missing-from-the-journal)).

If some stored records couldn't be read, a note at the top says how many. They're left out of the list and the details are in the app's log.
