# Troubleshooting

*When something doesn't look right: the likely reasons, and what to check.*

Each answer starts with the most common reason. Many come down to the app not running at the time: see [When the app or computer is off](behind-the-scenes.md#when-the-app-or-computer-is-off).

## The app won't open or isn't working
- **There's no OJ icon** in the system tray (Windows) or the menu bar (Mac): the app isn't running. Open **OptionsJourney** from the Start menu (Windows), or from Spotlight (⌘ Space) or Launchpad (Mac).
- **The icon is there, but the page won't load:** choose **Restart OptionsJourney** in the OJ menu, wait a few seconds, then choose **Open OptionsJourney**.
- **A page says "Something went wrong":** see [below](#a-page-says-something-went-wrong).
- **Finding the logs:** choose **Open logs** in the OJ menu. If there's no menu, the `logs` folder is inside your data folder: `AppData\Local\OptionsJourney` on Windows, `Library/Application Support/OptionsJourney` on a Mac (see [Where things are](installing.md#where-things-are)).
- **Reinstalling:** run the installer again, as in [Installing](installing.md). Your trades, bots and settings are kept, because they're stored separately from the app.
- **Still stuck?** Send `optionsjourney.log` from the logs folder to [hello@optionsjourney.com](mailto:hello@optionsjourney.com), with a line on what happened.

## Why is a trade missing from the Journal?
**The likely reasons:**
- **It hasn't been read yet.** The sync after a fill takes a few seconds, and if your broker's fill notice doesn't arrive, the trade waits for the next timed sync: every minute in market hours by default, every hour at other times (see [How trades get into the Journal](behind-the-scenes.md#how-trades-get-into-the-journal)).
- **The sync didn't work.** A broker that can't be reached, or a Schwab login that has run out, stops that broker's trades coming in.
- **The filters hide it.** The trade list opens on your open trades and those closed today. A closed trade from another day shows once you pick **Open and closed** or **Closed**. A dashboard, a tag, an account, a period or a search can hide it too.
- **It's a stock trade.** **All trades** lists options only; shares are on the **Stocks** tab.
- **It's a Paper bot's trade.** Pretend trades are only in the Journal's **Paper trades** view (see [Paper trades](journal.md#paper-trades)).
- **It's from before the app's history starts.** History starts on the day of the app's first sync. A trade you already held then has no opening here, so its close is listed in the folded box **… broker records not placed in a trade** at the bottom of the Journal: click it to open it.
- **It's at another broker.** Only TastyTrade and Schwab come in by themselves. Add other trades with **＋ Add a trade** (see [Adding a trade by hand](journal.md#adding-a-trade-by-hand)).

**What to check:** press **Sync now** at the top of the Journal. Then open **Transactions**: if the trade is listed there, the app has it, and it's the Journal's filters. If it isn't, look at **Last sync** at the bottom of that page for each account's last good sync and the problem, if any (see [Transactions](transactions.md#last-sync)).

## Why didn't a bot enter?
**Where to look first:** the bot's **Runs** page (on the bot's page), where each entry time has a run with its **Status** and **Reason**. The **Today** timeline on the Automation page shows today's entries too, and **📅 Schedule** shows what each armed bot does on any day (see [Today's schedule](automation.md#todays-schedule)).

**No run at all** means the bot didn't try that day:
- **It isn't armed**, or it's archived. Only armed bots enter (see [Arming](automation.md#arming)).
- **It isn't one of its days:** the market is shut (a weekend or holiday), or the bot's **How often** leaves that day out (see [How often](automation.md#how-often)).
- **It's a futures bot.** These enter only from the Trade page.
- **It uses an entry window** and no minute passed yet. A minute that doesn't pass leaves no run; the bot's page shows the last check and why (see [Entry window](automation.md#entry-window)).

**A skipped run** says why in its Reason. The bot checks these in this order, and stops at the first one that says no:

| The Reason says | What it means |
|---|---|
| Missed its time: the app was closed or busy. | The app wasn't running (or the computer was asleep) at the entry time, and it was more than 3 minutes late. An entry is never sent late. |
| Orders are frozen. / No new trades is on. | A safety switch is on (see [Pausing trading](pausing-trading.md)). |
| The market isn't open. / Too close to the close | Entries only go out in regular hours, and stop 2 minutes before the close. |
| today is a blackout day | One of the bot's blackout days, a **Skip a day**, or a day on a list it blacks out (see [Blackout days](automation.md#blackout-days-and-skip-a-day)). |
| … dates unavailable today, so the blackout can't be checked. | The app couldn't read a date list the bot blacks out, so it doesn't enter in case today is one of them (see [Date lists](automation.md#date-lists-fomc-cpi-end-of-month-and-market-holidays)). |
| … has reached its limit of … a day. | **Max contracts per day** is used up. |
| already at … entries today / already holding … open trades | **Max entries per day** or **Max open trades** (see [Limits](automation.md#limits-on-entries-and-open-trades)). |
| VIX level filter: … (or another filter) | A market filter wasn't met, or its prices couldn't be read (see [Market filters](automation.md#market-filters)). |

**After those checks** the bot picks its strikes and prices the trade, and a run can still be skipped then: no strike that fits, deltas or the SPX price not available, a premium outside its bounds, a price too close to even, or a budget that doesn't buy one contract. Each says so in its Reason, and the ones about missing prices also send you one alert that day. A Paper bot's run is also skipped if your broker's check of the order would refuse it.

**Missed** means the order went out and was chased but never filled, so there's no trade (see [What happens at an entry time](automation.md#what-happens-at-an-entry-time)).

## What does "Needs a look" mean?
While a Live bot has a trade open, the app checks every 30 seconds that each group is protected. Anything it finds shows in **Needs a look** at the top of the Automation page, with a link to its run, and sends you one alert. The trade's **bot** badge in your journal turns red too. It goes by itself once it's sorted. The app never changes an order because of it.

| It says | What it means | What to do |
|---|---|---|
| …'s stop filled, but nothing was done with its wing. | A minute after a stop filled, the group's other legs were neither sold nor left open on purpose. | Look at your broker: you may still hold the wing. Sell it there or keep it. |
| …'s stop shows as working but has no order number | The app can't follow that stop, so it wouldn't see it fill. | Check the stop at your broker. |
| … has no working stop. | The group has had no stop at your broker, and no stop the app watches, for 2 minutes, for example a stop that was cancelled or refused and not put back. | Open the run and use **Check** and **Restore missing exits**, or set a stop with **Change stop** (see [Managing a trade](automation.md#managing-a-trade)). |
| you switched its stop off / you turned its exits off | You did this on the run page, so nothing protects that group from a loss. | Switch it back on from the run page, or close the group. |
| …'s nearer legs expired; … still held | A calendar's near legs expired before its timed close closed it. The far legs are yours now. | Sell them or keep them at your broker (see [Calendars](automation.md#calendars-diagonals-and-trades-held-overnight)). |

A run whose Reason starts **Needs you** is different: it's one the app couldn't settle by itself (its entry order wouldn't cancel, say), or a filled trade whose exits may be missing. Check your broker; an order the app marked **Unclear** is explained in [Unclear: check your broker](pausing-trading.md#unclear-check-your-broker).

## Why does Schwab ask me to log in?
**The reason:** Schwab makes every app log in again every 7 days. The app reminds you with an alert at 48 hours left and at 12 hours left, and again once the login has run out (see [Broker logins](behind-the-scenes.md#broker-logins)). A banner shows on every page from a day before, and **Settings → Brokers** shows **Log in again soon**, then **Log in again**.

Logging in to Schwab from this app on another computer, or from another program that uses the same Schwab app, also signs this one out (see [Schwab: one computer at a time](setup.md#schwab-one-computer-at-a-time)).

**What to do:** click **Reconnect Schwab** in **Settings → Brokers** and log in. Until you do, Schwab accounts don't sync, and a Schwab bot can't send anything: stops and targets already resting at Schwab keep working, but a stop the app watches can't close a trade (see [Schwab bots](automation.md#schwab-bots)).

## Why does P/L Day show "≈"?
**The reason:** P/L Day measures each open trade from the value the app saved shortly after the last close (4:15 PM New York time). If it has no saved value for a trade, because the app wasn't running then or it's your first day, it works from your broker's previous closing prices instead, which is close but not exact. The **≈** says part of the figure came that way ("≈ partly from the closes before").

**What to do:** nothing. The next close the app is running for, it saves the value and the **≈** goes. To avoid it, keep the app running through 4:15 PM. See [P/L Day](behind-the-scenes.md#pl-day).

## Why didn't an exit fill before the close?
**The likely reasons:**
- **The close ran out of time.** A timed close, or a close you started, never chases past the market's close. When the market closes it stops, cancels its order and puts the stop back. On expiry day the options then simply expire (see [Timed close](automation.md#timed-close)).
- **The app wasn't running at the close time, or orders were frozen.** That side shows **missed** on the run page and you get one alert.
- **It ran out of tries.** Then the bot did what its **If a close hasn't filled after the last try** says (see [If a close hasn't filled](automation.md#if-a-close-hasnt-filled-after-the-last-try)). After the close it never goes to the natural price or legs out: it puts the stop back instead.
- **A timed close is tried once a day per side.** If it didn't go through, it isn't tried again that day.

**What to check:** open the run page. **Orders, step by step** shows every price the close tried, and **Exits** shows where each stop and target stands now. If something is still open that you wanted closed, close it at your broker or from the run page the next trading day.

## Why is nothing syncing?
**The likely reasons:**
- **The app isn't running,** or the computer is asleep. Nothing syncs then; trades filled meanwhile are read at the first sync after it starts.
- **A broker can't be read:** Schwab's login has run out, or the broker didn't answer.
- **The timed sync is off** in **Settings → Syncing your trades** (see [Settings](settings.md#syncing-your-trades)).

**What to check:** press **Sync now** in the Journal: the line it shows says how each broker did. **Transactions → Last sync** shows each account's last good sync and the problem that stopped the last one.

## A page says "Something went wrong"
**The reason:** the app hit an error it didn't expect. Your data is safe, and nothing is sent to your broker because of it.

**What to do:** go back and try again. If it keeps happening, choose **Restart OptionsJourney** in the OJ menu. Still stuck? Choose **Open logs** in the same menu and send `optionsjourney.log` to [hello@optionsjourney.com](mailto:hello@optionsjourney.com), with the line the error page shows.
