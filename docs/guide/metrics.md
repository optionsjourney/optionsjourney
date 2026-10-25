# Metrics: the P/L calendar and your trading numbers

The **Metrics** tab, next to **Journal** at the top, has two views: **P/L Calendar** and **Trading metrics**. Switch between them under the filters.

Both views use the same filters and dashboards as the [journal](journal.md#filters-and-dashboards). A dashboard you saved there, such as "0DTE", is a tab here too, and shows only its trades. **All Time** is where both views start.

## P/L Calendar
The **P/L calendar** shows the month one day at a time, Monday to Friday, with a total for each week. Use **‹** and **›** to move between months.

- **Each day** shows the P/L you made that day and how many trades closed. On a phone the amounts are shortened ($873, -$1.2k, $12k) so they fit; open the day for the exact figure.
  - Green days made money and red days lost it. The stronger the color, the closer the day was to the month's best (or worst) day.
  - Hover over a day for the details.
- **P/L lands on the day it happened.** Say you closed part of a trade on Monday and the rest on Wednesday: each day gets its share. The trade counts as one closed trade on Wednesday, and on Monday it shows as "1 partial".
- **Days are New York days,** the market's own calendar.
- **Weekend amounts:** brokers sometimes book an expiration on the Saturday after. Those amounts are included in the week's total and noted under it.
- **Economic events:** a day with a big release (CPI, a Fed decision, jobs numbers) shows it in red, with its time. A number in amber counts the day's smaller releases. Hover over it to see them all. On a phone they're coloured dots instead: red for a big release, amber for smaller ones.
  - The events come from the same place as the schedule's (see [Automation](automation.md)).
- **Above the grid:** the month's P/L, trades closed, winning days, the average day, and the best and worst day.

### Details for a day, week or month
Click a day, a week's total (the last column), or the month's numbers above the grid. A panel opens on the right with:

- **The totals:** the P/L, how many trades closed (and how many were closed only in part), and how many won and lost.
- **The day's economic events,** when you clicked a day.
- **A table** with a row for each of your dashboards that has trades in that period, and **Other trades** for the rest.
  - There's a column for each account that traded (with its broker under the name), then a total.
  - A trade can be in two dashboards (say "0DTE" and "SPX"). It then shows in both rows, and the **Total** row counts it once. A note says so when that happens.
- **The trades themselves,** under each dashboard's name.
  - Trades with the same name in the same account share one line, such as a bot's many entries in a day. The line shows how many closed (or were closed in part) and their P/L in that period.
  - Click a line to see each of its trades, with when it opened. Click a trade to open it.

A dashboard here picks its trades by its tags, accounts and search. Its saved dates don't apply, because the day, week or month you clicked is the date. The panel's numbers always match the cell you clicked.

Close the panel with **✕**, by clicking outside it, or with **Esc**.

### P/L over time
Under the calendar, two charts show how your P/L has built up.

- **Cumulative P/L** adds up your realized P/L day by day. The label at the end is where you stand now.
- **Drawdown** shows how far you were below your best point so far. Zero means you were at a new high. The deepest point is labelled, and it's also shown at the top of the card.

Move the pointer over either chart (or click it and use the arrow keys) to see the numbers for any day: the running total, that day's P/L, and the drawdown. **Show as a table** lists the same numbers month by month.

## Trading metrics
Click **Trading metrics** under the filters to see the numbers behind your trading.

- **At the top:**
  - **Realized P/L**, split between trades you've closed and partial closes of trades still open.
  - **Win rate:** closed trades that made money, out of all closed trades. A trade that broke even counts as one that didn't win.
  - **Expectancy:** what a closed trade made on average.
  - **Profit factor:** money won divided by money lost. Above 1 means you won more than you lost.
  - **Max drawdown:** your deepest fall from a high point.
- **Closed trades:** average win and loss, how big wins are next to losses, the largest win and loss, and fees (already counted in your P/L).
- **P/L by day, week or month:**
  - A bar for each day, week or month, and an amber line for your **cumulative P/L**: everything added up so far, starting from zero.
  - The line has its own scale on the right. Zero is at the same height on both scales, so a line above the zero line means you're up overall.
  - Hover over a bar for its P/L and the running total.
  - The table under it compares all three: how many were winners, the average, and the best and worst. Weeks start on Monday. On a phone it shows only the one you picked with **Day**, **Week** or **Month**.
- **Day of the week:** how each weekday has gone, and the trades that closed on it. On a phone each weekday is its own small block, so nothing needs scrolling sideways.
