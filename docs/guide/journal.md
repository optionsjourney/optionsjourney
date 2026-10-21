# Your trade journal

The **Journal** is the first thing you see when you open OptionsJourney. It lists every trade you've made: open ones and closed ones, newest first.

## Reading a trade
Each row is one trade, from the moment you opened it until it closed:

- **Trade:** a name based on what you traded, like "SPX Iron Condor" or "TSLA Short Put". Its full history (when it was opened, any partial closes or rolls, and how it ended) is on [the trade's own page](#the-trades-own-page): click the gear (⚙), then **Edit name, tags and notes**.
- **Legs:** what the trade holds, e.g. `-2 777 P 10/12` (2 sold, 777 put, expiring 10/12), each with its live price (`@0.34`). Closing fills are listed underneath in grey with the price they closed at.
- **Status:** **Open**, **Partial** (part of it is closed) or **Closed**. Under it you'll see how it ended if it didn't close normally, e.g. **Expired** or **Assigned**. **Expired, waiting on broker** means the options have expired but your broker hasn't sent the expiration record yet, which can take a day or so. Meanwhile, the P/L column shows an **estimate** (≈ … est.) worked out from the underlying's closing price from your broker. The broker's own record replaces it when it arrives, and estimates are never counted in totals. There's no estimate for monthly SPX options (they settle on the next morning's prices) or for stock options that finished in the money (they turn into shares).
- **Settle expirations at 4:15 PM:** a switch above the trade list, off unless you turn it on. When it's on, at 4:15 PM New York time on trading days the app closes trades whose options expired that day, so 0DTE trades close the same day and count in your P/L straight away. It works with TastyTrade, Schwab, or both: it uses the best price either broker gives at that moment, so one broker being down or logged out doesn't stop it. It first pulls in your latest trades from your brokers, so anything you closed before the bell is counted. It only waits for the accounts that have something expiring, and each account waits on its own. If one broker can't connect (say Schwab's weekly login ran out), the accounts that did sync settle straight away. That account's trades are listed under the switch as waiting for a sync, and the app keeps trying for them until 8 PM. Some trades are left for your broker: monthly SPX options, futures options, stock options that finished in the money, trades with a closing order the app couldn't match, and any where no price was available. The note under the switch lists them, with the reason.
- **Settled at close (prelim):** a trade settled at 4:15 PM at the latest price, before the official closing price is out, says this under its status. *Prelim* means preliminary. The app then corrects it to the official close, usually within hours, or the next morning for index options like SPX, and the label changes to **Settled at close**. If the correction changes the trade's P/L by $1 or more, you get an alert (see [Alerts](alerts.md#unread-alerts)); most trades expire worthless and don't change at all. Your broker's own record, when it arrives the next day, takes over from the app's. If it differs by $1 or more, you get an alert for that too.
- **Qty:** how many spreads (or contracts, or shares) the trade holds.
- **Entry / Exit:** the price per spread. **cr** means you received money (a credit), **db** means you paid (a debit).
- **Premium:** the cash the trade took in (**cr**) or paid (**db**) to open, rolls included.
- **Realized P/L:** what the trade made or lost, after fees, with that as a percentage of the premium. An open trade shows its live open P/L (and percentage) and its P/L Day underneath.
- **Fees:** all the trade's fees, commissions included. Once it has closing fees, the line underneath splits them: opening + closing.
- **Tags:** 0DTE means the options expired the same day you opened them. **Rolled ×2** means you rolled the trade twice. The entry then shows the combined credit, with the original underneath.

## Risk graphs
- The **chart icon** (next to the ⚙ gear) on an open trade opens its profit-and-loss picture in a panel over the journal. **Pop out ⧉** moves it to its own window; **✕** or Escape closes it.
- **Reading it:** the green line is what the trade makes or loses at expiry, at each price of the underlying. The dashed blue line is the same thing today. The thin upright line is where the price is now.
- **Trying "what if":** the **Time** slider moves toward expiry, and the **IV** slider tries a different volatility. **σ** shades the expected move, **Δ** shows the Greeks (delta, gamma, theta, vega), and **⊹** adds price slices that show the P/L at a price you pick.
- **Views:** the switch above the list picks **Cards** (one card per trade: name, P/L, day P/L, when and where; click a card to see its legs and prices), **Table** (every column), **Compact** (one line per trade without the legs, premium and fees; click a row to see them under it), or **Grid** (risk graphs of the open trades side by side). Your browser remembers your choice; on a phone it starts on Cards.
- **Columns:** each trade shows its name and tags first, then **P/L Day** (since the last close, with its % of the premium) and **P/L** (with its % of the premium; open trades show live P/L), then when it opened, the account, the legs still open, and the **closing legs**, grouped by closing order with the time and fill prices.
- **Open and closed:** with both showing, open trades come first under **Open**, then closed ones under **Closed** with their total P/L. Closed trades are grouped by the day they closed, and within a day listed by when they opened, latest first; each day shows how many trades closed and that day's P/L. Click a column heading to sort the whole list by it instead.
- **Several at once:** tick the box at the start of open trades and click **Risk graph together** to see them as one position, for example the put and call sides of an iron condor. Each trade gets a checkbox, so you can take one out and see the rest; trades on different underlyings get a tab each.
- **Stays up to date:** when new fills come in from your broker (a bot's stop filling, a trade you placed), the journal reloads itself within a few seconds. If you have trades ticked, a panel open or are typing, it waits and shows a **Reload** link at the top right instead.
- **Ticking trades:** in the table, click anywhere on a row (or its box) to tick it; in Cards and Compact, use the box. **Select all** and **None** on the Open and Closed headings tick a whole section, and **Select day** ticks one closing day. Ticks stay if you reload the page; **Esc** or **Clear** unticks everything. **Risk graph together** greys out when none of the ticked trades can be graphed (only open option trades can); hover it to see why.
- **Tags for several trades:** tick any trades and use the bar at the bottom: type tags to add or remove (separate them with commas), then click **Save**. Each trade keeps its other tags, notes and name.
- **⊞ Grid** shows the open trades the filters pick in a table, with one risk graph below it of the trades you tick, drawn together:
  - Every trade starts ticked (up to 50, the most one graph takes). Click a row or its box to tick or untick it; the graph redraws a moment later.
  - The box in the table's header ticks them all, or unticks them all if any are ticked.
  - The line above the table adds up the ticked trades: their live open P/L, its % of their premium, and how many are ticked. With more than one account, each account's total is listed too; click one to tick only that account's trades.
  - Click **Total open P/L** to fold the table away and keep just that line. The page remembers it next time.
  - **⤢** opens the graph large.
- **Futures trades** (like /ES options) show their graph at their opening prices; they don't update live yet.

## Grouping trades with tags
Tags are how you group your trades, such as "MEIC", "Wheel" or "earnings". A trade can have as many as you like, and you pick which ones matter by filtering on them. (There used to be "strategies" as well; each one became a tag of the same name, and every trade that was filed under one has that tag.)

Click the gear (⚙) on any trade, then **Edit name, tags and notes** at the bottom of its panel, to:

- **Add tags:** as many as you like, separated by commas, e.g. `MEIC, earnings, A+ setup`. New tags are created for you; a tag that exists is reused, whatever capitals you type.
- **Stock trades** always carry a **Stocks** tag.
- **A bot's trades** always carry the bot's tag (you pick it on the bot's page), along with any tags you add.

**Journal → Tags** lists every tag with how many trades you've given it. There you can **add** a tag, **rename** one (every trade with it shows the new name), or **delete** one (it comes off every trade). A tag a bot puts on its trades can't be deleted while that bot exists: the page says which bot, so give the bot another tag first. You'll find starter tags there already: 0DTE, Credit Spreads, Iron Condors, Calendars and Wheel.
- **A bot's past trades:** to give many trades a bot placed to that bot at once, use **Match trades** on the bot's page or **Match all bots** on the Automation page (see [Finding a bot's past trades](automation.md#finding-a-bots-past-trades)). A matched trade shows **bot ·** and the bot's name, like a trade the bot placed here; clicking it opens the bot's record. Its gear panel has no bot controls, because the bot didn't place it and isn't managing it.

## The trade's own page
Click the gear (⚙) on a trade, then **Edit name, tags and notes**, to open the trade's own page. At the top are its account, status, when it opened and closed, its size, entry and exit prices and fees, then its **History**: the opening, any partial closes or rolls, and how it ended. An open trade also has **Risk graph** and **Alert me when its open P/L goes** above or below an amount (see [Alerts](alerts.md#your-own-alerts)).

Under **Edit** you can:
- **Rename the trade**, if the automatic name doesn't suit you. Leave it blank to go back to the automatic name.
- **Change its tags** (see [Grouping trades with tags](#grouping-trades-with-tags)).
- **Set the close reason:** Profit target, Stop loss, Scheduled close, Manual close, Rolled, Expired, Assigned, Exercised, Matured or Other. **Automatic** uses what your broker's records say, like Expired or Assigned. It only changes the note under the trade's status, never its P/L.
- **Write notes** about why you took it or what you learned.

Click **Save** to keep the changes. The page also has **Manage this trade…** and **Hand this trade to a bot…** when they apply (see [Manage this trade](#manage-this-trade)), the split and combine buttons for an iron condor (see [Iron condors](#iron-condors-one-trade-or-two)), and, for a trade you entered by hand, its entries (see [Adding a trade by hand](#adding-a-trade-by-hand)).

## Stocks
**All trades** lists your options trades only; stock buys and sells are left out. The **Stocks** tab next to it shows **every share position in all your connected broker accounts**, as your brokers report them, not just shares you bought through this app. Long-term holdings show here too:
- **Ticker** and **Qty** (negative if you're short). A ticker held in several accounts shows once, added up. Pick an account to see just that one.
- **Cost basis:** the broker's average price per share, with the total underneath.
- **Current price:** live.
- **P/L:** in dollars and percent.
- **Today's P/L:** measured from the previous close.

It comes from your brokers, not from the journal's trades, so shares bought before your synced history, or transferred in, show with their real cost.

### Your stocks against what you paid
Above the table, a line chart shows your **total profit or loss on shares, day by day, measured against what you paid for them**. The figure above it is the latest day's P/L in dollars and as a percent of what you paid.
- **It follows the account picker.** With no account picked it adds up all your accounts; pick one to see just that account.
- **Ranges:** **1M**, **3M**, **1Y** and **All**. Hover a point to see that day's P/L.
- **How it's built.** The app can't ask your broker for last March's holdings, so it saves them itself. Shortly after each close (4:15 PM New York time, or 1:15 PM on early-close days) it saves every share position and the day's closing price. That's why **the line starts on the day of the first save**, and starts empty. The first point appears after the close of the first day the app is running with a stock position. **Days the app, or your computer, was off are missing** from the line; they aren't filled in afterwards.
- **If your broker doesn't answer,** nothing is saved that day yet. The app waits until **every connected broker** has answered, and keeps trying every 10 minutes. Saving only some of your accounts would make the line jump. If a broker is still not answering at midnight New York time, that day is lost.
- **If a closing price is missing,** the app keeps trying until 8 PM. After that it saves the day anyway, and shares without a price are left out of that day's figure. The chart then says, for example, "2 of 3 priced".
- **The price used** is normally the official close, or else the regular session's last price that day.

### A stock's own chart
Click a ticker in the table to open its chart in a side panel, with the same ranges.
- The line is the stock's **daily price**.
- The **yellow dashed line** is your broker's **average cost per share**.
- **▲** marks a buy and **▼** a sell, from your synced trades. Shares you got through **assignment or exercise** of an option count too.
- Shares you bought **before your first sync**, or that were **transferred in**, have no mark, because the app has no trade for them. Their cost still shows in the yellow dashed line.
- Prices come from Schwab, or from TastyTrade if Schwab can't supply them. If neither can, the panel says so.

## Shares
All the shares you hold of a stock in one account are **one trade**. Each buy, including dividend reinvestments, adds to it, and sales take from it at the average cost. When you've sold them all, the trade closes, and the next purchase starts a new one.

## Iron condors: one trade or two?
An iron condor normally shows as one trade. This works for any trade that sold a put and a call and bought a put and a call in one order, all with the same expiration, even when the sides have different numbers of contracts (an iron butterfly too). Four legs with different expirations, such as a double calendar, can't be split. If you'd rather track the put side and the call side separately, open the trade and click **Split into put and call spreads**. Each half then has its own P/L, notes and tags, and starts with the whole trade's tags (so a trade with a bot's tag stays in its record). You can **Combine** them again at any time.

## Filters and dashboards
Use the filters above the list to narrow it down by tag, account, open or closed, and time period. Click **Apply** to see the result.

- **Tags:** tick one or more; a trade shows if it has any of them. **No tags** shows the trades you haven't tagged. A dashboard you saved with a strategy filter now filters by that strategy's tag, and one saved with "No strategy" by **No tags**.
- **Totals:** the numbers above the list (and on P/L and Trading metrics) always come from the trades shown, each counted once. A trade with three tags still counts once, even if you pick all three.

The trade list opens on your **open** trades plus the ones **closed today** (New York date), and a line above the summary says so, since its numbers are then for those trades only. Choose **Open and closed**, **Open** or **Closed** in the status filter (or click **Show open and closed**) to see the rest. **P/L** and **Trading metrics** open on all your trades. A status you choose yourself stays as you move between Trades, P/L and Trading metrics, and a dashboard keeps the status it was saved with.

- **Search:** type words that appear in a trade's name, symbol, tags or notes (`hood`, `earnings`). Type `#` and a broker order number (`#123456`) to find that exact order's trade.
- **Opened … to …:** only trades opened between two dates. Leave either side empty for no limit.

**Sorting:** click a column heading in the trade list (Opened, Trade, Qty, Realized P/L …) to sort by it, and click again to reverse. Trades with nothing in that column, like the exit of an open trade, go last. Changing the filters goes back to newest first. When trades open in the same minute, sorting by when they opened keeps each entry's sides together: a bot's put side and call side in one account stay next to each other, even if another account opened its own sides a few seconds in between.

To keep a set of filters, click **＋ Save view** and give it a name. It becomes a **dashboard tab** you can come back to with one click, for example "0DTE" or "Wheel". Each dashboard shows its own summary above the trades.

### The summary above the trades

For the trades the filters show:

- **Closed P/L:** what your trades have realized. A trade you've rolled and still hold isn't counted until it's closed. On **Open + closed today**, an open trade counts only what it closed today.
- **Open Premium** and **Premium:** the cash your option trades took in (or paid) to open, counting rolls. **Open Premium** is for what's still open; **Premium** is for what's closed, including the closed part of a partly closed trade.
- **Per lot:** the average trade, average winner and loser, and the biggest winner and loser, each per lot (a 2-lot spread counts as 2). These count closed trades only, plus the closed part of partly closed ones; open trades aren't counted until they close.
- **Trades** and **Winners:** Trades counts every trade shown, open or closed. Winners counts closed trades that made money.
- **Win Rate:** winners out of closed trades. Open trades aren't counted until they close.
- **Capture Rate:** how much of the premium you kept (Closed P/L ÷ Premium). Like Win Rate it leaves open trades out: only closed trades, and the closed part of partly closed ones, count.

**Open P/L** is live: what your open trades would make or lose if you closed them now, at your broker's current prices, updated every few seconds. The green dot means prices are streaming. Each open trade shows its own open P/L in the list too. Futures options are priced from TastyTrade.

After the market closes and before it opens, brokers often pull the bid on index options such as SPX weeklies (a quote like 0.00 × 20.40). Pricing those at the middle of the bid and ask would make your P/L jump around all evening for no reason. So outside market hours, an option with no real market is priced at its last real price from the session instead. A real overnight quote is still used as it is.

**P/L Day** is how much your open trades made or lost today, in total and on each trade. On each trade it also shows that as a percentage of the trade's premium, the same way open P/L does ("+$9.00 (+4.5%)"):
- **The baseline:** each trade is measured from its value after the previous trading day's close. OptionsJourney notes that value 15 minutes after each close while it's running. A trade opened today counts all of its P/L, and one closed today counts its close against last night.
- **Without that note** (the first day, or the app was off at the close), it's worked out from TastyTrade's closing prices instead, or Schwab's during the trading day if TastyTrade doesn't answer. A price that couldn't be read is tried again every 2 minutes. The box then says "≈ partly from the closes before".
- **At the weekend or on a holiday** it shows the last trading day's change, labelled with that day ("Fri 10/9").

To change a dashboard, adjust the filters and click **Save to "…"**. To remove it, click **Delete dashboard**. Deleting a dashboard never touches your trades.

## The P/L view: calendar and charts
Click **P/L** under the filters. Like **Trading metrics**, it uses the same filters and dashboards as the trade list.

### The P/L calendar
The **P/L calendar** shows the month one day at a time, Monday to Friday, with a total for each week. Use **‹** and **›** to move between months. It follows the filters, so on a dashboard it shows only that dashboard's trades.

- **Each day** shows the P/L you made that day and how many trades closed. Green days made money and red days lost it; the stronger the color, the closer the day was to the month's best (or worst) day. Hover over a day for the details.
- **P/L lands on the day it happened.** If you closed part of a trade on Monday and the rest on Wednesday, each day gets its share. The trade itself counts as one closed trade, on Wednesday; on Monday it shows as "1 partial".
- **Days are New York days,** the market's own calendar.
- **Weekend amounts:** brokers sometimes book an expiration on the Saturday after. Those amounts are included in the week's total and noted under it.
- **Above the grid:** the month's P/L, trades closed, winning days, the average day, and the best and worst day.

### P/L over time
Under the calendar, two charts show how your P/L has built up. Like everything else on the page, they follow the filters.

- **Cumulative P/L** adds up your realized P/L day by day. The label at the end is where you stand now.
- **Drawdown** shows how far you were below your best point so far. Zero means you were at a new high. The deepest point is labelled, and it's also shown at the top of the card.

Move the pointer over either chart (or click it and use the arrow keys) to see the numbers for any day: the running total, that day's P/L, and the drawdown. **Show as a table** lists the same numbers month by month.

## Trading metrics
Click **Trading metrics** under the filters (next to **Trades** and **P/L**) to see the numbers behind your trading. It uses the same filters and dashboards as the trade list, and **Trades** takes you back.

- **At the top:**
  - **Realized P/L**, split between trades you've closed and partial closes of trades still open.
  - **Win rate:** closed trades that made money, out of all closed trades. A trade that broke even counts as one that didn't win.
  - **Expectancy:** what a closed trade made on average.
  - **Profit factor:** money won divided by money lost. Above 1 means you won more than you lost.
  - **Max drawdown:** your deepest fall from a high point.
- **Closed trades:** average win and loss, how big wins are next to losses, the largest win and loss, and fees (already counted in your P/L).
- **P/L by day, week or month:** a bar for each one, with a table comparing all three: how many were winners, the average, and the best and worst. Weeks start on Monday.
- **Day of the week:** how each weekday has gone, and the trades that closed on it.

## The gear: manage a trade without leaving the journal
Every trade in the journal has a small gear (⚙) at the end of its row. In the Compact view it shows when you point at the row or open it. Click it and a panel slides in from the right, over the journal. Close it with **✕**, the **Esc** key, or a click on the dimmed journal beside it. On a phone the panel fills the screen.

What the panel shows depends on the trade:
- **A bot's trade:** "Manage: " and the bot's name. If a close is working right now, it says so at the top, with **Cancel**. Then what the bot paid or took in, the fill price and the broker's order number; every order, step by step; a box for each side with its open contracts, stop and target at the top, then Stop loss and Profit target side by side, and a red **Close** for that side (the same as on the bot's run page); and this trade's own timed close and watch rules. A bar along the bottom stays in view as you scroll: the risk graph (to plan a roll), **Open full page** for the run page, and the red **Close entire trade** button.
- **One side of a split iron condor:** only that side: its groups, its exits' steps, and **Close this side** instead of Close entire trade. The other side is its own row with its own gear.
- **A trade with rules from Manage this trade:** its rules, with Change and Remove (see below).
- **Any other open options trade:** a manual bot or custom rules to give it, and **Hand this trade to a bot…** when that's possible.
- **A closed trade:** a short summary to look at. Nothing can be changed.
- **A trade you matched to a bot** (with Match trades, so no bot placed it), open or closed: **Unlink from** and the bot's name. It asks first, then takes the trade out of that bot's record and takes off the bot's tag. If the trade took the bot's name, it gets its own back. The trade's page has the same button.

Every panel links to the trade's page, for its name, tags and notes. When you save something in the panel, the journal reloads with the panel open again and the result shown at its top.

## Manage this trade
Any open options trade that no bot is looking after has a **Manage this trade…** button on its page, however long ago you opened it. It lets the app watch the trade's exits for you.

- **Pick a manual bot** (you make those on the Automation page) and click **Use these rules**, or
- **Set custom rules** for this trade only: a profit target, a stop loss (each a % of what you took in or paid, or dollars), an exit time and alerts. Custom rules aren't saved as a manual bot.

The rules are copied onto the trade, so editing the manual bot later doesn't change them. The app then places the stop and target at your broker through the same safety checks as every bot order: preview, the size limits and **Freeze**. A trade bigger than 100 contracts, or on an underlying the app can't trade, can't be managed this way.

A futures trade (like /ES options) keeps only the profit target: its stop loss, exit time and alerts aren't placed, and the app tells you so.

A trade with legs on more than one underlying can't be managed this way.

Once a trade has rules, its **gear** in the journal opens them again (the panel's **Open full page** opens this page). There you can:
- **Change its rules**: pick another manual bot or type new custom rules. The old stop and target are cancelled and the new ones placed.
- **Remove its rules**: the stop and target are cancelled at your broker and nothing manages the trade any more. You can give it rules again later.

While a close is working on the trade you'll see "A close is working on this trade. You can change its rules once it finishes." Wait for it to fill or give up, then try again.

**Assignment.** Options on stocks and ETFs like SPY can be assigned, which turns them into shares. If such a trade has no exit time, the page says, for example, "SPY options can be assigned. This has no exit time." One click on **Add an exit time** closes it on its expiration day at 15:45 New York time. It's only a suggestion. Cash-settled options such as SPX and XSP don't show it.

## Adding a trade by hand
Trades in your TastyTrade and Schwab accounts come in by themselves. For anything else, such as a trade at another broker, click **＋ Add a trade** at the top of the journal.

1. **Choose the account.** The first time, pick **New account…** and give it a name, like "Fidelity IRA". After that it's in the list.
2. **Opens a position** or **Closes one**, then the date, the time (New York time) and the total fees.
3. **One row per leg:** put, call or shares; buy or sell; quantity; the underlying (like SPX); strike and expiration for options; and the price. Option prices are per share, so 2.00 means $200 a contract. Leave rows you don't need empty.

The trade then shows up in the journal, the P/L view and the metrics like any other trade.

On a hand-entered trade's page, you can:
- **Close…** opens the form with the closing legs filled in. Just add the prices.
- **Mark expired** closes what's left at 0, as expired worthless, at the expiration day's close. It works once the market has closed on that day.
- **Delete** removes an entry you made. Deleting the opening entry removes the trade.

Trades from your synced accounts can't be added or deleted by hand: your broker's records are the source of truth.

## Paper trades
A bot set to **Paper** makes pretend trades. They never appear in the Journal and never change its numbers. They have a view of their own inside the Journal: **Paper trades**, next to Trades, P/L and Trading metrics.

- Each row is one pretend trade: when it opened and closed, what it took in, what closing cost, what closed it (a stop, target, timed close, expiry...) and its P/L.
- If the bot splits its iron condors (as MEIC bots do), each pretend iron condor shows as two rows, its **Put side** and its **Call side**, the same way the Journal splits real ones. Each side has its own stop, its own close and its own P/L.
- **Closed by** names what closed the trade: a side that was stopped says **stop**, even though its cheap long leg was then left to expire at the close.
- Click the bot's name to open the run. Its **Fills** table lists every pretend fill leg by leg: the time, what it was (the entry, a stop, a target, the expiry...), which leg, buy or sell, how many contracts, the price and the dollars (money in green, money out red). A leg that expires is priced at what it was in the money at the close, so a worthless wing shows 0.
- During market hours it works like the Journal. An open trade (or side) shows its P/L now, priced live: what it took in, what its closes cost so far, and what its legs still held would cost to close at their mid prices. The **Open P/L** box adds them up. The page also reloads by itself when a pretend fill comes in (or shows a Reload link if you're in the middle of something).
- A trade or side counts as **closed** as soon as its sold legs are gone. When a stop buys back a sold leg, the cheap bought wing is left to expire, and the row says **wing held**. The wing counts as worth 0 until it's sold or expires. If it expires in the money, the P/L takes that in.
- The boxes at the top are **Open P/L**, **Paper P/L (closed)**, **Win rate**, **Winners** and **Losers**. All but Open P/L count only closed trades.
- You can narrow the list to one bot, to one tag, and to the dates trades opened. Picking a tag shows the trades of every bot that carries it: the tag a bot files its trades under, or one of the bot's own tags from the Automation page (capitals don't matter). The boxes at the top count only the trades shown.
- Paper trades are pretend. Fills are estimated at the mid price, and stops are watched by the app instead of resting at the broker, so real results would differ.

On the Automation list, a Paper bot shows its paper P/L with a **paper** tag. Live bots are unchanged.

## Broker records not placed in a trade
If a folded box reading, for example, **2 broker records not placed in a trade** appears at the bottom, click it to open it. It lists records from your broker the app couldn't put into a trade. Usually they're closes of trades opened before the app's history starts (on the day of its first sync, so a trade you already held then has no opening here), or broker events such as a stock split. It's nothing to worry about.
