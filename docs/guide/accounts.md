# Your accounts

The **Accounts** page lists every account the app is connected to, with its value, cash and buying power, plus a total across all of them.

The page opens straight away with the balances the app last read, then checks your brokers and updates them, usually within a couple of seconds. Next to each broker, **As of** shows when its balances were read. If a broker can't be reached, you keep seeing the last balances it gave, with a note saying why they weren't updated.

A broker you haven't connected (in **Settings → Brokers**) isn't shown here, and the app doesn't try to reach it anywhere else either. If your Schwab login has run out (every 7 days), Schwab still shows, with a note to log in again.

## Account values over time
Click **Account values over time →** next to the page title to see how your accounts' value has changed.

- **The chart** shows the value of all your accounts together, or of the one you pick, day by day. A hollow dot marks a day money moved in or out.
- **Value now** and **At the start** are the first and last values in the period you chose: 90 days, 1 year, or everything recorded.
- **Money in / out** is deposits less withdrawals over the period, including transfers between accounts.
- **Gain** is how much the value changed, not counting money you moved in or out. If an account starts being counted partway through, its starting value isn't counted as a gain either.
- **The table** shows the same thing month by month.

### Where the values come from
- **TastyTrade** keeps a value for every day. The first time you sync, the app brings in the last year, and after that each sync adds the new days.
- **Schwab** doesn't keep a history. The app writes down your Schwab balance each day it syncs or the Accounts page updates, starting from the first time. Days the app isn't running are left out, and the last recorded value stands in for them.

To bring values up to date, click **Sync now** on the Transactions page.
