# Automation

*Bots that open a trade for you at set times and then look after it.*

A **bot** is a saved trade the app opens for you at times you choose, or at the first good minute of a window you choose. A bot trades options on **SPX**, **XSP**, or a **stock or ETF** such as SPY or AAPL (see [Which underlying](#which-underlying)), most often ones that expire the same day ("0DTE"): an iron condor, a credit or debit spread, a single option bought or sold, a butterfly, a straddle or a strangle, or a shape of your own with up to 4 legs. See [Shapes a bot can trade](#shapes-a-bot-can-trade). Trades held overnight and legs in other expirations come later.

**TastyTrade or Schwab.** A bot trades a TastyTrade or a Schwab account. A Schwab bot's exits work the same way as a TastyTrade bot's (see [Schwab bots](#schwab-bots)).

**Examples below use made-up round numbers.** They show how the settings work, not what to trade.

Open **Automation** in the top bar to see your bots. **New bot** starts a fresh one.

## The Automation page
**The bar at the top** sums up the day:
- **New entries:** Allowed, or **No new trades** or **Frozen** while one of those switches is on (change them from the switches menu in the top bar). Below it, while some bot leaves a wing open after a stop, whether that's on or paused.
- **Armed:** how many bots are armed, and how many of those are Live.
- **Today:** how many of today's entries have filled, how many are still to come, and any that need a look (not filled, missed or an error).
- **Next entry:** the next entry time, the bot, and how long until it.
- **Integrity:** whether the app found anything left unprotected on its open trades (see [When the app checks itself](#when-the-app-checks-itself)).
- On the right: **📅 Schedule**, **Match all bots**, **Import a bot** and **New bot**.

**Today** draws today's entries on a timeline from about 9:15 to 4:15, one line per strategy. Each dot is an entry: filled (solid green), still to come (a ring), skipped or blocked (a dashed amber ring), or not filled or missed (red). A green line marks the time now. Point at a dot to see its accounts and what happened; click it to jump to the bot's row.

**Bots sit under their strategy**, the journal tag their trades are filed under. Bots that are switched off are listed last, under **Off**. **Group: Day** lists them by the weekday they enter instead (Every weekday, Monday, Tuesday … or Specific dates & monthly); the page remembers your choice.

**One row for the same bot in several accounts.** If you keep a copy of a bot for each account and end each copy's name with its account, like "Iron Condor - Beta" in your Beta account and "Iron Condor - IRA" in your Roth IRA, they share one row called "Iron Condor". Each copy stays its own bot, with its own size, times and switches. Copies only share a row when the end of the name matches that bot's own account name, and only under the same strategy; a name like "Butterfly - XSP" stays as it is.

Each row shows:
- the bot's name, what it trades, **Paper** while it's armed in Paper, and its legs in one line;
- its schedule: the weekdays it enters (today underlined) and its entry times. Past times are crossed out and the next one is outlined. When the accounts have different times, each account gets its own line;
- its exits;
- an **account chip** for each account: a green dot when armed and Live, amber when armed in Paper, dashed when off, and its size (×2 is 2 contracts, $ means sized in dollars). With more than one account the row adds up the lots;
- **P/L:** the closed P/L of all its accounts together (a Paper bot's pretend P/L is marked paper). It shows "—" when two of its bots trade the same account, since their records overlap; open the row to see each;
- **Today:** a dot for each of today's entries, and how many filled.

**Click a row** to open it: its legs and exits, and a line for each account with that bot's own buttons: **Preview order** (the eye), **arm / switch off** (the power button, green while armed), **Paper / Live** (the page means Paper and opens the go-Live summary; the red bolt means Live, and pressing it switches the bot back to Paper), **Trade now** (Live bots: opens the bot's page, where you type TRADE) and **Clone**. Hover over a button to see what it does.

The buttons at the end of a row do the same without opening it: the eye previews the order, the play button goes to **Trade now**, and **⋯** lists everything for each account. With several accounts, the eye and the play button ask which account first, so one click never acts on several accounts. Click an account chip for the same list for that one bot.

**Ticking:** the box at the start of a row ticks all of its bots (or tick one account in the open row). The bar at the bottom then works on the ticked bots: Skip day, Arm, Turn off, Paper, Live and Tags.

Without JavaScript every row shows open, with its buttons.

## A bot's page
A saved bot's page opens with a **summary**: the underlying and the account it trades, its legs, when it enters (times, days, size and filters), its exits, and how its orders are chased. Below that is its record so far: open trades, closed P/L, win rate, and the average and biggest winner and loser per lot (a Paper bot shows its paper P/L, win rate and trades). **See its trades** opens the full record.

Once the bot has closed a trade, a **P/L graph** sits under those numbers, folded away. Click it to open it: the line starts at $0 and steps up or down with each closed trade, oldest first, so you can see how its total got where it is. Point at the line (or use the arrow keys) to see each trade's date, what it made or lost, and the total after it. A trade that's still open isn't on the line until it closes, so while one is partly closed the line can end a little off the **Closed P/L** figure. If you leave the graph open, it's open again the next time you come back to any bot's page; fold it to put it away.

## Building a bot
A bot's page is one long form in numbered sections. The menu on the left jumps to each one and shows which you're in; in a narrow window it's a row across the top. Changes to its settings are kept only when you press **Save** at the bottom.

A new bot starts as an iron condor with the numbers left blank for you to fill in. To start from another shape, use **Start from** at the top of a new bot's page (see [Shapes a bot can trade](#shapes-a-bot-can-trade)).

An iron condor has two **groups**, a call side and a put side. Each has a short option (the one you sell) and a long option, the "wing" (the one you buy for protection). Many examples on this page use an iron condor; earlier versions of the app called a group a "side", and you'll still see that word where only an iron condor is meant.

### Shapes a bot can trade
**Start from** (only on a new bot) fills in the legs and the groups, with the stops and targets left blank. A short put, a short call and blank start with their stop resting at the broker; every other shape (any group of more than one leg, and every debit shape) starts with its profit target resting there (see [Which exit rests at the broker](#which-exit-rests-at-the-broker)). Pick a shape and press **Use this shape**. It starts the form again, so anything you'd typed below is cleared. The shapes are: iron condor; put or call credit spread; put or call debit spread; short put or call; long put or call; iron butterfly; long call butterfly; short straddle; short strangle; long straddle; condor (four calls, for a debit); reverse iron condor (for a debit); calendar, diagonal, double calendar and double diagonal (legs in two expirations, see [Calendars, diagonals and trades held overnight](#calendars-diagonals-and-trades-held-overnight)); and blank (one leg, to build your own).

You can change any shape afterwards:
- **Legs:** 1 to 4. **+ Add leg** adds one; 🔗 adds one linked to that row; ✕ on a row removes it. Four is the most one order can carry at the broker.
- **Ratio:** how many of that leg go with one of the others, from 1 to 10. A long call butterfly buys 1 call, sells 2 calls a little higher and buys 1 higher still: its middle leg has ratio 2. With 3 contracts per entry, that's 3, 6 and 3.
- **Stop and target, or leg groups:** most bots have one stop and one profit target for the whole trade. Tick **Use leg groups** to split the legs into groups (up to 4) that each close on their own stop and target, like an iron condor's call side and put side. Each group shows its name and its legs as cards; **▲** and **▼** on a card move that leg to the group above or below. **+ Add leg group** adds an empty group to move legs into, and ✕ removes a group (its legs move to the first group). Each group then has its own **Stop and target** panel, and renaming a group changes its name there as you type. Ticking it adds a group. Unticking it makes the legs one trade with one stop and one target (they start from the first group's, so check them); the page says so, and nothing is saved until you press Save. The iron condor shape starts with it on.

#### Credit or debit: the trade decides
A bot doesn't say whether it opens for a credit or a debit. A trade takes **whichever side its middle price lands on** when the entry is priced. A broken-wing butterfly, a risk reversal or anything picked by delta can come out either way from one day to the next, and it's entered either way.
- **Too close to even is skipped.** If the middle price is under `$0.05` either way, the run is skipped: "Priced too close to even (0.03); not entered."
- **A group with one sold leg inside a trade that takes in money must take in money itself.** Say an iron condor's put wing lands *inside* its short put: the put side would cost money, so nothing is sent and the run shows an error: "Inverted: the Put side prices as a debit." The rest of the trade's credit can't hide it, because that group's stop would sit on an option that rises while the group wins. So the only entries refused for their price are one too close to even and one with an inverted group.
- **Put premium bounds on every bot.** A credit spread whose quotes go bad can now come out as a debit and be entered. Bounds keep it to the kind of trade you meant: bounds of `1.00` to `3.00` let in a `1.50` credit or a `2.20` debit, and skip a `0.40` of either (see [Premium bounds](#premium-bounds)).
- **Each trade is looked after by the side it filled on.** A trade that fills for a credit sets its stops and targets one way, a debit another (see [Which exit rests at the broker](#which-exit-rests-at-the-broker)). The run page and **Preview order** say which side it priced as.

**Debit entries are chased the other way.** A credit entry gives up a step of credit each try, never going below $0.05. A debit entry pays a step more each try, up to the number of tries you set. If you've set premium bounds with a maximum, a debit chase never pays more than that maximum. Example: a call spread offered at `2.00`, steps of `0.05`, 6 tries, and a maximum of `2.15`: it tries `2.00`, `2.05`, `2.10`, `2.15`, and stops there.

### Basics
Every field in Basics has an **ⓘ** beside its name: point at it, tab to it or tap it to see what the field does.

- A name.
- The **journal tag** it puts on its trades in your journal (required). Pick one of your journal's tags, or press **+ New tag** under the list, type a name (it starts with the bot's name) and press **Create**: the tag is made (or, if you already have one by that name, picked) and nothing else on the form changes. It shows in **Journal → Tags** too, where you can rename it. While the bot exists, that tag can't be deleted.
- The **account** it trades in: a TastyTrade or a Schwab account, shown by its nickname at the broker.
- The **underlying**: SPX, XSP, or a stock or ETF ticker. See [Which underlying](#which-underlying).
- **Size each entry by:** **Contracts** (every bot starts this way) or **Buying power**. Only the box for the one you pick shows:
  - **Contracts per entry**, from 1 to 100. Say it's 3: each entry is one order, with every leg, for 3 contracts. See [Bigger entries and partial fills](#bigger-entries-and-partial-fills).
  - **Most buying power per entry**: the app asks your broker what one contract needs and buys as many as that covers. See [Sizing by buying power](#sizing-by-buying-power).
- Under **Size and limits**, two contract limits, **Max contracts per trade** and **Max contracts per day**, counted in **contracts in the largest leg**. **You can leave both blank** for **Auto**: per trade is the biggest entry the bot makes on its own (its contracts, or an entry time's own number); per day is what the entry times that are on add up to (an entry window counts as one entry), no more than **Max entries per day** of them. The box shows the number it uses, for example "Auto: 6". Every order the bot sends is still checked against them, so if something went wrong and an entry fired twice, it couldn't repeat all day. Blank limits don't hold back entries you start yourself (Trade now, the Trade page, taking over a trade): those are capped only at 100 contracts and don't count toward the bot's own day. Type a number to set a limit of your own; it then applies to everything. For a shape where every ratio is 1, like an iron condor, that's simply the contracts. A butterfly whose middle leg is sold 2 at a time uses 2 for each contract you enter: 3 contracts per entry need a limit of at least 6. Contracts per entry × the largest ratio can't be more than either limit (picking a shape raises the limits for you if they're too low). Only the orders that **open** a trade count toward them; the bot's own stops and closes don't. When the app re-sends the unfilled rest of a partly filled entry, that doesn't count a second time.
- **Max entries per day** and **Max open trades.** Both are optional: leave them blank for no limit. See [Limits on entries and open trades](#limits-on-entries-and-open-trades).
- **Tags** (optional): your own labels, like `0DTE` or `Mornings`. The bot's tags show as chips; press **×** on one to take it off. **Pick tags** lists every tag your bots and your journal already use, to tick; to make a new one, type it in the **New tag** box and press **Add** (or Enter, which never saves the bot). They help you find bots on the list and never change how a bot trades. They're never in a shared bot file. See [Tags](#tags).

### Which underlying
Type the **underlying** the bot trades: **SPX**, **XSP**, or any **stock or ETF** your broker lists options on, like `SPY`, `QQQ`, `IWM` or `AAPL`. The box suggests the first five. When you save a bot with a new stock, the app checks your broker lists options on it. If it doesn't, or the app can't reach your broker, the bot isn't saved and the page says why. Other indexes (RUT, NDX, VIX) aren't traded. Type SPX, not SPXW: the bot picks SPX's weekly options itself. A share class with a dot, like BRK.B, can't be traded yet: its options trade under another symbol (BRKB) that the bot can't match to it. The examples on this page use SPX; everything works the same way on the others, in their own prices.

| Underlying | What it is | Settles | Price steps |
|---|---|---|---|
| **SPX** | the S&P 500 index | in cash, at the close | $0.05 (a single option at $3 or more: $0.10) |
| **XSP** | Mini-SPX, a tenth of SPX | in cash, at the close | $0.01 |
| **SPY, QQQ, IWM** | big index funds (ETFs) | in shares | $0.01 |
| **a stock or another ETF** | | in shares | its own: often $0.01 under $3 and $0.05 from $3 |

- **Points, strikes and prices are in the underlying's own price.** XSP is a tenth of SPX: with SPX at `5,800`, XSP is `580`. A spot offset of `-5` on XSP is like `-50` on SPX, and a `$0.30` XSP option is like a `$3.00` SPX one. On XSP the app reads SPX's price and divides by 10, because SPX's price comes in more reliably. The strikes come from the option chain, so they follow each stock's own strike steps.
- **A stop's trigger is rounded down to the option's own price step.** A stop at `4.875` rests at `4.80` on SPX, `4.87` on XSP or SPY, and `4.85` on a stock that steps in nickels from $3.
- **A stock's price steps come from TastyTrade.** Before a stock or ETF bot enters, or takes a trade you hand over, it loads them. **If it can't** (TastyTrade isn't connected, or doesn't answer), nothing is sent and the run says "Couldn't load TastyTrade's option increments". So with only Schwab connected, only SPX and XSP bots can trade. A stock the app has loaded before keeps using its saved steps, even if they're over a week old, while TastyTrade is down; after a failed attempt the app tries again after 5 minutes.
- **Each contract is for 100** of the underlying. The bot trades only the standard options. It never trades a monthly SPX option, which settles on the next morning's opening prices. It never trades an "adjusted" option either: one with a name like SPY1, left over from a split or a special dividend, which isn't for 100 shares. A trade on one of those is refused.
- **The chase's steps can be a cent** on everything but SPX. A new bot still starts at $0.05.
- **The filters and watch rules read the underlying's own price** (SPX ÷ 10 for XSP). The VIX filters read VIX for every bot.
- **Hours** are the same for all: bots work only in the market's regular hours, and entries stop 2 minutes before the close.
- **Changing a bot's underlying** isn't allowed while it has a trade open, an exit working or an entry under way. Once it's saved with a new underlying, the bot is switched off and set to **Paper**, so you can watch it again before it trades for real.

#### Stock and ETF options settle in shares
SPX and XSP options settle in cash at the close: an option in the money is simply paid out. **Stock and ETF options are different** ("American-style"). An option you sold can be **assigned** to you at any time, and at the close of its last day one that's in the money is turned into shares. Say a bot sold an SPY `570` put and SPY closes at `568` on its last day. You'd buy 100 SPY shares at `570` for each contract, and you'd hold them the next morning.
- **Give such a bot a timed close if you don't want to hold its options to the end.** It isn't required.
- **On expiry day, 10 minutes before the close** (3:50 p.m. New York, 12:50 on a half day), you get an urgent alert if a bot's trade still holds any option that expires that day. It lists each one and whether it's in the money, and names the bot's timed close if it has one. You get it once, even if the app restarts. The app doesn't close anything because of it: close it yourself, or check your broker.
- **An early assignment** happens overnight. The morning check at 9:20 (see [Trades held overnight](#calendars-diagonals-and-trades-held-overnight)) finds the sold option missing at your broker and says it may have been assigned. You may hold shares, and its stop may still be resting at your broker. Cancel that stop and decide what to do with the shares; the bot does neither.
- **The app doesn't know a stock's earnings or dividend dates.** A stock can jump on earnings, and a sold call is more likely to be assigned the day before a dividend. Check them before a bot holds a trade over one.

#### Sizing by buying power
Instead of a number of contracts, you can give each entry the most **buying power** it may use. Before each entry the app asks your broker what **one contract needs on its own**, and buys as many as your amount covers. It works the same for a trade that prices as a credit or as a debit.

- **Which broker:** the one the bot's account is at. TastyTrade is asked with a dry run; Schwab with its order preview (two of them, for one contract and for two). A preview is never placed, and it's allowed even with the trading guard on.
- **On its own:** what the trade needs by itself, not net of what you already hold. So the same bot gets the same size whatever else is in the account.
- **At the worst price:** the price of the entry chase's last try, so no fill the chase can reach needs more than you set.

Example: the most buying power is `$2,000`. The trade is a 5-wide put credit spread whose chase could go down to a `0.05` credit. TastyTrade says it needs `$500` of margin, less the `$5` credit, plus `$2.30` of fees: `$497.30` a contract. `$2,000` buys **4**.
- It never goes past your contract limits. Above **Max contracts per trade** it's cut down to the limit. If it would pass **Max contracts per day**, the entry is skipped, not cut down, as for a bot sized by contracts.
- If your amount doesn't cover one contract, the entry is skipped and you get an alert ("One contract needs $497.30 of buying power at the worst price 0.05, more than the $400.00 budget").
- If the broker refuses the preview (TastyTrade does when the account can't afford even one), doesn't answer, or its answer can't be read, the entry is skipped and you get an alert. So is one where the app can't work out the worst price. It never guesses.
- An entry time's own contracts are ignored. Entries you send from the Trade page keep the size you set there.
- It's yours: a shared bot file never carries it.

The run page says how an entry was sized: the amount, the worst price, and what the broker said one contract needs.

### Legs: how the strikes are chosen
Each leg is one row: buy or sell (sold rows are red, bought ones green), call or put, and how its strike is found. A row shows only the boxes its **Pick by** uses: **From** for an offset or a premium leg, **Exact** for an offset, **Max offset** for a premium leg with a From leg. The words beside **Target** say what it's in ($, Δ, or points). The ⓘ on each heading explains it. With two or more groups, the arrows under **Groups** move a leg between them.

**🔗 on a leg** adds a leg right below it, measured from it: the other side (a bought wing for a sold leg), the same type, ratio and DTE, picked by offset 5 points further out (puts below, calls above). Change the 5 to the width you want. A leg that is itself an offset has no 🔗.

- **By premium:** you give a price, say `$1.00`. At the entry time the app looks at the live option prices and picks the strike whose price is closest. For a leg you **sell**, it only looks at strikes **out of the money** (calls above the SPX price, puts below). A leg you **buy** can be any strike, in or out of the money: a long call by premium `$30.00` takes an in-the-money call. If two are equally close, it takes the one further out.
- **By delta:** you give a delta, say `16` (typing `0.16` means the same). At the entry time the app picks the strike whose delta is closest; for a leg you sell, again only **out of the money**; if two are equally close, it takes the one further out. A delta closer to the money than the nearest out-of-the-money strike (say `50`) takes that nearest strike. The form shows it back as `16`.
  - **Where the deltas come from:** Schwab's option chain when you've connected Schwab; otherwise TastyTrade's live deltas, or, where it hasn't sent one, a delta the app works out from the option's middle price. Those are used only while TastyTrade's live prices are coming in and SPX has updated in the last 30 seconds (a far-out option's price can sit unchanged for minutes, and that's fine).
  - **If the deltas aren't there, the bot doesn't guess.** If fewer than 95 out of every 100 strikes on that side have a delta, or none is as far out as you asked (you asked for `2` and the furthest listed is `4`), the entry is **skipped**. Missing deltas also send you one alert that day, "IC: entry skipped, deltas unavailable". A delta of the wrong sign (a put's must be negative) counts as bad data the same way. It never falls back to picking by price.
  - A short whose delta comes out at `90` or more is refused as an error: the prices look stale.
- **By spot offset:** you give a distance in points from the SPX price, say `-50`. With SPX at 5,803 that's 5,753. A short goes to the nearest listed strike **at least that far out**, 5,750, so it's never closer to the money than you asked; a long takes the nearest listed strike either way (5,755). A short put's points are below the price (a negative number) and a short call's above (positive); the page won't save it the other way round. **`0`** takes the listed strike nearest the SPX price, for any leg: that's how an iron butterfly or a straddle puts a call and a put on the same strike.
- **By offset:** you give a distance in strike points from another leg, and the app adds it as you type it: plus is a higher strike, minus a lower one. So a wing above a short call is a positive number and a wing below a short put a negative one. With a short at 5,000, `75` puts a call wing at 5,075 and `-75` puts a put wing at 4,925. The other leg can be picked any way except by offset itself, so a wing can sit 50 points past a 16-delta short. **Exact strike** means that strike or nothing: if it isn't listed, the bot skips that entry. Without it, the bot takes the nearest listed strike further out.

- **A wing with a max offset:** a bought leg picked **by premium**, with **From** set to the sold leg of its type and a **Max offset** in points (a put's negative, a call's positive). It takes the strike nearest its premium that is no further than that from the sold leg, so the spread is never wider. Example: a bought put aiming at `0.50` with `-25` from a sold put at `5,700` looks only from `5,695` down to `5,675`. If nothing there has a price, it takes the priced strike nearest that limit just past it.

**Delta and spot offset need a fresh SPX price.** If the app's SPX price is more than 30 seconds old at the entry, the entry is skipped and you get one alert that day ("SPX price unavailable"). A bot that picks only by premium and offset isn't affected.

The usual setup is the shorts by premium or delta and the wings by offset from their short. Each leg has a **ratio** from 1 to 10 (see [Shapes a bot can trade](#shapes-a-bot-can-trade)) and a **DTE**, its days to expiration: 0 is today, up to 60. Legs with different DTEs make a calendar or a diagonal, and any DTE above 0 means the trade is held overnight (see [Calendars, diagonals and trades held overnight](#calendars-diagonals-and-trades-held-overnight)).

Under **Strike picking**, below the legs: **Exact DTE only** (see [Calendars, diagonals and trades held overnight](#calendars-diagonals-and-trades-held-overnight)) and two more:
- **Min short width:** with one sold put and one sold call in the same expiration, they're moved apart one listed strike at a time until the call is at least this many points above the put. Each step moves whichever side gives up less of its own target (its premium, or its delta). Example: `20` turns sold strikes at `5,795` and `5,805` into `5,790` and `5,810`. If there aren't enough strikes, the entry is skipped. Blank is off. The wings are placed from where the shorts end up.
- **Move conflicted strikes** (new bots start with it on): a leg that lands on an option you already hold the other way would make your broker refuse the whole order, so it moves one strike further out. So does a leg on the same option as another leg of this order. It moves at most one strike: if that one is also taken, held the other way, or has no price, it stays where it is. The app reads your positions for this at each entry; if it can't, it goes on without that check and says so in the run's log.

The run's log says which strikes moved and why.

**Strikes that don't make sense are refused** as an error, nothing sent: a sold option that's in the money (except a sold call and a sold put on the same strike, the middle of an iron butterfly or a straddle, which sits at the money by design), a sold call below a sold put, or two legs on the same option.

**A sold leg placed by an offset may be in the money.** A leg picked by spot offset or by offset from another leg sits exactly where you put it, so in the money is your setup, not a mistake. Example: an in-the-money diagonal that buys a call 50 points below SPX and sells a call 10 points above it. The app still checks that sold option's delta: at `90` or more it is refused as an error (the prices look stale). If the app can't get a delta for it while it's in the money, the entry is **skipped** and you get the "deltas unavailable" alert that day. Sold legs picked by premium or delta are still never in the money.

### Stop and target (or each group's)
With **Use leg groups** off, the section is called **Stop and target**, and what follows is for the whole trade, as one group. With it on, each group has its own. Each group has a **stop**, a **profit target** and, if it has one sold leg and other legs, a choice of what happens to those **other legs** after a stop. Leave a stop or target blank for none. The ⓘ on each heading explains it. A box shows only once it's needed: the limit price when the other legs are **sold at a limit**, **When its stop fills, also close** once the group has a stop, the profit-action rows once **Use profit actions** is ticked, and the closing order's amount once it isn't Off. **"$" always means option price per contract:** 2.00 is $200 for one contract.

**How the stop and target are set on the form:** **Stop** is an amount, then **% / basis** or **$ P&L**. A % stop then names its basis: **leg premium** (% above the short's fill), **entry premium** (% of the whole trade's entry credit), **closing order ($)** (dollars off the whole trade's entry credit; a line under it works out what a $5.00 entry would stop at), or **group premium** (% of the group's own entry price lost; the only basis for a group with no single sold leg). **$ P&L** is a loss per contract. **Profit target** is an amount and **%** or **$**. The ⓘ on each heading explains them, and the sections below go through each with examples.

**Split iron condors:** on by default, and shown only for a bot that sells a put and a call and buys a put and a call, all at the same days to expiration. The number of contracts on each leg can differ (for example 1 put spread and 3 call spreads). A bot whose legs expire on different days (a double calendar or diagonal) can't be split. It doesn't matter how the legs are grouped (two groups, one per side, or each leg in its own group), as long as no group mixes puts and calls. Each group's stops work as usual. Its trades show in your journal as two sides, a call spread and a put spread. Any other shape shows as one trade. See [Your trade journal](journal.md); you can **combine** any one trade back.

#### Which exit rests at the broker
Your broker (TastyTrade or Schwab) takes only **one closing order** for the same contracts. So a group can have its stop or its profit target resting at your broker, not both, and **the app watches the other one**.

**Only a group of one sold leg can rest its stop.** Once such a group (a short put on its own, say) has a stop, **At the broker** offers **Rest the stop at the broker (stop market order)**: ticked, its stop rests there; unticked, its profit target rests and the app watches the stop. **A group of more than one leg** (a credit spread, an iron condor's side, a butterfly) **never rests its stop**: there's no choice on its panel, which says **Stop watched by the app**. Its profit target rests at your broker and the app watches its stop. Schwab bots follow the same rules (see [Schwab bots](#schwab-bots)), and futures bots rest a target only (see [Futures bots](#futures-options-bots)).

| The group | Rests at the broker | The app watches |
|---|---|---|
| **One sold leg**, trade filled for a credit, chose **Stop** | its stop, on the sold leg | its profit target |
| One sold leg, chose **Profit target** | its profit target | its stop, on the sold leg's price |
| **More than one leg, with one sold leg** (a credit spread, an iron condor's side) | its profit target (one closing order for all its legs) | its stop, on the sold leg's price |
| Two or more sold legs, or none (an iron butterfly kept as one group, a long call) | its profit target | its stop, on the whole group's profit or loss |
| Trade filled for a **debit** | its profit target | its stop, on the whole group's profit or loss |

**To rest a stop on a sold leg, give it a group of its own** and put its wing (the option you bought for protection) in another group. Then, on the sold leg's group, pick the wing's group under **When its stop fills, also close** (see [Closing another group when a stop fills](#closing-another-group-when-a-stop-fills)). An iron condor done this way is four groups: short call, long call, short put, long put, each short closing its wing.

Examples (made-up numbers):
- **A short put on its own, resting its stop.** Sold at `2.50` with a 95% stop: a stop at `4.80` rests at TastyTrade on the put and **works even when your computer is off**. The app watches the profit target.
- **A put credit spread.** Its target rests at TastyTrade and the app watches its stop on the sold put's price: a 95% stop on a put sold at `2.50` closes the spread once that put costs `4.80`. The stop works only while the app runs.
- **An iron butterfly kept as one group.** It has two sold legs, so no single option says when to stop. Its target rests, and the app watches its stop on the whole group's profit or loss.
- **A call debit spread.** Bought for `2.00`, with a 50% target and a 50% stop: the target rests as a sell order at `3.00`, and the app closes the group if it falls to `1.00`.

**Bots switched off by this change.** Until 15 October 2026 a group with one sold leg and its wing rested its stop on the sold leg. When the app updated, every armed or live bot with such a group was switched off and set to Paper, with one alert each, and its page says why until you arm it again. Its groups now rest their targets; arm it again with the "watched by this app" box ticked, or split each sold leg into its own group to keep a resting stop. Trades open at the time kept the exits they started with.

**The fall-back.** A group that chose **Stop** but whose trade filled for a **debit** can't rest a stop (a broker's stop on a sold leg would fire while the debit trade is winning: the middle of a long butterfly goes up as SPX moves toward it). So its target rests, the app watches the stop, and you get an alert when the trade is entered: "opened for a debit, so its stop is watched by the bot", naming the profit and loss level it watches. The run page shows the same note on the group.

**A stop the app watches only works while the app is running and your computer is on, awake and connected.** If the computer sleeps or the app is closed, nothing closes the trade. Keep the computer awake while such a trade is open (the menu bar app's **Keep awake** helps). The app reads every leg's price every 2 seconds while the market is open and closes the whole group once the loss reaches the stop on two readings in a row. The app alerts you whenever it can't watch: stale prices, a gap in watching (with the times), a stop reached while orders are frozen, and a stop that started 3 closes in a day without closing the group. These work the same as for a Schwab bot: see [Schwab bots](#schwab-bots).

**The "watched by this app" box.** The bot's page says so, and arming the bot, making it live and **Preview order** on a live one need a box ticked to say you understand. It's needed when any group has a stop that's watched, or is set to rest its **Profit target** (on a debit fill its stop is watched either way, even with no target filled in). A group set to **Stop** that might fall back on a debit fill is **not** counted for the box: you're told at the entry instead. If you change an armed or live bot so that a stop becomes watched by the app (for example, you set a group to rest its target), saving switches it off and sets it to Paper, and you tick the box when you arm it again.

**Resting targets stay on overnight.** A target resting at TastyTrade is good till cancelled, so it works through the night and when your computer is off. It is placed as one closing order for all of the group's legs.

**Every close takes the resting target off first.** A timed close, a close you ask for, a profit action or watch-rule close, and a watched stop's close all start by cancelling the group's resting target and checking with the broker that it's gone, so the group never has two closing orders at once. If the target filled first, that is recorded and nothing else is sent. If the close didn't close the group, the target is placed again before its stop is watched again, and never while another close is running on the group. **If the app can't tell whether the target is still there**, it sends nothing, holds that side, and tells you: the group may have no stop and no profit target until you check your broker, and the stop isn't watched until then. The same goes when TastyTrade gives no clear answer as the app places a resting target (at the fill, when Freeze lifts, when you switch it back on, after a close, or on Restore), or when the app stops while placing one: the app treats it as possibly resting, sends no close on that group, marks the run **Needs you** and tells you to check your broker. A resting target that fills ends the watch on that group.

**When a resting target is gone, the alert says what's left:** its stop, still watched by the app (or resting at the broker); or, on a group with no stop (or a futures trade), that it has **no exit now**, and then the run is also marked **Needs you**.

**Bots made before this change.** A bot that used to open for a debit now rests its targets at TastyTrade from its next trade, where before the app watched them; every other bot keeps resting its stops. A trade already open keeps what it started with.

Why not rest every stop at the broker? A broker's stop order is for one option. A group with two sold legs, or none, has no single option whose price says when to stop. And on a debit trade a sold leg's price **rises while the trade is winning**, so a stop resting on it would close a winning trade.

A watched stop on a group's profit or loss is set one of two ways:

| Style | What the number means | Example |
|---|---|---|
| **% of entry price lost** | Close once the group has lost this % of what it was opened for (the credit it took in, or the debit it paid). It reads the same on a credit and a debit. (A group you add starts on **% above the short's fill**; pick this one yourself.) | A call spread bought for `2.00`, stop `50`: closes once it's worth `1.00` (a loss of `1.00`). A short straddle that took in `1.20`, stop `100`: closes once buying it back costs `2.40`. |
| **$ loss per contract** | Close once the group has lost this much per contract. | The same call spread, `$1.00`: closes once it's worth `1.00`. |

"Per contract" is per one of the group's smallest whole sets: for a 1×2×1 butterfly, one of each outer leg and two of the middle.

**A group's stop on a credit trade** is rested or watched on its sold leg with the styles below.

The rest of this part describes stops worked out on the sold leg, resting or watched.

#### Four ways to set a stop on the sold leg
A stop on the sold leg (the "short") fires when the short's price rises to the trigger. It rests at your broker when the group chose Stop; when the group chose Profit target, the app watches the same trigger on the short's price. If the short's ratio is more than 1, the trigger is per contract of that leg. The number you type means something different for each style:

| Style | What the number means | Example |
|---|---|---|
| **% above the short's fill** | The short's price may rise this % above what you sold it for. 100% means the price doubles. Credit trades only. | Sold for `2.50`, stop `95`: 2.50 × 1.95 = 4.875, so the stop sits at `4.80`. |
| **% of the entry credit** | The trigger is this % of the credit for the **whole** trade, the same for both sides. (Not "above": 95 means 95% of the credit.) On a debit fill it is read as a loss: 200 is the same loss less 100, so 200 means all of the debit lost. | The entry brought in `4.90`, stop `95`: 4.90 × 0.95 = 4.655, so `4.60`. |
| **$ off the entry credit** | The trigger is the whole trade's credit less this amount, the same for both sides. Credit trades only. | The entry brought in `4.90`, `0.20` off: `4.70`. |
| **$ loss per contract** | The short's fill plus this amount. | Sold for `2.50`, `$2.00`: 2.50 + 2.00 = `4.50`. |

In every case the trigger is rounded **down** to a price your broker accepts ($0.05 steps below $3, $0.10 from $3), so it fires a little sooner, never later. The **$ loss per contract** stop rests at the broker on the short option, like the others; it is not a watch on the whole condor.

**Which styles work on which side.** *% above the short's fill* and *$ off the entry credit* (and the closing order below) are measured on a credit. On a trade that filled for a debit they don't translate: that group gets no stop and you're alerted. *% of the entry credit* reads as a loss on a debit (above). *$ loss per contract* and *% of entry price lost* work on both. **On a group with one sold leg (a credit spread), *% of entry price lost* is measured on the sold leg, with the bought leg (the wing) counted as worthless.** Its stop sits at the group's credit times (1 + that %) on the short's price, but the short's price also carries the wing's value, so on a spread it fires early. If that level would fire at once on the short (often a calendar or diagonal), no stop is set and you're alerted. For a credit spread, *% above the short's fill* is the better fit, and it's what a group you add starts on.

#### The closing order: Replaces each stop, or Whichever fires first
On the bot's page this is **Advanced closing orders (optional)**, under the groups' panels, and it shows only with **Use leg groups** on: with one group, the trade's credit is that group's, and the stop's own **closing order ($)** basis does the same. (A one-group bot that already uses it still shows it.)

The **closing order** is one dollar amount off the whole entry credit. It is used only on a credit trade's stop on a sold leg; on a debit fill it is ignored and you're told. It sits under the sides and has three settings:
- **Off:** each side uses only its own stop.
- **Replaces each stop:** every side whose stop isn't a **$ loss per contract** stop (that is, any of the first three styles) uses the closing order instead of its own number.
- **Whichever fires first:** a side uses the closing order only if it is lower than the side's own stop (it's reached first); otherwise it keeps its own.

Example: the entry brought in `4.90` and the closing order is `0.15`, which gives `4.75` (then rounded down to the tick, `4.70`). Call side's own stop works out at `4.60`: with **Whichever fires first** it keeps `4.60`, because that is lower. Put side's own stop is `5.40`: it uses the closing order, `4.70` after rounding. With **Replaces each stop** both would use `4.70`. A **$ loss per contract** stop ignores the closing order.

#### Profit targets: % or $
- **% of the credit kept, or gained on the debit.** On a trade that filled for a credit, the credit is what the group brought in, what was sold minus what was bought. A group brought in `2.00` and the target is **50%**: the group closes when closing it would cost no more than `1.00`. On a debit, the group closes once it can be sold for this much more than it cost: a call spread bought for `2.00` with a **50%** target closes once it can be sold for `3.00`. Allowed from 1 to 1000 on the bot's page. **On a credit a target of 100% or more can't be reached,** so that trade gets no target and you're told. (On the run page, a target change is 1 to 99 on a credit and 1 to 1000 on a debit.)
- **$ profit per contract:** either way, the profit you want per contract. The credit group above with `$0.75` closes at a cost of `1.25`; the call spread with `$0.75` closes once it sells for `2.75`.

When the group chose **Profit target** (or fell back to it), the target rests at TastyTrade as one closing limit order for all its legs. Otherwise the app watches it: it reads every leg, what the sold legs cost to buy back at their asking price, less what the bought legs sell for at their bid.

#### When another group's stop closes it: how its legs are closed
A stop the app watches closes its whole group, and a group whose stop rests at the broker is one sold leg, with nothing else in it. So this choice is for a group that **another group's stop closes** (see the next section): the wing of a sold leg that rests its stop on its own. It shows on a group once another group picks it under **When its stop fills, also close**. With two or more legs, each is sold on its own order, the same way:
- **Market closed** (the default; shown as "sold now" in a trade's log). If the wing has a fresh price (a quote under 30 seconds old), it is sold at market. If it doesn't, the app offers it at one tick ($0.05 on SPX) instead, which still sells at the bid if there is one. So an order at one tick on the run page isn't a bad price; it's the app's safe way to sell without a recent quote. *This changes what existing bots do:* they used to always sell at market.
- **At a limit** (no longer offered; a bot saved with it keeps it until you pick another): your price, or the bid if blank. It may not fill.
- **Chased closed:** offered from the middle of the bid and ask and stepped down by the exit chase settings. Whatever isn't sold by the last step is sold now. If your broker turns the chase's first order down, nothing is out, so the wing is sold now instead. If the app's own order check refuses it, that is final and nothing more is sent; if the app can't tell whether the order reached your broker, it sends nothing more and tells you to check your broker. The chase runs on its own, so the app keeps watching your other trades while it works; on the run page the wing shows **chasing** until it's done, and the "stop filled" alert comes when the chase ends, saying how it went.
- **Left alone:** nothing is closed. The wing stays in your account for you to close. The run page shows **left open** and you get an alert.

Only what you still hold is sold, never more than the trade's size.

#### Closing another group when a stop fills
A group of one sold leg whose stop rests at TastyTrade can also close **another group** the moment its stop fills: under the group, pick it in **When its stop fills, also close** (only such a group offers it). This lets a sold option and the option that protects it sit in **separate groups**, so the sold one's stop can rest at the broker on its own, while the protection is still closed when the stop fills.

For example: group 1 sells a put at about 2.90 with a 95% stop, and group 2 buys the put 75 points lower. Set group 1 to close group 2. When group 1's stop fills, the app closes group 2 too. How it closes is **group 2's own** "When another group's stop closes it" choice: **Market closed** sends a market order for a one-option group, **Left alone** leaves it (unless that is paused), and anything else (a limit, a chase, or a group of more options) is closed by the bot's exit chase from the middle price. Group 2's own stop and target are taken off first, and the close is never bigger than what you hold.

If group 2 can't be closed then (the market's regular session is over, a close is already running on it, or the app is holding it back), nothing is sent, group 2 keeps its own stop and target, and you get one alert. It isn't tried again later. A group can't close itself, two groups can't close each other, and only a stop that rests at TastyTrade can do this (not a stop the app watches, a Schwab bot's or a futures bot's). On a trade where that group's stop is watched (it chose Profit target, or it filled for a debit), the entry alert says closing the other group won't run.

#### Pausing "left open"
**Wings left open after a stop** has its own box at the bottom of the Automation page. It only shows while some group or some trade is set to "Left alone" (or while it's paused, so you can always resume). **Pause** (optionally **until** a time, which is **New York time**, such as `15:30`) when you step away. While it is paused:
- sides set to "left open" sell their wing instead, and
- wings already left open are sold within about 30 seconds.

**Resume** puts "left open" back for the next stops. If the app can't tell whether it is paused, it counts as paused: a wing is sold, never left unwatched.

#### Profit actions
Once a group has made a set profit, close part of it and/or move its stop closer, to lock in some of the gain. Tick **Use profit actions on this group** and fill in a row (up to 5 per group):
- **At:** the profit that sets it off, measured like the group's profit target: the % of its entry price made (its credit, or the debit paid), or $ profit per contract if the target is in $. Each row's **At** must be higher than the one before, and below the group's own profit target.
- **Close %:** this share of the group's contracts at entry, rounded down, at least 1. `0` closes nothing. At least 1 always stays open for the profit target, so a row that would close everything is skipped (you get an alert). All rows together must close less than 100%.
- **Then move the stop to:** the loss you'd accept on what's left, as a % of the group's entry price. `0` is about break-even; `-25` keeps about a quarter of it. A resting stop buys the short back at the credit × (1 + that %), counting the wing as worth nothing; a watched stop moves to that loss on the whole group. Leave it blank to keep the stop where it is.
  - Or pick **$ P&L** beside it: the profit (or, negative, the loss) per contract the stop keeps. On a side that took in `2.45`, `0.40` rests the stop at `2.05`, where buying the short back keeps `0.40`; `-1.00` allows a loss of `1.00`. With a target in $, it must be below the row's **At**.

**On a loss, too.** A negative **At** sets a row off on a loss instead: `-50` once the group has lost half its entry price (or, with a $ target, `-1.00` once it has lost 1.00 a contract). It closes a share and/or moves the stop like any row. Example: "at `-50`, close `50`" closes half a losing trade before its stop is reached. Loss rows go down, each deeper than the one before (`-25`, then `-50`), and take their own turn: the next loss row is watched alongside the next profit row. A new stop on a loss row must be a bigger loss than its level, or it would fire straight away. Close % and the new stop are each optional, but a row needs one of them.

Example (an iron condor side): a side took in `2.00` for 3 contracts, with a stop at `3.80`. A row "at `50`, close `33`, stop `0`": once the side is `1.00` up (it could be bought back for `1.00`), 1 contract is closed, and the stop on the other 2 moves to `2.00`.

How it works:
- **In order, each once.** Only the first profit row that hasn't run yet is watched; when it has run, the next one is. Loss rows (below) have their own turn.
- **Two fresh checks in a row,** every 2 seconds in regular hours (no 30-second wait, unlike the delta and ratio watch rules). Stale prices never set one off.
- **The stop only ever moves closer.** If the new stop isn't tighter than the one working, or it's at or below what the short is offered at right now (it would fire straight away), it isn't moved; the old stop stays and you get an alert.
- **Closing part** runs like a close you ask for on the run page. The new stop is put on what's left once the close has filled; if the close doesn't fill, the old stop goes back.
- **Moving the stop only** (close `0`) cancels the old stop, checks it's gone, then places the new one, like a time action does.
- **Not on a group whose exits you turned off,** not while orders are frozen.

The run page shows a **profit action** row for each one that ran, with the profit it saw and what it did.

#### A stop that can't be used
Before it sends a stop, the app checks the number makes sense. It is **not sent**, and you get one alert saying that side has no stop, if:
- a stop in any of the first three styles works out, after rounding, at or below the short's fill, so it would fire the moment it was placed (a small % stop is the usual case). *This changes what existing bots do:* they used to send it, and it fired at once.
- the closing order or an entry-credit stop works out at zero or below (the amount is at or above the entry credit),
- the trade filled for a debit and the stop is in a style that only works on a credit (see "Which styles work on which side"), or
- the run has no entry credit to base the stop on.

**That side's profit target is still set,** and the alert is the only sign something is missing. When you see "a stop was not placed", open the run page to see why, and add a stop at your broker if you want one. A profit target that can never be reached (a % of 100 or more on a credit) is refused the same way, with an alert, and that side keeps its stop.

### Exits: timed close, time actions and watch rules
The timed close and time actions are in the **Exits** section of the bot's page, after Filters; the watch rules have their own **Watch exits** section after it. All are off until you fill them in. Each has a one-line hint, and its ⓘ has the detail with an example. Profit actions are per group, in the **Groups** section (see "Profit actions" above).

#### Timed close
A timed close closes every side that's still open at a time you choose, for example `15:45`, so a trade isn't left to the last minutes of the day. You set:
- **Close at:** a New York time from `09:30` to `15:59`. Leave it blank for no timed close.
- **Days to expiration, at most:** `0` closes on the day the options expire. Every bot here trades same-day options, so `0` is what you want.

What happens at that time, for each side still open:
1. The side's resting stop or resting target is cancelled (and the app checks with your broker that it's gone), and a watched target stops watching. If it can't tell, nothing is sent (see "Every close takes the resting target off first"). This is exactly what happens when a profit target is reached, so a side never has two closing orders at once.
2. The app checks how much of the side you still hold, and closes that much, never more. If none is left, nothing is sent. If the short is gone but its wing is still there (you bought the short back yourself), nothing is sent and you get an alert: the wing is yours to sell.
3. The close starts at the **middle price** (the middle of the short's bid and ask, less the middle of the wing's), plus the exit chase's starting give-up, and steps toward the broker's side every few seconds, like any close. Say the short is bid `0.55` and asked `0.65`, and the wing is bid `0.00` and asked `0.10`: the close starts at `0.55`.
4. If it hasn't filled after the last try, it does what you chose under **If a close hasn't filled after the last try** (below): with **Give up and put the stop back**, the stop goes back, the profit target starts again (resting at the broker, if it rested before), and you get an alert saying the side is still open.

Good to know:
- **Never past the close.** If the market closes while the timed close is still chasing, it stops, and the stop goes back (on expiry day the options simply expire).
- **Once a day per side.** If the close doesn't go through, it isn't tried again that day. The alert tells you; close the side yourself if you want it closed.
- **Half days.** On a day the market closes early (at 13:00), a time at or after the `13:00` close (your usual `15:45`, say) closes at `12:45` instead, so there's time to chase. A time from `12:45` to `12:59` is kept as it is, which leaves less than 15 minutes before the close; the page warns you about that.
- **No timed close on weekends or market holidays.**
- **A side whose stop already filled is left alone,** including a wing you chose to leave open.
- **Entry times must come before the close time.** A trade entered at or after it would be closed as soon as it's in, so the page refuses it.
- **Missed.** If the app wasn't running at the close time (or orders were frozen) and the market closes with a side still open, that side shows **missed** on the run page and you get one alert. Check your broker.
- You can **Cancel** a running timed close from the run page, the same way as a profit target's close (see "The runs page"). Its stop and target go back.

#### Time actions
A time action moves every open side's stop or profit target at a set time. Turn the **Time actions** pill on and its rows appear; fill one in:
- **When:** *days to expiration* (calendar days until the options expire; `0` is expiry day) or *days in trade* (`0` is the day the trade was entered, `1` the next trading day).
- **Days** and **At:** for example `0` days, at `14:00` New York.
- **Move stop to %:** the side's stop becomes this %, measured the way that side's own % stop is (% above the short's fill, or % of the entry credit). A side whose stop is in $ or "$ off the entry credit" uses % above the short's fill. The closing order doesn't apply to a moved stop. From 1 to 1000.
- **Move target to %:** the side's profit target becomes this % of its credit kept (1 to 99), or on a trade that filled for a debit the % gained (1 to 1000). A side with no profit target gets one.
- On a group whose stop **the app watches**, a moved stop is the % of the entry price lost, and moving it just changes the level the app watches: nothing is cancelled or sent.
- **Close %:** also close this share of each side's contracts at entry, rounded down, always leaving at least 1 open. `50` on a 4-contract trade closes 2 of each side; on a 1-contract trade it closes nothing (1 must stay). The close runs the same way as one you ask for on the run page (see "Managing a trade"), and what's left keeps the stop and target the row just moved.

Fill in at least one of the three. Up to 10 rows; a blank row is ignored.

Example: a side sold for `1.00` with a 50% target closes when it can be bought back for `0.50`. A row "0 days to expiration, at `14:00`, move target to `25`" changes that at 14:00 to `0.75`, so the side closes sooner in the afternoon. A row "move stop to `150`" on a short sold for `2.50` moves its stop to `6.20` (`2.50 × 2.5 = 6.25`, rounded down to the `0.10` tick).

How it works:
- **Each row runs once per side.** Editing the bot later doesn't make a row that already ran run again (rows are known by what they say, not where they are in the list).
- **Moving a stop** cancels the old one, checks with your broker that it's gone, then places the new one. For a second or two the side has no stop. If the old stop can't be confirmed cancelled, or it filled first, nothing is moved, the old stop stays, and you get an alert.
- **A stop that would fire straight away is refused.** Before cancelling anything, the app looks at what the short is being offered at right now. If the new stop is at or below that price, it would fire the moment it was placed, so the move is refused and the old stop keeps protecting the side. If the app has no up-to-date price for the short, it waits a few seconds for one; if none comes it touches nothing, tells you once, and tries again at each check (every 30 seconds) while the action is due.
- **If the new stop is turned down** after the old one was cancelled, the app tries again at its next check (within 30 seconds) and tells you the side has no stop until then. If the app stops in the middle of a move, it sorts the stop out when it starts again: the old stop if it's still there, otherwise the old stop placed again.
- **A stop that can't be used is not moved:** for example a % so small it works out at or below the short's fill. You get an alert and the old stop stays.
- **A side that's closing waits:** if a profit target or timed close is running on it, the action runs at the next check once it's done, if the side is still open.
- **Nothing happens while orders are frozen.** A row runs late once you unfreeze; if that's on a later day you get a "fired late" alert.
- Applying an action is quiet: you get an alert only if something wasn't moved. The run page shows each **time action**, what it moved, and the stop's new arithmetic ("moved by time action 1").

#### Watch rules
A watch rule closes the whole trade (every side still open) when the market moves against it. They have their own card, **Watch exits**, after Exits. Each rule is a pill, **Use delta**, **Use short/long ratio** or **Use underlying price movement**: turn one on and its boxes appear, and the ⓘ beside each box has the detail and an example. A blank number is that part off.
- Under **Use delta**, **Exit below / above position delta** is the whole trade's. For one leg's own delta, press **Add leg delta exit**, pick the leg, and fill in its levels; **✕** removes it.
- Under **Use underlying price movement**, the **Exit when OTM short put is ... from SPX** row shows while the trade sells a put (and the call row while it sells a call). Tick it and fill in the points; unticking it turns it off when you save.
- **Delta:** the trade's delta in whole numbers. A short put counts plus and a short call minus as each is tested, so a put side being pushed toward the money makes it go up. Example: "above `30`". Delta comes only from TastyTrade's live quotes: if they aren't coming in, this rule waits, closes nothing, and you get one alert a day.
- **Short/long ratio:** what the shorts are worth (their middle prices added up) divided by what the wings are worth. As the shorts lose value faster than the wings, it falls. Example: "below `0.78`".
- **Underlying move:** SPX up or down this % from where it was when the trade filled (its last trade at the fill, if under 30 seconds old; otherwise where it was when the strikes were picked). Example: "down `1.5`" on a trade entered with SPX at `5,800` closes once SPX is at `5,713` or lower.
- **A leg's delta:** the same as Delta, for one leg on its own: each leg has its own **below** and **above** under Use delta. Example: a sold call "below `-40`" closes the trade once that call is about 40 delta. A leg the trade no longer holds isn't watched.
- **Short tested:** SPX within this many points of the short put's (or short call's) strike. A negative number means past the strike. Example: "short put within `5`" with a `5,690` short put closes once SPX is at `5,695` or lower.

How it works:
- **Not in the first minute.** Prices right after 09:30 are often far apart, so nothing counts until 09:31.
- **Two checks in a row.** The app checks every 2 seconds during the market's regular hours. A rule closes the trade only when it holds on two checks in a row, so one odd price doesn't close anything.
- **Delta and short/long ratio must hold for 30 seconds.** These two read single option prices and deltas, and one bad quote can make them jump for a few seconds. So they close the trade only once they have been past their level on every check for 30 seconds in a row; a single check back inside starts the 30 seconds again. Example: the ratio reads `0.75` (below `0.78`) at 11:00:00 and stays below on every check; the trade starts closing at 11:00:30. The underlying move and short tested rules still close on two checks in a row.
- **Only fresh prices count.** If a price is missing or more than 30 seconds old (2½ minutes for delta, which the broker sends less often), the rule waits and closes nothing. The broker only sends an option's price when it changes, so a quiet option can go a minute or two without one. While the price stream is clearly working (SPX, or the bot's underlying, updating through it in the last 30 seconds), an unchanged price still counts for up to 5 minutes, as for a stop the app watches. The same goes for a profit action and a target the app watches. If that lasts a minute you get one alert ("watch rules paused") and another when prices are back. The alert names the rules that are paused and the stops still resting at your broker, for example "Your stops at TastyTrade still work: Call side 4.80, Put side 4.80." Those keep protecting the trade the whole time. If no stop rests at the broker for that trade (say the app watches it instead), the alert says so, so you know nothing at the broker is covering it. A profit target the app watches that loses its prices isn't alerted: that only delays taking a profit. It's written in the app's log instead.
- **Closing:** each open side is closed the same way as a close you ask for on the run page: its stop is cancelled (and checked gone) and its profit target stops watching, then the close starts at the middle price and steps with the exit chase. If it doesn't fill, the stop and target come back.
- **Once per trade.** If a watch rule's close doesn't go through, it isn't tried again: the alert tells you, and the rest is yours to close.
- **Left alone:** a side whose exits you turned off, and a side that's already closing (its profit target or timed close is running).
- **Not while orders are frozen,** and never outside regular hours.
- **Underlying move needs the entry price.** Trades opened before this version don't have one recorded, so that rule is off for them (you get one alert); the other rules still work.

The run page shows a **watch rule** row on each side it closed, with what it read ("S/L ratio 0.770 < 0.78"), and the close itself as **close (S/L ratio exit)**.

### Alerts
A bot tells you what it does through the Discord and Telegram you set up under **Settings → Notifications** (see [Alerts](alerts.md)). The **Alerts** section of the bot page sets how they look and where they go.

#### Alert name and the phone's lock screen
Each message is written to be read from a phone's lock screen, which only shows a few lines:

```
✅ close filled · IC Joint
The side with SPX 5700P closed at 0.55.

Trade: Iron condor · ••1234 · run 42
Legs: −1 SPX 13 Oct 5700P · +1 5675P · −1 5800C · +1 5825C
Protection: Put side closed · Call side stop 4.80 · target 0.90
```

- **The first line** is an icon, what happened and the bot's **alert name** (up to 40 characters; the bot's name if you leave it blank). 🔴 is a problem, ✅ is good news, ⚠️ is worth a look.
- **The second line** says what happened in full, or what to do.
- **Below it**, when the alert is about a trade: the bot and run (your account by its last 4 digits only), the legs, and each side's stop and target. "stop NONE" means that side has no working stop at your broker.

#### Where routine alerts go
**Routine alerts go to** is **Every channel** (the default), **Discord only** or **Telegram only**. Routine means good news with nothing left for you to do: a close, profit target or timed close that filled, a profit action that moved the stop, a default alert going off. For example, set a busy bot to "Discord only" to keep its fills off your phone.

Problems always go to **every** channel, whatever you choose: a side with no stop, an order whose outcome is unclear, Freeze holding a stop back, a side held back, a close that failed or didn't fill, anything that needs you. If the channel you chose isn't set up, routine alerts go to the ones that are, so nothing is lost.

#### Default alerts
Alerts the bot makes for each trade when a live entry fills. Turn the **Default alerts** pill on and its rows appear; pick a row's kind and only its boxes show (above or below and a %, or the points). They work like the alerts you set yourself: they show in the app's alerts, and are sent like routine alerts (tick **In the app only** to never send them). Up to 10:
- **P&L %, above or below:** the trade's profit or loss as a share of what it took in (or cost), measured from what it would cost to close it now (its legs' middle prices). Example: a trade that took in 2.00 with "above 25" goes off once it can be closed for less than 1.50 (25% kept); "below −50" goes off once closing it costs more than 3.00 (half the credit lost). A trade that took in (or cost) less than $50 in all gets no P&L % alert.
- **Near a short strike:** the underlying within that many points of each sold strike. Example: 10 on an SPX trade with a 5,700 short put and a 5,800 short call makes two alerts: SPX below 5,710, and SPX above 5,790. 0 means at the strike. One that's already true when the trade fills isn't made.

They're named after the bot and run ("Iron condor · run 42 · P&L ≥ +25%"). A trade filled in pieces gets one set. Once every side of the trade is closed, its default alerts are switched off and cleared. Paper runs never make them.

### Schedule
The Schedule shows only what you're using: the entry window's times only while **Use an entry window** is ticked (and then the entry times hide), only the fields for the frequency you pick, and the blackout dates only while **Use blackout days** is ticked. Anything hidden is kept, so switching back brings it back. Press **ⓘ** next to a field for what it does.

#### Entry window
Instead of fixed times, a bot can use an **entry window**: tick **Use an entry window** and give a start and an end, say `11:30` to `11:45` (New York, from 09:30 to 15:57, the start before the end).
- **Once a minute** from the start to the end (both included), the app checks everything an entry time checks: the switches, blackout days, the limits, the market filters, then the strikes and the premium bounds.
- **The first minute that passes enters**, and that's the bot's one entry for the window that day. If its order doesn't fill, the next minute tries again. If something goes wrong (the broker refuses the order, say), the window is over for the day, so a refused order is never sent again every minute.
- **If the app can't read the option chain** at a minute, that minute is a miss too and the next minute tries again; you get one alert that day.
- **A minute that doesn't pass leaves no run.** The bot page shows the last one: "Last check 11:32: VIX level filter: VIX 21.3; above max 20". If no minute passed all day, the last minute leaves one **skipped** run: "Window 11:30–11:45 closed; last check 11:45: …".
- **While the window is on, the entry times are ignored,** and so is each time's own contracts: the bot's contracts are used. The times are kept, so unticking the window brings them back.
- With a timed close, the window must end before the close time.

Example: a window of `11:30` to `11:45` with a VIX maximum of `20`. VIX is `21` until 11:37 and `19.8` at 11:38, so the bot enters at 11:38 and doesn't check again that day.

#### Entry times
A list of times in New York time, such as `09:45`, each with an on/off switch. Press **+ Add entry time** for another row and **✕** to remove one. Times run from 09:30 to 15:57. The app doesn't open anything in the last 2 minutes before the close.

You can skip the colon: `945`, `9:45` and `09:45` all mean 09:45, and `1314` is 13:14. The box shows `09:45` once you leave it. (Three digits are read as written: `130` is 01:30, not 13:00.) The entry window's times read the same way.

**Seconds, if you want them.** An entry time can name a second, in 15-second steps: `9:30:15` or `93015` is 09:30:15, which suits an entry just after the open. Other seconds, like `09:30:10`, aren't accepted. The app's clock checks every 10 seconds, so a time with seconds enters within about 10 seconds of it. The entry window works in whole minutes only.

Each time can also have its own **contracts**. Leave contracts blank to use the bot's contracts. A bot sized by buying power has no contracts box on its times. Say the bot's contracts is 2: a `09:45` time with 1 contract buys 1, a `10:30` time with 4 buys 4, and a `11:15` time left blank buys 2.

**The per-trade limit has the last word.** If a time asks for more than the bot's **per trade** limit, the app sends the limit instead and notes on the run that it cut the size down (for example "asked for 4, sent 3"). A time can't ask for more than the bot's **per day** limit, though: the page won't save it ("entry time 09:47: 4 contracts is more than the daily limit of 3"). Contracts must be a whole number from 1 to 100; anything else, such as `two` or `0`, is shown as a problem rather than quietly treated as blank, and the same goes for a premium bound that isn't a number.

#### How often
- **Every trading day** (the default).
- **Certain weekdays:** press the days you want, say **Mon** and **Thu**; the chosen days light up. Only Monday to Friday are offered, since the market is shut at weekends.
- **One day a month:** the first to the fifth Monday to Friday of the month, say the third Friday. There is no "last". A fifth Friday only exists in some months; in the others the bot doesn't run.
- **Specific dates:** a list such as `2026-10-14`, one per line or separated by commas or spaces. You can paste a list; it is sorted and repeats are dropped. You can also tick **FOMC days**, **CPI days** and **Last trading day of the month** (see [Date lists](#date-lists-fomc-cpi-end-of-month-and-market-holidays)): the bot then also runs on those days, without you typing them.

On a day that isn't one of the bot's days, **nothing is recorded**: the bot simply has no run that day. (Blackout days, below, are different: they show up as skipped runs.) Existing bots keep running every trading day.

#### Blackout days and Skip a day
**Blackout days** are dates the bot won't enter, say a holiday week or a day you want off. Turn **Use blackout days** on and list the dates. On a blackout day each entry time is recorded as **skipped** ("today is a blackout day"), so you can see it. A blackout day wins over a specific date that falls on the same day. **Preview order ignores blackout days.**

**Skip a day** is a shortcut for one-off days. On an **armed** bot's page, the button reads **Skip today**, or **Skip Mon 10/13** once today's session is over (it skips the next day the market is open). Press it and the bot shows **Skipping Mon 10/13** with an **Undo**. On the Automation list, tick several armed bots and press **Skip day** in the bar that appears at the bottom to do them all at once (there is no bulk undo). Skipping adds the date to the bot's blackout days; Undo takes it out again. The bot stays armed and any trade it has open is still looked after.

If blackout days was off but old dates (or ticked lists) were still there, Skip a day clears those first, so they don't suddenly start applying. That clearing is permanent: Undo takes the skipped day back but doesn't tick the lists again. Dates that have passed are dropped.

You can also use lists under blackout days: **FOMC days**, **CPI days**, **Last trading day of the month** and **Market holidays**. Each list has two boxes, and you can tick either or both:

- **Entry day** skips the entry when the list's day **is** the day the trade would open. The run is skipped with the list named, for example "today is a blackout day (FOMC)".
- **Before expiration** skips the entry when the list's day falls **after** the day the trade would open, up to the day it expires. The run says which day, for example "today is a blackout day (FOMC on 2026-10-28, before expiration)". The bot can still enter **on** the day itself, unless you tick Entry day too.

The app works out the expiration from the bot's legs: the entry day plus the longest leg's DTE, moved on to the next day the market is open if it lands on a weekend or holiday. A **0DTE** bot expires the day it enters, so Before expiration never skips it.

**Market holidays** only has Before expiration: there's no trading on a holiday, so it can't be the entry day. Tick it to skip a trade that would be held over a day the market is shut, like a Friday calendar whose back leg expires after a Monday holiday.

#### Date lists: FOMC, CPI, end of month and market holidays
- **FOMC days** are the days the Federal Reserve announces its rate decision (the second day of each of its meetings, at 2 p.m. New York).
- **CPI days** are the days the monthly inflation report comes out (8:30 a.m. New York).
- **Last trading day of the month** is the month's last day the market is open, so a month ending on a Saturday uses the Friday before.
- **Market holidays** are weekdays the market is closed all day (from NYSE's holiday calendar). A half day, like the day after Thanksgiving, isn't one. Blackout days only.

You don't type these dates; the app reads them (see [The market calendar](#the-market-calendar)). Say FOMC days is ticked as **Entry day** and the Fed announces on Wednesday the 28th: on the 28th every entry time is skipped, and the other days run as usual. Tick it as **Before expiration** instead on a bot whose trades last 7 days: from Wednesday the 21st to Tuesday the 27th it doesn't enter, because those trades would still be open on the 28th, but on the 28th itself it does.

**When the app can't tell.** If the app hasn't been able to read a list recently, it can't say whether today (or, for Before expiration, a day before the trade expires) is in it. Then:
- a bot that **blacks out** that list **doesn't enter today**, with the reason "FOMC dates unavailable today, so the blackout can't be checked", because it might be one of the days you asked to avoid;
- a bot that **only trades on** that list's days doesn't trade today;
- either way you get **one alert that day** for each list, saying which bots use it. Open **Settings → Market calendar** and press **Refresh now**.

This costs trading days when it happens. Without a FRED key, CPI days come only from ForexFactory's current week, so if ForexFactory can't be read for a while, a bot that blacks out CPI days skips every day until it can. On a fresh install, a bot using a list skips its entries until the app's first read of the calendars has finished (usually within a minute of starting).

If the Fed has an FOMC day (or FRED a CPI day) this week that ForexFactory doesn't list, the day still counts and you get one alert: ForexFactory may have renamed the event.

### Filters
**Check the filters now** (on a saved bot with the VIX level or VIX9D/VIX ratio filter on) reads VIX and VIX9D once and says how those filters read right now, for example "✓ VIX 18.50 (as of 10:31:05 ET)" or "✗ Would not trade: VIX 31.20; above max 30". After hours the prices are old, so it adds "stale: a live entry would skip". It only reads prices; nothing is sent or saved.

#### Premium bounds
Skip an entry when the trade's premium isn't in a range you set. Turn the **Premium bounds** pill on and its boxes appear: set a **minimum**, a **maximum**, or both. There is no credit or debit choice: the bounds are on the **size** of the price, whichever side the trade comes out on. Say the bounds are at least `$1.00` and at most `$6.00`. The app checks the strikes it picked, using the live middle prices before any chase concession:
- Net credit `$0.85`: skipped, "net credit 0.85 < min 1.00".
- Net credit `$6.10`: skipped, "net credit 6.10 > max 6.00".
- A net debit of `$6.10` is skipped the same way: "net debit 6.10 > max 6.00". A net debit of `$1.20` is let in.
- Exactly `$1.00` or `$6.00` is fine.

Preview order ignores the bounds, so you can still see what the strikes would be.

#### Market filters
Five more filters, each with its own pill: VIX level, VIX overnight move and VIX9D / VIX ratio under **VIX**, and technical indicators and intraday movement under the underlying. Turn a pill on and its boxes appear; the ⓘ beside each explains it, with an example. They're checked just before the bot looks at the options, only for **scheduled** entries; **Preview order ignores them**. A filter that is turned off keeps its numbers, so you can turn it off for a day and back on. A filter turned on with nothing filled in won't save. The prices come from TastyTrade's market data at the moment of the entry; if TastyTrade isn't connected or can't give them, the app asks Schwab instead (except for futures, which only TastyTrade has).

- **VIX level:** enter only while VIX is between a minimum and a maximum (either can be blank). With a maximum of `20`, an entry when VIX is `21.30` is skipped: "VIX level filter: VIX 21.3; above max 20".
- **VIX overnight move:** VIX's move from yesterday's close to its opening price at 9:30 (the open of its 9:30 one-minute bar), in %. If that 9:30 bar is missing, a later minute's price is never used instead: the filter can't be checked and the bot doesn't enter. Four boxes, **Up** and **Down** by **Min** and **Max**, all positive numbers:
  - **Max up** stops entries after a rise bigger than that; **max down** after a fall bigger than that. Each only limits its own direction.
  - **Min up** needs a rise of at least that; **min down** a fall of at least that. With both minimums, either one is enough.
  - Example: yesterday's close `15.00`, today's first price `15.60` is `+4.00%`. With max up `3`, the day's entries are skipped: "VIX 15.00 -> 15.60 (+4.00%); outside max up 3.00%". With max down `3` only, it runs (a rise isn't limited by max down).
  - An entry at 9:30 waits up to a minute for VIX's first price.
- **VIX9D / VIX ratio:** the 9-day VIX divided by VIX at the entry. VIX9D `14.50` and VIX `16.00` is `0.906`, so a maximum of `0.90` skips it.
- **Technical indicators** on SPX:
  - **SMA (days):** today's price and the previous days' closes, averaged. A 3-day SMA at 1 p.m. is (the 1 p.m. price + yesterday's close + the close before) ÷ 3. Say SPX is at `5,800` and closed at `5,790` yesterday: the 2-day SMA is `5,795`, so **price above** passes and **price below** is skipped. Above or below needs at least 2 days; up to 200.
  - **EMA (minutes):** an average of the 1-minute closes (today's and the previous session's), with more weight on the latest. Up to 390 minutes.
  - **Compare** enters only when the first average is above the second, for example a 5-day SMA above a 20-day SMA. Its second box (**> days** or **> minutes**) appears once you choose Compare.
  - With both SMA and EMA set, both must pass. Equal never passes.
- **Intraday movement:** SPX's move from today's open (the open of its 9:30 one-minute bar; if that bar is missing, the bot doesn't enter) to the price at the entry, with the same four boxes as the VIX overnight move. Open `5,000`, now `5,016` is `+0.32%`: with min up `0.3` the bot enters; with min up `0.5` it's skipped.

**If the prices can't be read** (neither TastyTrade nor Schwab can give them: neither is connected, a price is more than 5 minutes old, the history didn't all arrive or has no 9:30 bar, or Schwab's previous close isn't from the last trading day), the filter **doesn't let the entry through**. The run is skipped with the reason, naming the broker it last asked, for example "VIX level unavailable: no VIX price from Schwab. Not entered.", and you get **one alert per bot and filter that day**. A filter that simply isn't met (VIX too high, say) is only shown on the run, with no alert.

### Chase settings
Orders are chased: the app starts at the fair price, and every few seconds, if it hasn't filled, moves the price a step toward the broker's side. You set the starting give-up, the step, the seconds between tries, and the number of tries. The defaults are fine to start: for entries, steps of `$0.05` every 10 seconds, up to 6 tries; for exits, every 5 seconds.

**Stop after (minutes)**, on the entry row, ends an entry's chase after that long even if it has tries left: the order is cancelled, the cancel confirmed, and whatever filled is kept. Example: `3` with 10-second steps stops after about 18 tries. Blank: only the tries count.

#### If a close hasn't filled after the last try
This is what happens when a side's profit target is reached and the closing order still hasn't filled after the exit chase's last try. You choose one of three under **Then** (the minutes box shows only for the natural price):

- **Give up and put the stop back:** the closing order is cancelled, the side's stop goes back for what's still open, and you get an alert. Bots made before this choice existed work this way.
- **Natural price, then leg out** (new bots start with this):
  1. **The natural price.** For the minutes you set (2 to start, up to 30), the close is offered at the *natural* price: what it costs to buy the sold legs back at their asking price, less what the bought legs sell for at their bid. If prices move, the order follows them. Say the short is asked at `0.65` and the wing is bid at `0.10`: the close is offered at `0.55`.
  2. **Legging out.** If that doesn't fill either, the close is cancelled and the options are closed one at a time, **sold legs first.** Each is offered from the middle of its bid and ask, stepping up by the exit chase settings, and after the last try it is bought back **at market**.
  3. **Then the bought legs.** Only once every sold leg is bought back are the bought legs sold, each the same way as a chased wing after a stop.
- **Keep lowering the price until it fills:** the close carries on past its last try, one step at a time (the exit chase's step, every wait), but never past the natural price. If it reaches the natural price it waits there, and follows it if prices move. It never stops trying until it fills, you cancel it, or the market closes; at the close it is cancelled and the stop goes back, as with Give up. Example: the last try paid `1.50` and the natural price is `1.60`: it offers `1.55`, then `1.60`, and waits at `1.60`.

**Leave long legs with no bid to expire:** tick this under the exit chase and, when a close runs, a bought leg that nobody is bidding on is left out of the close and expires on its own instead of being sold for nothing. Sold legs are always closed. Leave it unticked and every leg is closed, as before. It never applies to a close you start yourself or to a close of only part of a group (a profit or time action that closes a share): those close every leg. The run's timeline shows "Left … to expire: no bid" for each leg left out; it stays open in the Journal until it expires and is settled. If every leg of the group is a bought leg with no bid, nothing is sent and the close tells you why.

**First, at market (one-leg groups):** turn this pill on and a close of a group with a single option that still hasn't filled after its last try is sent **at market** for what's left, during the market's regular hours, before the choice above runs. A bought option with no bid is never sold at market (your broker would cancel it): the choice above runs instead.

**An order with two or more legs is never sent at market.** Only a single option ever goes at market. The app never sells a bought leg while a sold one might still be open, and if anything looks off (the app can't tell whether an order reached your broker, the short's market order hasn't filled, or someone else changed the position), it stops, sends nothing more and tells you to check your broker. After the market closes it never starts the natural price or legs out: it puts the stop back instead. If the market closes after the short is bought back but before the wing is sold, the wing is not sold at market: what's left is offered at one tick (`0.05`), or, if the wing sale hadn't started yet, it isn't sold and the alert tells you to check your broker.

The page checks everything when you save and shows what to fix beside each field.

## Manual bots

A manual bot is a named set of exit rules for a trade you already have. It never opens a trade. On the Automation page, under the bots, **Manual bots** lists them; **New manual bot** makes one.

A manual bot holds:
- **Profit target** and **Stop loss**, each in % or $. Percent is measured from the trade's net credit or debit when it filled. Leave one empty to skip it.
- **Exit time:** tick it, then choose how many days before expiration (0 is the expiration day) and the time in New York (15:45 to begin with). Off unless ticked.
- **Alerts:** the same rows as a bot's default alerts (P&L %, or near a short strike).

A manual bot with no target, stop or exit time only sends alerts.

A manual bot can't manage a trade of more than 100 contracts or more than 4 legs, a trade whose sold leg expires after its bought leg (a reverse calendar), or options on an index the app's bots don't trade (such as RUT or NDX). It tells you why when you try.

Changes apply to trades you attach from now on. Trades already using this bot keep their rules. Deleting a manual bot is the same: trades already using it keep theirs.

## Bigger entries and partial fills
An entry of more than 1 contract can fill in pieces. The app never changes the price of a partly filled order, because the change would be for the full size. Instead it works in **tranches**:
1. It cancels the order and confirms the cancel.
2. **What filled is a trade of its own.** It shows in your journal as its own trade and gets its own stops and targets, based on its own fill price.
3. **The rest is tried again** as a new order, at the price the chase had reached, using the same number of tries (they are not reset).
4. If the tries run out, whatever is still unfilled is given up and you get an alert such as "Entry short: 2 of 3 filled — rest given up".

The stops and targets for a piece are put in **after the whole order has finished** (filled, or given up), not the moment that piece fills. Until then that piece has no stop. The runs page lists the later pieces under the first one, marked "tranche 2" and so on.

If the broker reports the legs filling **unevenly** (for example the short call filled but the long call didn't), the app records and protects only the **whole units** (complete sets of every leg at its ratio) and alerts you about the leftover contracts, which it does not track or protect. It then **keeps buying the rest** of the entry as usual, as a new tranche, so you may end up with the leftover contracts on top of a full-size entry: settle them at your broker. If there is not even one whole unit, the run shows **Needs you: The entry's legs filled unevenly**, nothing else is sent, and the app's recovery never touches it. Fix it at your broker.

## Limits on entries and open trades
Both are checked at each scheduled entry, after the daily contract limit, and both are optional.
- **Max entries per day:** how many entries the bot may take today. An entry that was split into tranches counts once. An entry still in progress counts (a Paper bot's too), so two entry times close together can't both slip under the limit. Skipped, missed and error runs don't count, and **Preview order saves no run.** At the limit the run is skipped with "already at 2 entries today".
- **Max open trades:** counted from the bot's own runs, as their orders fill, so you don't need to sync your journal for it to be right. A trade stays open until its options expire (after the close on its expiration day) or every side of it is closed: a side closes when its profit target fills, or its stop fills and its wing is sold (or was already gone). If the bot splits its iron condors, each side that is still open counts as **1**, so a fresh condor counts as **2**; otherwise each trade counts as 1 while any side is open. An entry that filled in tranches counts once, by its largest piece. At or over the limit the run is skipped with "already holding 2 open trades". If the app can't count them, it doesn't enter.

Preview order ignores both.

## Preview order
**Preview order** is on the bot's page (and in the bot list). It shows the order the bot would send right now, **without sending it**: it picks strikes from the live option prices, works out the price and size, and asks your broker to check the order. The page shows the strikes it chose, the price (a credit or a debit), how many contracts, what your broker said, each group's **exits** (its stop and profit target as prices on this order, and which one rests at the broker), and a **risk graph** of the trade at the middle prices, the same graph as in the Journal. It works the same for a Live bot and a Paper bot, and sending nothing is guaranteed: it only ever asks the broker to check.

**It saves no run.** Nothing is added to the bot's runs, the Journal or its Paper trades view, and it doesn't count against the bot's daily limit, entries per day or open trades. It ignores blackout days, the schedule, the market filters and premium bounds, but it still obeys Freeze (and your size limits). It works **when the market is closed** too: it picks from the last prices the broker has, so outside market hours the strikes and price are only a guide to what the bot would do at its entry time. Use it to check your settings. Older preview runs from before this button are still in the runs list.

## Trade now
A **live** bot's page has **Trade now**: it sends the bot's entry now, off its schedule, as a real order. Type `TRADE` in the box first (and tick the "I understand" box if the app watches this bot's stops).
- Pressing it is your choice to trade now, so it works on a bot that isn't armed and ignores the schedule, blackout days, the market filters and the premium bounds.
- It still obeys Freeze and No new trades, the market's hours, and the contract limits (per trade and per day) and Max open trades.
- It counts as one of today's entries (the button asks you to confirm that), and while it's being entered it counts as an open trade.
- Only one Trade now of a bot runs at a time: pressing it again, or from another tab, is refused until the first has finished.
- The page takes you to the new run, which shows what happened.

A Paper bot has **Preview order** instead, and its entries are pretend fills.

## Live and Paper
Every bot is either **Live** or **Paper**. The list shows which, and so does the bot's page.
- A **new bot starts Live and not armed.** So does an imported bot. A **clone** keeps the mode of the bot it was copied from (and is off). Nothing runs until you arm it.
- A **Live** bot sends real orders when armed. On the bot's page the button says "Arm it (sends real orders)". Arming a Live bot always shows a page first: the account it will trade, the most it can send per trade and per day, and where its stops are held (at your broker, or watched by this app, which needs the box ticked). You confirm there.
- A **Paper** bot pretends. At each entry time it does everything a Live bot does, up to the order: it follows the bot's own limits, filters, blackout days and **Skip a day**, and **No new trades** and **Freeze all orders** still stop its entries. It asks your broker to check the order, as Preview order does, and if the broker would refuse it the run is skipped. If the check passes, nothing is sent: the app records a pretend fill at the **mid price** (halfway between bid and ask) of each leg, using the live prices the app already streams. If a price is missing or old, no pretend fill is made.
- A Paper bot never chases an entry or leaves a limit order resting. The fill is at the mid, at once.
- **Several bots at once:** on the Automation list, tick the bots (the box at the top of the list ticks them all, or none) and use the bar at the bottom. **Turn off** and **Paper** happen straight away. **Arm** and **Live** first show one page listing every ticked bot: its mode, account, the most it can send per trade and per day, and where its stops are held. You tick the box there and confirm. Arm skips bots that are already armed, and names any it can't arm (archived or futures bots). Live closes each bot's paper trades and leaves them switched off, as it does for one bot.
- Switch with **Switch to Live** or **Switch to Paper** in the bot's page, "This bot" section. A bot is also set to Paper (and switched off) when you change its broker or underlying, or change it so the app now has to watch a stop it did not before, so you look at it again before it sends anything real.

### What a Paper bot does after it enters
The app watches the pretend trade to its end, every 2 seconds, from live prices:
- **Every stop and target is watched by the app**, including the ones a Live bot would rest at the broker. A trade's own "stop off" or "target off" is honoured.
- It also follows profit actions, watch rules, the timed close, time actions, the close-days-before-expiry setting and expiry.
- A level counts only after **two readings in a row**, so one stray price does not close a trade (the delta and short/long ratio watch rules also wait 30 seconds, as on a Live bot). Exits are filled at the mid.
- A trade still open when its options expire is settled at its **intrinsic value** from the last price the app saw of the underlying.
- If the price feed drops out, you are alerted. The app never goes back and fills the gap: a stop that would have hit in the gap is not applied after the fact. This is one more way paper results are kinder or harsher than real ones.
- **Freeze all orders** stops new paper entries (they need the broker's check), but it does not stop paper exits: Freeze stops the app sending orders, and a Paper bot sends none. Switching the bot off stops its new entries, as for any bot; its open paper trades are still watched to their end. The bot's limits (daily size, **Max entries per day**, **Max open trades**) count its own paper trades, and only its own.
- Alerts about a Paper bot start with "Paper:" and go to the bot's usual channels.

### Reading paper results honestly
Paper trades are pretend. Fills are estimated at the mid price, and stops are watched by the app instead of resting at the broker, so real results would differ. Real fills are usually a little worse than the mid, and a stop resting at the broker can fill at a different price than one the app watches. Treat paper results as a check that the bot does what you meant, not as a forecast of profit. The run page and the bot page of a Paper bot say so too.

Paper trades live in the Journal's own [Paper trades](journal.md#paper-trades) view. They never appear in its other views, in its totals, on the dashboard's real numbers, or in a bot's Live record. On the Automation list, a Paper bot's P/L and win rate are its paper numbers, marked "paper". A trade closed exactly at break-even counts as closed, but not as a win.

### Switching to Live
Switching a Paper bot to Live first closes its open paper trades at the mid, with the reason "switched to Live" (or "(last price)" if the app has no fresh price for a leg), so pretend and real trades never mix. A paper trade the app never got any price for closes where it opened, with no gain or loss, also marked "(last price)". The paper history stays in the Paper trades view. If the paper trades can't be closed, the bot stays in Paper and the page says so.

Switching to Live also **switches the bot off**, even if it was armed as a Paper bot. To start it again, arm it: you get the confirmation page (account, limits, where stops are held) and tick the box that you understand it sends real orders.

A paper trade's run page has no "Manage this trade" buttons: nothing is at your broker to close or change. Its pretend stop, target and rules run on their own.

## Arming
A bot only runs at its entry times while it is **armed**. Arm and disarm it on its page or in the list. A bot you've just built isn't armed, so nothing happens until you say so. The app has to be running at the entry time; if it was closed or busy for more than 3 minutes past the time, that entry is **skipped** (never fired late).

## What happens at an entry time
(With an entry window, the same happens at each minute of the window, until one enters.)
1. The bot checks it may run: it's armed, the market is open, orders aren't paused, it isn't a blackout day (typed or from a ticked list), it's under its daily limit, it's under the entries-per-day and open-trades limits, and, last, its market filters pass. If not, the run is **skipped** with the reason. (A day outside the bot's schedule gets no run at all.)
2. It picks the strikes, checks the premium bounds and works out the price. A **Live** bot first asks your broker again for the latest bid and ask of each option it picked, a second after reading the option prices, so one old or wide price can't start the order too far from the market. If any of the options has no proper bid and ask just then, the prices it already read are used. The run's log lists any option whose price changed.
3. A **Paper** bot has the broker check the order (it is never sent), then records a pretend fill at the mid price, and stops there. A **Live** bot sends one order with all the bot's legs.
4. **The order is chased.** If it hasn't filled after a few seconds, the price is moved a step, up to the number of tries you set. If part of the order fills, what filled is kept and the rest is tried again (see [Bigger entries and partial fills](#bigger-entries-and-partial-fills)).
5. If it never fills, the run is **missed** and you get an alert. Nothing is left working.
6. Once it fills (or the chase ends with part of it filled), the app waits until your broker shows the new positions, then puts the protections in place:
   - **A stop for each group**, worked out by the style you chose. A group of one sold leg set to rest its stop has it resting at your broker on the short option (TastyTrade or Schwab), where it works even if the app is closed. Every other group's stop is watched by the app instead (see [Which exit rests at the broker](#which-exit-rests-at-the-broker)). A stop that can't be used isn't sent (see [A stop that can't be used](#a-stop-that-cant-be-used)).
   - **A profit target for each group.** Your broker takes only one closing order on the same contracts, so a group's target rests at your broker when its stop doesn't, and the app watches it when the stop rests there (see [Which exit rests at the broker](#which-exit-rests-at-the-broker)). A group of more than one leg always rests its target.
7. **When a resting stop fills,** the app cancels that group's target, and closes the group it was set to close, if any, as that group says: sold now, at a limit, chased or left open (see [When another group's stop closes it](#when-another-groups-stop-closes-it-how-its-legs-are-closed)). A stop the app watches closes the whole group instead. The other groups aren't touched. (A trade opened before 15 October 2026 with a spread resting its stop deals with the spread's wing the same way.)
8. **When a target is reached,** the app cancels that side's stop and closes the side. It checks the target on two fresh price readings in a row, using the cost to buy the short back at its asking price less what the wing would sell for. If the close doesn't fill, it does what you chose in [If a close hasn't filled](#if-a-close-hasnt-filled-after-the-last-try): puts the stop back, goes to the natural price and then legs out, or keeps lowering the price until it fills. The close runs on its own, so the app keeps watching your other trades meanwhile. While it runs you can **cancel** it from the run page (see below).
9. **If a stop disappears** without the app cancelling it (the broker cancelled it, say), the app puts it back, up to 3 times a day per group, sized to what's still open. It's put back only while you still hold the stop's option **sold**: if it's gone, or you now hold it bought, there's nothing for a stop to protect and none is sent. After 3 times it tells you and leaves it to you.
10. **On the evening the options expire**, after the market closes, your broker expires the stops along with the options. The app marks them **expired** and doesn't put them back or alert you.

**A trade keeps the setting it started with.** If you switch a Live bot to Paper, or change its account, while one of its trades is open, that trade's stops, targets and wing sales still happen for real, in the account it was opened in. The switch only affects the bot's next entries. (The other way round, switching a Paper bot to Live ends its open paper trades; see [Live and Paper](#live-and-paper).)

If the app can't be sure what happened with an order, it never guesses: it leaves the run as **running**, alerts you to check your broker, and settles it on its own when it can see the answer.

If the app was closed or crashed just after a trade filled, it checks when it starts again (and every 30 seconds): a filled trade whose stops may not have been placed gets one alert, **"Needs you: exits may be missing — check your broker"**. The app doesn't place them again by itself, because a stop may already be at your broker. Look at your broker and add any missing stop there.

### No new trades and Freeze
See [Pausing trading](pausing-trading.md).
- **No new trades:** bots open nothing new, but running bots keep their stops, targets, re-arming and wing sales working.
- **Freeze all orders:** the bots send nothing at all, and the app stops watching. Stops already at your broker stay there. If a trade fills while orders are frozen, its stops **wait** (you get an alert) and are placed once you unfreeze; until then that trade has no stop. Manage your trades in your broker's app until you unfreeze. If such a side reaches its target and its close doesn't go through, its waiting stop goes back to waiting and is placed at the app's next check; the alert says that side has no stop until then.

## Schwab bots
A bot can trade a Schwab account just as it trades a TastyTrade one, and **its exits work the same way**: a group that rests its stop at TastyTrade rests the same stop at Schwab (a stop market order on the sold leg), a group that rests its profit target rests it at Schwab, and everything else is watched by the app, exactly as on TastyTrade (see [Which exit rests at the broker](#which-exit-rests-at-the-broker)). A stop resting at Schwab works even when your computer is off.

So a Schwab bot shows the yellow "watched by this app" box, and needs a box ticked to arm it, make it live or use **Preview order** while it's live, only when one of its groups has a stop the app watches, the same as a TastyTrade bot.

**Trades opened before this change.** Until this version every Schwab bot's stops were watched by the app, with nothing at Schwab. A Schwab trade that was already open when you updated **keeps its stops watched by the app until it closes**: nothing is placed at Schwab for it, so it never gets a second stop. Keep the app running and the computer awake until those trades are closed. New trades get their exits the new way.

#### How the app watches a stop
This applies to every stop the app watches, at either broker. The app reads the short option's price every 2 seconds while the market is open (for a group with no single short, every leg's price and the group's whole loss). When its middle price is at or past the stop on two readings in a row, the app closes that group: the group's close, chased, and if that doesn't fill, **always** the natural price and then legging out, which ends by buying the short back at market. That's whatever you chose in [If a close hasn't filled](#if-a-close-hasnt-filled-after-the-last-try) for other closes: a stop has to close. Example: a stop at `$4.80`; the short's price reads `$4.90` and then `$4.95`, two seconds apart; the app closes the side.

**So a watched stop only works while this computer is on and awake, and the app is running and connected.** If the computer sleeps or the app stops, nothing closes that group. Keep the computer awake while such a trade is open (the menu bar app's **Keep awake** helps).

The app tells you whenever the watching can't work:
- **Stale prices:** if a short's price is missing or old, its stop can't fire (an old price never closes anything). "Old" is more than 30 seconds, or, while the price stream is clearly working (SPX updating through it), more than 5 minutes: a quiet option's price doesn't change often. After a minute you get an alert, "stop not watched (stale price)", and another when prices come back.
- **A gap in watching:** if the app wasn't watching for more than a minute while the market was open (the computer slept, the app was closed or stuck), you get an alert with the times, say "stops weren't watched from 10:31 to 10:45". The same check runs when the app starts. A stop still past its level when watching starts again closes on the next two readings; a price that went past a stop and came back during the gap isn't acted on.
- **Freeze:** with Freeze on, nothing is sent. A stop that's reached then sends nothing and alerts once; it closes once you unfreeze, if it's still past its level.
- **A close that keeps failing:** a stop's close can still fail to start (the app can't read your positions, the market closed, the order is refused). The stop is then watched again, without counting as one of the side's 3 re-arms a day. If a side's stop has started 3 closes in a day and the side still isn't closed, the app stops watching it, marks it failed and tells you: that side has no stop until you set one or close it.

Changing a watched stop in **Manage this trade** just changes the level the app watches; nothing is sent. Changing a stop that rests at Schwab cancels it there and places the new one, as at TastyTrade. The runs page shows a watched stop as **watched by the app**.

**Schwab's weekly login.** Schwab asks you to log in again every 7 days. The app reminds you **48 hours** and **12 hours** before, and once more if it runs out. Reconnect Schwab in Settings → Brokers. Once it has run out, a Schwab bot can't send anything: stops and targets already resting at Schwab stay there and still work, but a stop the app watches can't close a trade, and nothing new is placed or moved.

## Today's schedule
Press **📅 Schedule** at the top of the Automation page or the Journal. A panel slides in from the right with one line for each entry time of every armed bot (or one for a bot's entry window). Each line says what will actually happen, not just what is set. Each line shows the time, the bot's name with **Live** or **Paper**, what it trades and how much (like "SPX · 1 contract"), and its account and tags. The coloured dot and the badges at the top show the state:
- **pending**: still to come;
- **watching**: an entry window that is open now, with its last check, like "last check 10:31 · VIX 31.2; above max 30";
- **running**, **done**, **not filled**, **skipped** (with the reason) or **error**, with a link to the run;
- **missed**: the time passed and nothing ran (for example the app wasn't running then);
- **NOT scheduled**: the bot clock has stopped, so this won't fire until you restart the app.

When the app has checked that its bot clock is running, a green line says **✓ Every entry below has a live scheduler job.**

**Economic events** for the day shown sit above the lines, as small labels with the time in New York: red with 🟥 for high-impact releases (like "🟥 08:30 CPI" or "🟥 14:00 FOMC"), amber for medium ones (like "08:30 Unemployment Claims"). Hold the pointer over one for each release behind it with its forecast and the previous figure, for example "CPI m/m · fcst 0.3% · prev 0.4%". This week's come from ForexFactory; for later weeks only FOMC and CPI days show, at their usual times (see [The market calendar](#the-market-calendar)). They're there to read; whether a bot trades on those days is up to its [date lists](#date-lists-fomc-cpi-end-of-month-and-market-holidays).

On a market holiday the panel names it ("Market closed: Thanksgiving Day. Nothing trades on this day."), and on a half day it says when entries stop ("Early close at 13:00: entries stop at 12:58 ET."). A day further ahead than the holiday calendar reaches says so: it's treated as a trading day.

The tabs **All**, **Live** and **Paper** show every bot, only the ones that trade for real, or only the paper ones; each tab says how many lines it has, and the badges and counts follow the tab. The panel remembers the tab you picked.

Use **‹** and **›** to step a weekday at a time, or pick any day in the date box; **Today** brings you back. Another day is a forecast: there, pending reads **scheduled**, and the market filters, Freeze and No new trades are only decided when each entry fires. Lines that won't trade at all that day (a blackout day, Freeze or No new trades on, past the early close) are counted but not listed.

**Skip day** under a time, on today or any later day, adds the day shown to that bot's blackout days, so none of its entries open that day. The panel then shows **Undo**. Open trades keep being managed. Press Esc, ✕ or anywhere outside the panel to close it.

For example, at 9:00 a bot with entries at 9:47 and 12:30 shows "2 pending"; at 10:00, after the first filled, "1 done, 1 pending".

## The runs page
Each bot has a **Runs** page, newest first, and each run has its own page: the strikes, the entry price and what it was priced as, what it filled at, **Orders, step by step**, the exits (stops, targets, wing sales, timed closes and time actions) and the matching rows of the order log. The exits table has a **How** column that shows the arithmetic behind each stop, for example `short 2.50 × (1 + 95%) = 4.875 · → 4.80 (rounded down to the 0.10 tick)`, and, when a stop wasn't placed, **not placed** with the reason. A wing left open shows **left open**. Trades a bot opened show a **bot** badge in your journal, and the bot's tag.

### A run's page, top to bottom
Open a run with **Details** on the Runs page, or from a link in an alert, the Journal's gear panel (**Open full page**) or **Open bot trades**. **All runs** at the top goes back to the list.

- **A banner**, while a watch rule is closing the trade (see "The watch-exit banner" below). A Paper run also says that paper trades are pretend.
- **The facts:** the **Status** with **Live** or **Paper**, the **Reason** (why it was skipped, or what needs you), the contracts and how they were sized, the **Entry price** it aimed for and the **Fill price**, whether it opened for a credit or a debit, the strikes, and when it started and filled. A line says if it came from **Trade now**, **From the Trade page**, or was **Handed over**. A futures entry that's resting has **Cancel entry** and **Modify** here (see [Futures-options bots](#futures-options-bots)). **See its trades in the Journal** opens the Journal.
- **Legs:** each option, bought or sold, its **Mid** when the strikes were picked (for a Live bot, read again just before the order), its **Delta** when the app read one to pick it, and its **Fill**.
- **Fills**, on a Paper run: every pretend fill, leg by leg (see [Paper trades](journal.md#paper-trades)).
- **Orders, step by step**, folded: click it to open (below). Older runs show a **Chase** list instead.
- **This trade's settings** and **Manage this trade**, while the trade is open (see [This trade's own timed close and watch rules](#this-trades-own-timed-close-and-watch-rules) and [Managing a trade](#managing-a-trade)). A Paper run has nothing to manage: nothing is at your broker.
- **Exits:** one row per stop, target, wing sale, timed close or action, with its group, its state (**closing** with **Cancel** while a close runs, **watched by the app**, or resting at your broker with its order number), its **Trigger or limit**, the **How** arithmetic, the fill and how many times it was put back (**Re-arms**).
- **Orders:** this run's rows of the [order log](pausing-trading.md#the-order-log): when, what, and the result.

**Orders, step by step** lists everything the entry and the exits did, oldest first, with the time in New York: when the entry was placed, each time the chase moved its price, the fill, when each stop was worked out and placed, and each closing order. Each line says where it came from ("Entry", "stop · Put side", "close (Delta exit) · Put side"), the price ("1.10 credit", "0.60 debit"), the legs as orders ("STO 1 5800P · BTO 1 5725P") and a note. A line still working says **working now**. For example:

| When (ET) | From | What happened | Price |
|---|---|---|---|
| 10-14 09:47:05 | Entry | Placed | 1.10 credit |
| 10-14 09:47:15 | Entry | Replaced | 1.05 credit |
| 10-14 09:47:25 | Entry | Filled | 1.05 credit |
| 10-14 09:47:30 | stop · Put side | Placed | |

**The watch-exit banner.** When a watch rule (like a delta exit) fires on a trade, a banner at the top of its run page says so and keeps itself up to date every 2 seconds: amber while it is closing ("Delta exit fired: position delta 32 above 30 (closing)"), grey once closed, and **red when the close didn't happen**: "close failed: close it yourself". Red means the trade may still be open: check your broker and close it there or from this page.

**Cancelling a close.** While a side is closing for its profit target or a timed close, that row shows **closing** with a **Cancel** button. Pressing it stops the close at its next step (within a few seconds): the app cancels the closing order, checks with your broker that it's gone, and puts the side's stop back for whatever is still open. The alert says how much had closed, for example "closed 0 of 1". If your broker doesn't confirm the cancel, nothing is put back (a stop on top of a live closing order could close too much) and the alert tells you to check your broker. Once the short is being bought back at market, or has been bought back, while legging out, Cancel no longer applies and the wing is still sold; the page tells you Cancel can't stop it now. If the app restarts in the middle of a close, it cancels that close's order and checks it's gone before it puts the stop back.

| Status | What it means |
|---|---|
| **Skipped** | It didn't try. The reason says why: not armed, market closed, a blackout day, over a limit, a market filter not met or not readable, premium outside the bounds, no strike that fits, its time passed, orders paused. |
| **Preview** | An old preview run from before Paper existed: strikes picked and checked, nothing sent. |
| **Running** | An order is out or may be. Normal for a few seconds; if it stays, check your broker. The app settles it by itself and alerts you if it can't. |
| **Filled** | The whole entry filled. Stops and targets are in place. |
| **Partial** | Part of the order filled and was recovered after a restart. The stops and targets cover what filled. |
| **Missed** | Sent and chased but never filled, and cancelled. No trade. |
| **Error** | Something went wrong, such as the order being refused, no option prices, or strikes that don't make sense (a short that is in the money). The reason says what. |

## Managing a trade
You can change one of a bot's trades while it's open, without touching the bot itself. In your journal, click the gear (⚙) at the end of the trade's row: the panel that opens has the same controls, and **Open full page** takes you to the trade's run page (see [The gear](journal.md#the-gear-manage-a-trade-without-leaving-the-journal)). **Open bot trades** on the Automation page links to the run pages too. On the run page, **Manage this trade** has a box for each side (for an iron condor: the call side and the put side). Each box shows what's open, the stop's trigger and the target's close-at price, and four things you can do. Every change is checked before anything is sent; if it can't be done, nothing changes and the page tells you why.

**Change stop.** Type a new stop and press **Change stop**. In **%**, it works the same way as the side's own stop: % above the short's fill price, or % of the entry credit if that's how the side is set (a side with a $ or closing-order stop uses % above the short's fill). For example, with the short filled at 2.00, **200%** puts the stop at 6.00. In **$ a contract**, it's the loss per contract you'll accept: with the short filled at 2.00, **$1.50** puts the stop at 3.50 (on a side the app watches, it closes once the side has lost 1.50 a contract). Before the old stop is touched, the app checks the short's live price:
- if the new stop is at or below what the short costs right now, it would fire at once, so it's refused and the old stop stays;
- if there's no fresh price, it's refused too (try again in a moment).

Then the old stop is cancelled, the app checks with your broker that it's gone, and the new one is placed. For that second or two the side has no stop. If the new stop is turned down, the app tries again at its next check (within 30 seconds) and alerts you. If the side's stop had failed, this places a new one.

**Preview.** Type a stop % and press **Preview** instead of Change stop to see what it would do first. The page tells you where the stop would be, where it is now, how it's worked out, and whether it would fire at once at today's prices. For example: "a 200% stop would be at 6.00 (now 4.50) … The short's ask is 2.10: it wouldn't fire at once now." Nothing is saved or sent.

**Closing order.** A side whose stop rests at TastyTrade and that took in a credit can have its own **closing order** on this trade: Off, Replaces each stop or Whichever fires first, and the dollars off the entry credit. For example, a trade that took in 5.00 with **Every group, 1.00** puts the stop where buying it back costs 4.00. **When tighter** uses that only if it's lower than the side's own stop. Pressing **Set** moves the stop now, with the same live-price check and the same steps as Change stop. **Off** puts back the side's own number. A stop in $ has no closing order.

**After the stop.** For a side whose stop rests at the broker and that has other legs (the wing), you can choose what this trade does with them when the stop fills: sell now, sell at a limit (your price, or the bid), chase, or leave open. It's the same choice as the bot's, for this trade only. **Back to the bot's** undoes it. It can't be changed once the stop has filled.

**Profit actions for this trade.** Open it under the side to give this trade its own profit actions on that side, in place of the bot's: the same rows as on the bot's page (the level, the close %, where the stop moves to). They're checked the same way, against this trade's target. An action that already ran stays as it was: you can add rows or change the ones still to come, but not remove one that ran, and once one has run you can't go back to the bot's list.

**Change target.** Type the % of the side's credit to keep and press **Change target**. For example, a side that took in 1.00 with **25%** closes once it costs 0.75 or less to buy back. A side with no target gets one.

**Close.** Choose how many contracts of that side to close (it starts at all of them), **Limit** or **Market**, and press **Close**. This works only while the market is open, with Freeze off, and never for more than your broker shows you hold. The close works like a timed close:
- the side's stop is cancelled first (and checked)
- the close starts at the middle price and steps toward a fill, using the bot's exit chase settings
- you can **Cancel** it from the run page while it runs

After its last try, the close ends the way the bot's **exit chase** is set to end: "Give up and put the stop back", "Natural price, then leg out", or "Keep lowering the price until it fills". With the second, a close that still hasn't filled can end by **buying the short back at market**, then selling the wing.

If you close only part, the stop and the target come back for exactly what's left. For example, close 1 of 3 and the stop goes back for 2. If the close doesn't fill, everything goes back as it was for whatever is still open.

**A side of several of one option.** If a side is one option bought or sold several at a time (a bot that sells 3 calls for each contract, say), Close counts single contracts: it says "Close 3 of 3 contracts" and you can close 1, 2 or all 3. Close 1 and the stop goes back on the other 2 at the same price it had. A side of two or more different options closes in whole sets: a put spread of 2 short and 2 long puts closes 1 spread (1 of each) at a time.

**Market** is for trades where every leg expires today (0DTE). On a side of a single option (a short put, say) it sends one market order once the stop is cancelled. A side of two or more options is never sent at market as one order: Market there closes it the same way Limit does, with the bot's exit chase.

**Close entire trade** is above the sides. It closes every open side, one after another, Limit or Market. Like a Market close and Close selected, it first asks you to confirm in a sentence saying what it will do. If one side doesn't fully close, it stops there, alerts you, and the sides it didn't reach keep their stop and target.

**Missing exits: Check and Restore.** Sometimes a stop or target goes missing: the broker cancelled it, it was refused, or it was re-placed 3 times that day already. **Check** lists what each side should have and doesn't, with the level it would go back at. It sends nothing. **Restore missing exits** puts them back:
- a stop goes back at its last level, after the same live-price check as Change stop, and never for more than your broker shows you hold. If it can't be placed, it's left off and listed, and nothing places it later on its own. A stop whose last send was unclear, or a watched stop that already closed its side 3 times today, is left to you;
- a target is switched on again;
- a futures trade's target is placed again at TastyTrade.

It never touches what's already there, a side you switched off, or a side the app is holding back (see below). Anything it can't put back is listed, with why, and you get an alert.

**Stop off and Target off.** Each side has two switches.
- **Stop off** cancels that side's stop. Its target, timed close and rules still work. If your broker doesn't confirm the stop is cancelled, nothing changes.
- **Target off** switches its profit target off. Its stop still works. If the target rests at your broker, it's cancelled there first, and it's off only once your broker confirms the cancel.
- **Turn all exits off** does both at once.

Switching exits off takes away the **stop and target only**. The bot's **timed close still closes the side**, and so do its watch rules and profit actions. The red box on the run page says when the timed close will act, and an alert goes out as it does. A profit action or time action that moves the stop switches the stop back on at its new level, unless that level would fire at once; one that moves the target switches the target back on. To keep a side out of the timed close, close it yourself first.

With the stop off, **the app doesn't protect that side from a loss**, and tells you so in three places:
- a red chip on the journal row: **Stop off: no stop**, or **Exits off: not protected** with both off
- a red box on the run page
- **Needs a look** on the Automation page, which also sends one alert

**Switching back on.** **Switch the stop back on** (or **Turn exits back on**, with both off) puts the stop back at its old level, or at a new % you type. The same live-price check comes first: if the stop would fire at once, or there's no fresh price, the stop stays off and the page says why. You can then set a higher stop, or close the side. **Switch the target back on** arms the target again (a target that rested at your broker is placed there again).

**Chips.** A changed trade shows what's different from its bot, on its journal row and on the run page. For example **Stop 200%**, with "bot: 150%" when you point at it, or **Stop $1.50 a contract**, **Closing order $1.00 (both)**, **After the stop: leave open**, **Profit actions: this trade's**, **Target 25%**, **Closed 1 of 3**, **Stop off: no stop**, **Target off** or **Exits off: not protected**. A split iron condor shows each half's own chips. Type the bot's own number again and its chip goes away.

**When the app holds back.** If a close or a stop change on a side may still be working at your broker, the app refuses to change that side's stop or close it. This happens when a close ended unclear, or when a stop move or "turn exits off" was cut off by a restart. The page says "A close or stop move on this side may still be working at your broker: check your broker first." Nothing is sent, because a second stop or a second close could close too much. That includes the timed close.

**How a held-back side comes back.** Every 30 seconds the app reads that earlier close's order back from your broker, and checks that no working order is left on the side's two options. While one may still be working, or can't be read, the side stays held, and you get one alert saying so.

Once your broker shows it over, the side is **released**. You get one alert: "Call side is no longer held back. The app didn't put a stop back — check your broker, then set a stop (or close) from the run page." The app sends nothing by itself here. It doesn't put the stop or the target back, and it doesn't close anything. Look at your broker, then use **Change stop**, **Close** or **Turn exits back on** as usual. Until you do, the side has no stop.

The run page shows a held-back side in a yellow box. After you've sorted things out at your broker, press **I've checked my broker: re-check now** to run the same check at once. It never unlocks the side blindly: if an order still looks live, it says so and nothing changes.

**Good to know**
- A time action that comes due later still moves the stop or target, so the most recent change wins, and the chip for that level goes away.
- A time action that comes due while a side's stop (or target) is off switches it back on at the action's level. A stop that would fire at once at that level stays off, and the run page says why. A time action that closes part of the side still does.
- Changes apply to this trade only. The bot's next trades use the bot's settings.

## This trade's own timed close and watch rules
On an open trade's run page, **This trade's settings** gives that one trade its own **timed close** and **watch rules**, in place of the bot's. The bot and its other trades keep theirs. **Back to the bot's** undoes it.

**Timed close.** Set a time from 09:30 to 15:59 (New York) and the DTE, as on the bot's page, and press **Save**. The card says which is in force and when it next runs, for example "15:45, 0 DTE or fewer (set for this trade) · next: today at 15:45". Leave the time blank to switch the timed close **off** for this trade (not for a calendar or diagonal, which must close before its near legs expire). If the time you save has already passed today, the close would start at the app's next check (within 30 seconds), so the page asks you to confirm first. A side that already had its timed close today isn't closed by time again that day.

**Watch rules.** Use delta (the whole trade's, and each leg's, in whole numbers like -40 and 40) and Use S/L ratio, with their "below" and "above" numbers. They replace the bot's delta and S/L ratio rules for this trade; the underlying move and the tested-short rules stay the bot's. Before saving, the app checks the rules on the prices now: if one would already fire (it would close the trade within seconds), or one can't be checked right now (no fresh prices), it tells you which and asks you to confirm. Nothing is sent by saving. They can't be changed once a watch rule has fired on the trade, or while a close is running.

## Open bot trades
The Automation page lists every bot trade still open under **Open bot trades**, with a link to each one's run page.
- **Check missing exits** and **Restore missing exits** do what the run page's Check and Restore do, for every open trade at once.
- **Close selected**: tick the 0DTE trades you want closed (at most 15), choose Limit or Market, and press it. Each one gets Close entire trade on its own; if one can't be closed, the others still are, and the page says what happened to each.

## Handing a trade you opened to a bot
Opened a trade yourself and want a bot to look after it? You can hand it to a bot. From then on the bot manages it exactly as if it had opened it: its stops, profit targets, timed close, time actions, watch rules and alerts, and **Manage this trade** on its run page. In the journal it gets the bot's badge.

**Which trades.** An options trade at TastyTrade or Schwab, of up to 4 legs, on the bot's own underlying, that you opened **today**, with nothing closed or rolled yet. It can be any shape a bot trades, in one expiration or several (a calendar, say).

**Which bots.** A bot on the same account, in **Live** mode (a Paper bot places nothing), whose own legs have the same shape: the same number of legs, each a buy or a sell, a call or a put, at the same ratio. The bot's strikes and DTEs don't have to match: the trade keeps its own. The trade can't be bigger than the bot's **Max per trade**, and it can be a credit or a debit, as long as it isn't too close to even. The bot doesn't need to be armed.

**Trades held past today.** A trade with any leg expiring after today goes only to a bot that holds trades overnight itself (a bot with a leg above 0 DTE), and the same rules apply as to that bot's own trades (see [Calendars, diagonals and trades held overnight](#calendars-diagonals-and-trades-held-overnight)): in each group no sold leg may expire after a bought leg; a group in two expirations needs the bot's timed close; no leg more than 1,100 days (about three years) out. Monthly SPX options (the AM-settled ones) are refused.

**How.**
1. Open the trade in the journal. Under **Automation**, press **Hand this trade to a bot…**.
2. Pick the bot and press **Check**. This reads your broker and the live prices. It places nothing.
3. The page shows which of your legs go in each of the bot's groups (the lowest strike of each kind first, as for the Trade page) and what the bot would place on each group. For example, with the call sold at 2.50 and a 95% stop, the stop is at 4.80, next to what the call costs right now (say 2.10). A group whose stop the app watches (a calendar, a debit spread) shows its stop as a profit-and-loss level, say P&L −1.00, next to the group's P&L right now. It also shows each group's profit target, what **rests at the broker** for that group (its stop or its profit target), and whether the app holds the stop instead (on the short's price, or on the group's P&L): a stop the app holds works only while the app is running and the computer is awake. Anything the trade's side changes (a stop or target this fill can't take) is shown in a yellow box under the group.
4. If it all looks right, press **Hand to (bot name)**. Everything is checked again, then the bot places its stops.

**When it says no.** Nothing is done, and the page says why:
- **A working order on the trade**, such as a stop or a profit-taking order you placed yourself. Cancel it at your broker (or on the Orders page), then check again. The app never cancels your orders for you.
- **A stop that would fire at once.** If the market has moved so far that the bot's stop is at or below what the short costs now, it would buy the short back the moment it lands. For example, a stop at 4.80 when the call costs 5.00. For a stop the app watches, the same goes when the group's P&L is already at or below its level (a stop at P&L −1.00 when the group is at −1.50). Manage that trade yourself instead.
- **No fresh price** for a short (or for any leg of a group the app watches): there's no telling, so try again in a moment.
- **The bot doesn't match**: "1 short put + 1 long put doesn't match a 4-leg bot", "Priced too close to even (0.03); not entered.", "Inverted: the Put side prices as a debit.", or one of the held-overnight rules above.
- **Freeze** or **No new trades** is on, or the market is closed.
- The broker can't be read, or doesn't show the legs.
- Something changed between Check and Hand (a price, the bot, the trade): check again.
- **An earlier order on these options has an unclear outcome.** Check your broker, then clear it on the order log.
- **A bot's entry on this account is still being settled** (or needs you on the Automation page). Try again once it's settled.
- **It's already a bot's trade.** A trade a bot opened, or one already handed over, can't be handed over again.
- **The bot is at its Max open trades.** For example, a bot set to hold at most 2 trades that already holds 2 can't take a third. Raise the limit, or wait for one to close.

**If the price jumps while the stops go on.** Each stop is checked against the live price once more, just before it's sent. If the trade has already gone through a side's stop by then, that side's stop isn't sent: you get an alert, the run is marked **Needs you**, and the page tells you in a yellow box. Manage that side yourself from the run page or at your broker.

**Good to know**
- The page warns you about things that will happen straight away: a profit target that's already reached (that side closes for its target within the next checks), or the bot's timed close time that has already passed (it starts closing at once).
- A bot's **SPX move** watch rule can't run on a handed-over trade: the app didn't record SPX's price when you opened it. The page says so.
- It counts as one of the bot's open trades (for **Max open trades**), but not as one of its entries for the day.
- If the bot splits its iron condors in the journal, your trade shows split too after the hand-over.

## Sending a trade from the Trade page as a bot's entry
You can set a trade up on the [Trade page](trade.md) and have a bot open it and look after it from the first fill. After **Review order** is accepted, a box **Or manage it with a bot** appears beside **Send**.

1. The box lists the **live** bots on the ticket's account whose legs match the ticket (the same rules as handing a trade over, above), each with a line per group naming your legs it takes and its exits, for example "Put side: sell 5690P Oct 13 / buy 5615P Oct 13 · stop 95% · target 50%". Bots that can't take it are listed under a fold with why.
2. Pick the bot and press **Send as (bot name)'s entry**. The app asks you to confirm.
3. The bot sends the ticket as **its own entry**: one order through the app's usual checks, within the bot's limits, **chased by the bot's entry chase settings** starting from the middle price of the legs right now. Your typed limit and time in force aren't used. When it fills, the bot places its stops and targets exactly as for one of its scheduled entries. The answer links to the run.

**What it checks first** (nothing is sent if any of these says no): the bot is live and on the ticket's account; the legs match; **Freeze** and **No new trades** are off; the market is open and it isn't the last 2 minutes; the trade fits the bot's **Max per trade**, **Max per day** and **Max open trades**; and the bot's **timed close** isn't already due for this trade (it would be closed as soon as it's in).

**Then, after the order is claimed:** the app reads your positions (a ticket that would close or roll something you hold is refused: a bot only opens new trades) and the live prices of every leg (a leg with no fresh price stops it). Each of these ends the run with nothing sent, and you get an alert.

**One review sends one order.** Sending it as a bot's entry uses up the review, so **Send** goes away too (and the other way round). **Check which legs go together.** Each bot's line shows how it pairs your legs into its groups, with each group's stop, for example "Put side: sell 5690P Oct 13 / buy 5615P Oct 13 · stop 95%". Each of the bot's legs takes your leg of the same kind with the lowest strike, so a bot with two put spreads given 5000/4950 and 4980/4975 pairs 4980 with 4950 and 5000 with 4975. If that isn't what you meant, don't send it. **Pressing twice sends once.** Each review gets its own one-time code; a second press with the same code says "This order was already sent (run 12)." and nothing more goes out. Any change to the ticket takes the box away until you review again.

**Good to know**
- Arming, the bot's schedule, blackout days and market filters don't apply: you pressed the button.
- It counts as one of the bot's open trades, but not as one of its entries for the day.
- A Paper bot isn't offered. If one is switched to Paper just as you press, the order is only checked.
- The run page says **From the Trade page**.

## Futures-options bots
A bot can trade **futures options** on TastyTrade: type a futures root like **/ES** or **/MES** as its underlying (not a month like /ESZ6). A futures bot works differently from the others:

- **Futures: profit target only, no stop.** Each group gets a profit target, and nothing closes the trade on a loss. Leave the stops blank; the bot page refuses a stop, and a group without a target.
- **It enters only from the Trade page**, through **Or manage it with a bot**. It's never armed and has no entry times or **Preview order**.
- **TastyTrade only.**
- The legs on its page are only the **shape** it takes (for example "sell a put", or "sell a put and sell a call" for a strangle). The ticket brings the strikes and the expiration.
- It can't have a timed close, time actions, watch rules, profit actions, the closing order, an entry window, market filters or default alerts. Those are built for stock and index options and the regular session.

**How a futures trade goes**
1. On the Trade page, build the ticket on /ES (say, sell 1 put at the 7000 strike), set your **limit** (say 12.50 credit) and the time in force (**GTC** keeps it working until it fills), and press **Review order**.
2. Under **Or manage it with a bot**, pick your /ES bot and press **Rest the limit as (bot name)'s entry**.
3. The bot sends **your limit, at your price**, as its entry. It rests at TastyTrade at any hour the market is open, until it fills or you cancel it. It's never moved or chased. The run page shows it with a **Cancel entry** button.
4. When it fills, the bot rests a **profit target** on each group at TastyTrade, also GTC. With a 50% target on a 12.50 credit, that's a buy order at 6.25. The order works overnight and while the app is closed.
5. The target fills, or the option expires.

**The money:** futures prices are in points, and each futures option has its own dollars per point. /ES is $50 a point, /MES $5. The run page shows the contract (like /ESZ6) and "×50 a point", and the dollars beside each price: 12.50 is $625 a contract on /ES.

**Good to know**
- **If part of your entry fills**, the bot waits 60 seconds during regular market hours for the rest, then cancels it. The filled part gets its profit target, and the rest is sent again at the same price as a new run. Outside regular hours it does this at once.
- **Changing the target from the run page:** type a new % and press **Change target**. The app cancels the resting order at TastyTrade, checks the cancel went through, then places the new one, so there are never two targets at once; for a second or two there's none. For example, a 30% target on a 12.50 credit is placed as a buy at 8.75. A target that has partly filled isn't changed: do that at TastyTrade. If the cancel isn't confirmed, or the new order's answer is unclear, the app stops touching that target, marks the run **Needs you** and tells you to check your broker. If the new one can't be placed, you're told at once: the trade has no exit until you press **Restore missing exits**.
- **Target off** cancels the resting target (the page asks first, because with no stop the trade then has no exit at all). It's switched off only once TastyTrade confirms the cancel. **Switch the target back on** places it again at its last price.
- **If you cancel or change the profit target at TastyTrade**, the app tells you at once with an urgent alert: the trade has no exit. It doesn't put the target back by itself: press **Restore missing exits** on the run page to place it again at its last price. Adding a stop or closing part of a futures trade from the run page isn't built yet: do it at TastyTrade.
- **Modify a resting entry:** while the entry rests, type a new limit beside **Cancel entry** and press **Modify**. The order is replaced at TastyTrade at the new price. If it filled (or partly filled) first, the app records that, protects what filled, and doesn't modify anything (a partly filled entry's rest goes back at the new price).
- **If you change the entry at TastyTrade**, TastyTrade names the new order, and the bot follows it: its fill gets the profit target as usual. If the new order isn't the same legs and size, the app stops watching the run, marks it **Needs you** and tells you. **If the entry is cancelled at TastyTrade** with no new order, the run ends and you're told.
- **A futures trade you opened yourself** can be handed to a futures bot from the journal the same day, at any hour, as for other trades. It gets its profit target and no stop.
- Each morning's 09:20 check confirms every futures trade's target is still resting.
- **Expiration:** a futures option in the money at expiration turns into a futures position, and nothing closes it. From 10 minutes before its series expires (each series has its own time: an /ES weekly at 16:00 New York, /GC at 13:30) the app sends one urgent alert if the trade is still open.
- It counts as one of the bot's open trades while its entry rests, and its contracts count toward Max per trade and Max per day like any bot's.
- **Size limits count contracts, not dollars:** one /ES option counts the same as one SPY option, though an /ES option moves about ten times as many dollars. Set a futures bot's limits with that in mind.
- A target in points (rather than %) is in the futures option's own points: 2.00 on /ES is $100 a contract.

## Calendars, diagonals and trades held overnight
Each leg has its own **DTE** (days to expiration), from 0 (today) to 60. Give the legs of one group different DTEs and you have a **calendar** (the same strike in two expirations) or a **diagonal** (different strikes too). **Start from** has a calendar, a diagonal and their doubles (a put one and a call one, as two groups), each selling the 7 DTE option and buying the 14 DTE one; change the DTEs to suit you.

**Which expiration a leg trades.** With **Exact DTE only** on (the default), a leg set to 7 DTE trades only the expiration exactly 7 days away; if there isn't one that day (a holiday, say), the entry is skipped and the run says why: "No expiration at exactly 7 DTE (the nearest is …, 8 DTE)." With it off, the leg takes the first listed expiration **at or after** 7 days, never an earlier one. If two of your DTEs land on the same expiration that way, the later one moves to the next expiration, so a calendar never buys and sells the same option.

**The rules a calendar must follow.**
- **It must have a timed close.** A calendar or diagonal must close before its near legs expire, so the page won't save one without a **timed close** time. The timed close counts days to each group's nearest leg: with **0** days it closes the group on the near legs' last day (say at 15:45), and with **2** days two days before.
- In a group, **a sold leg can't expire after a bought one.** The nearer leg is the one you sell. A reverse calendar (buy the near one, sell the far one) is refused for now: once the near leg expired, the sold far leg would be left with no protection.
- A calendar's far leg can sit on the near leg's strike: an **offset of 0** from it is allowed when the two legs have different DTEs.
- A leg at **0 DTE** always trades today's expiration, even with Exact DTE only off, and no leg is ever placed more than 1,100 days (about three years) out.
- **A debit diagonal can lose more than you paid for it.** If SPX runs through both strikes, the near leg you sold can cost more to buy back than the far leg is worth.

**Overnight.** A trade with any leg above 0 DTE stays open after the close:
- A stop that **rests at TastyTrade** (a credit group with one sold leg, like a credit diagonal) stays at TastyTrade overnight. If TastyTrade drops it, the app puts it back the next time it checks, at any hour, as it does during the day.
- **Profit targets**, **stops the app watches** (a group that rests its target, or any group that fills for a debit) and **watch rules** only work while the market is open. If the app was off for a whole trading day, it tells you when it starts again. They start again at the next open. **The app must be running at 09:30** for those stops to work: overnight, nothing at your broker protects them.
- The **timed close** and **time actions** count days to expiration for each group on its own, from its nearest leg. For example, a calendar whose near leg expires on Friday with a timed close at **15:45** and **0 DTE** is closed at 15:45 on Friday, both legs together.

**If the near legs expire anyway.** This should never happen, because the timed close closes the group first. But if that close doesn't fill (the market ran away, Freeze was on, the app wasn't running), the near legs expire at the close and the group's stop and profit target end with them. **The far legs are then yours: the bot never sells them.** You get an **urgent** alert on every channel, for example: "Put calendar's near legs expired, far legs held. The timed close didn't close it before its near legs expired. SPXW 261020P5690 expired at the close. SPXW 261027P5690 is still held, with no stop or target now. The bot leaves it to you: sell it or keep it at your broker." The run page shows it as **far legs (near legs expired)**, and **Needs a look** on the Automation page and the check before the open keep saying so. Until the far legs expire, the trade still counts toward the bot's **most open trades**.

**The check before the open.** At 09:20 on each trading day, and whenever the app starts, it compares every trade held overnight with your broker: is each leg still held, and is each stop in place? It sends one alert: "Held trades checked: 2 trades held overnight, every leg held and every stop in place", or, if something doesn't match, what it is (for example, the far leg sold at your broker, a stop not yet at your broker, or far legs left after the near legs expired). The check never changes anything; the alert tells you what to look at.

**Good to know**
- If a leg left after a stop is sold with a resting limit order, and that option doesn't expire today, the order is good until cancelled, so it keeps working overnight instead of lapsing at the close.
- Monthly SPX options (the ones that settle in the morning) are never picked: a bot trades the weekly SPXW options only.

## The market calendar
**Settings → Market calendar** shows:
- each list (FOMC days, CPI days, last trading day of the month), its next few days, and whether the app can tell about today;
- where the dates come from and when each was last read: **ForexFactory** (this week's economic events), **FOMC** (the Federal Reserve's meeting calendar), **FRED CPI** (only with a key, below) and the **NYSE calendar** (market holidays and early closes);
- the years the holidays cover;
- a **Refresh now** button, which reads them all again in the background (reload the page after a minute).

The app reads them by itself while it runs: ForexFactory once each new week (from Sunday evening), the others once a week, and again an hour later if a read failed. If a read fails, or a page looks wrong (a meeting that was there before has vanished, say), the app **keeps the dates it had**, shows the problem there and alerts you once that day.

**CPI days and the FRED key.** Without a key, CPI days come from ForexFactory's calendar for the current week, which is all a bot needs on the day. For CPI days further ahead (shown in the list), get a free key at fred.stlouisfed.org (My Account, then API Keys) and paste it in the same section. Leave it blank to go without.

Only public calendars are read; nothing about you or your accounts is sent.

## The holiday table
The app carries a built-in table of market holidays and early closes for 2026 and 2027. Once it has read NYSE's own published calendar (usually three years ahead), it uses that instead, but only if NYSE's page agrees exactly with the built-in table for 2026 and 2027, lists at least 9 holidays and an early close for each later year, and doesn't add a holiday in the coming week that the app didn't already know. Otherwise it keeps the table and alerts you. When neither covers the current year, the app alerts you once a day: "The market holiday table doesn't cover 2028. Bots may try to trade on a holiday; update the app." Bots keep running meanwhile, and may try to enter on a holiday, so until it's covered (press **Refresh now** under Settings → Market calendar, or update the app), **disarm your bots, or arm them only on days you know the market is open.** On a day with no options listed for that day, the "No expiration today" check skips the entry anyway.

A run reading **Needs you** is one the app couldn't settle (older than 5 days, its bot was deleted, its entry order wouldn't cancel) or a filled trade whose exits may be missing. Look at your broker.

## A bot's record
**Record** on a bot's page (or its P/L on the Automation list) shows how the bot has done in its account:
- **Closed P/L**, **win rate** (winners out of closed trades) and the number of trades;
- per lot (one contract of each leg, so a 3-contract trade counts as 3 lots): the average trade, average winner, average loser, best and worst;
- the open trades, then the closed ones, newest first, 50 a page, with **All / Winners / Losers**.

Each closed trade also shows its **Days** (New York days from open to close; a same-day trade is 0) and its **P/L %** (the P/L as a share of the premium taken in or paid). Open trades show their days so far. For example a credit of $200 closed for a $50 profit is 25%.

It counts the trades the bot placed or that you matched to it, plus every other trade with the bot's tag in that account that no bot is linked to, **including trades you opened by hand**, so trades you made that way before the bot keep their history. The **Source** column says which: "this bot" (with a link to its run), "another bot" or "by hand". Taking the tag off a hand-made trade takes it out of the record. Two bots with the same tag and account share the hand-made trades, but not each other's own. A hand-made trade with two bots' tags counts in both.

For example: 5 closed trades, 3 made money, together +$250. The record shows win rate 60% (3 of 5) and closed P/L +$250. The numbers come from your journal, so they update after a sync.

## Tags
Tags are labels you give your bots. The Automation list shows them under each bot's name, and the row above the list filters by one: click **Mornings** to see only bots tagged Mornings, or **All** to see every bot again. **Armed**, **Off**, **Live** and **Paper** are always there and filter by the bot's state; you can't use those words as your own tags.

**Search** above the list finds bots by name, account number or underlying, and every word must match: "beta spx" finds bots with both. A day word (mon, tue, wed, thu, fri, or the full name) finds the bots that enter that day instead.

**On a bot's page**, Basics has the tag picker: chips for the bot's tags, **Pick tags** for the ones your other bots use (archived ones too) and your journal's tags (picking one here labels the bot only, not its trades; the **journal tag** above is the one its trades get), and a **New tag** box. A tag you type that a bot already has, in any capitals, keeps that bot's spelling, so `mornings` becomes **Mornings**.

**Tagging several bots at once:** tick the bots and a bar appears at the bottom of the window. Type a tag (or a few, separated by commas) in its box and press **Add tag**, or **Remove tag** to take them off. **Clear** unticks them all. Only the tags change, so armed and live bots can be tagged too. A bot can have up to 20 tags; one that would go past that is left as it was and named.

**In the journal**, a bot trade's chips (like "Target 25%") update by themselves when its stop or target changes, whether you changed it or a rule did; a chip that just changed is ringed for a few seconds.

## Clone
**Clone** on a bot's page, or the copy icon at the end of its row in the bot list, makes a copy with everything the same (legs, sides, times, filters, account, size and tags) named "… (copy)". The copy is always **off**, and keeps the original's mode (Live or Paper), even when the original is armed, and has no runs. Change what you want, then arm it.

## Archive
**Archive** puts away a bot you've finished with. It leaves the list (**Show archived** brings the archived ones up), it's switched off, and it **never trades** until you **Unarchive** it, which brings it back switched off. Its runs and journal trades keep their link.

You can only archive a bot when nothing of it is open. Otherwise the page tells you what's in the way, for example "1 open trade (run 14)", "1 exit still working (run 14)" or "1 entry in progress (run 16)". Wait for the trade to close or expire, then try again.

## Sharing a bot
**Download bot file** on a bot's page saves the bot's settings as a small file (`spx-iron-condor.json`) you can send to a friend. It holds what the bot trades and when: legs, sides, stops and targets, entry times, the schedule, filters and chase settings. It **never** holds your account, your broker login, your contract sizes or limits, your journal tag or tags, or anything about your trades. Blackout days already past are left out. An archived bot can't be shared.

To share it with a link instead, put the file somewhere online, like Google Drive, Dropbox or GitHub. Set it so **anyone with the link can view it**, then send the link. The **Download bot file** address itself only works on your own computer, so your friend can't use it.

**Import a bot** on the Automation page brings one in. Paste a link to a bot file, choose the file, or paste what's in it, then press **Preview**: you see what the bot does, and nothing is saved yet. Then choose:
- a name (if you already have a bot with that name, it suggests "… (imported)"),
- the journal tag its trades get (**+ New tag** makes one here too),
- your TastyTrade account,
- contracts per entry, most per trade and most per day (it suggests 1 contract, and a daily limit of one contract for each entry time that's on).

**Import** saves it **off**, and **Live** like any new bot: it can't trade until you arm it, and arming a Live bot always shows you what it will do first. Switch it to Paper first if you want to watch it with pretend money. Its page says "Imported from a bot file exported …", with the link if it came from one. The app checks every setting the same way as when you save a bot yourself, so a file with something it won't accept is turned away with the reason, and nothing is saved.

After an import, the bot's page offers **Find this bot's past trades**: if trades already in your journal fit this bot (ones you opened by hand before you had it, say), this adds them to its record. See [Finding a bot's past trades](#finding-a-bots-past-trades).

**If a link won't import**, the page says why, for example:
- "That link gave a web page, not a bot file": the file isn't shared publicly. On Google Drive choose **Share → Anyone with the link**; on Dropbox, **Copy link**.
- "That link answered 404": the file was deleted or the link is mistyped.
- "That link points at this computer or your home network": links must lead to a file on the internet.
- "That's this app's own Download bot file link": press **Download bot file** and choose the file instead.

A file made by a newer version of the app is turned away with "This bot was saved by a newer OptionsJourney (file version 4). Update the app to import it." A file that isn't a bot file says so. Bot files carry any shape and, for each group, which exit rests at the broker; files saved by earlier versions still import (a bot that opened for a debit gets Profit target on every group, every other bot Stop). An older copy of the app turns a new file away the same way.

## When the app checks itself
**Needs a look.** While your bots have trades open, the app checks every 30 seconds for a side left without protection: a stop that filled with nothing done about its wing, a stop shown as working that has no order number, an open side with no stop at all, or a side whose exits you turned off. Each one shows in a **Needs a look** box at the top of the Automation page, with a link to its run, and sends you one alert. In your journal, the trade's **bot** badge turns red and pulses, with what was found when you hover over it; click it to open the run. It disappears once it's sorted. The app never changes an order because of these: check your broker.

**When the bots' clock stops.** The app keeps checking that the parts that run your bots are working: the clock that fires entries, the watch on profit targets and the exit monitor. If one stops, a **Bots may not run** warning shows at the top of the Automation page (for example "The bot clock has stopped since 10:31: no bot will fire.") and, on a market day from 09:20 New York time, you get one alert a day. Check the app's log and restart the app.

## Deleting a bot
Deleting a bot disarms it and hides it. Its past runs and journal trades keep their link. To put a bot away and keep it to hand, archive it instead.

## Good to know
- Start with **Preview order** and a few days in **Paper** before going Live. Then switch to Live with 1 contract, and raise it once you've seen the stops and targets work.
- The app has to be running, and your computer awake, for entries to fire. For a stop the app watches (a group that rests its target, a Schwab trade opened before Schwab stops could rest, a group that fills for a debit, and groups with no sold leg or two or more) it also has to be running for the stop to work.

### What changed for iron condor bots in this version
Your iron condor bots trade exactly as before, and their stops still rest at the broker. A few things you may notice:
- **Contract limits count the largest leg.** For an iron condor every leg is 1, so the numbers are the same.
- **"Group" instead of "side"** on the bot's page and in some alerts, and "the group's other legs" for the wing.
- **A crash at the wrong moment is now reported, not hidden.** If the app stops after it has bought a side's short back but before it has handed the wing on to be sold, it used to mark the close done with the wing quietly still held. Now that close ends **left to you**, and the alert names the wing so you can sell it.
- **A leg-out that stops part-way leaves the side to you.** If a close is legging out and has to stop after some of it has already filled, the app no longer puts the old stop back (a stop on a half-closed side can't close it properly). It tells you which options are still held and that nothing protects them. If nothing had filled yet, the stop goes back as before.
- **A stop is only put back while you hold its short sold.** If you've closed the short yourself, or hold it bought, the app doesn't send a stop.
- **Trades held overnight** (this version): bots can trade legs in other expirations. Your same-day bots are unchanged: every leg is 0 DTE and Exact DTE only is on.

## Finding a bot's past trades
A bot's record is the trades it placed or that you matched to it, on its account, plus any trade with its tag on that account that no bot is linked to (one you opened by hand). Trades that fit it from before you set it up aren't linked to it, so its record starts empty. **Match trades** on the bot's page finds them for you, and **Match all bots** does every bot at once.

- **What it lists:** trades on the bot's account and underlying with the same legs as the bot (sold or bought, calls or puts, in the same ratio; the strikes and dates don't matter). If the bot has entry times, the trade must have opened within 2 minutes of one; if it trades on chosen days, on one of those days. Each trade shows why it matched, for example "same legs · opened 10:30 · Monday", newest first. Open and closed trades of any age are listed.
- **Ticked or not:** a trade that matches the bot's entry times is ticked for you. If the bot has no entry times, the legs are all there is to go on, so those trades are listed unticked: check them yourself.
- **What it leaves out:** trades a bot placed (they already belong to it), stock trades, and trades already matched to this bot. A trade you gave this bot's tag earlier but never matched is listed, so you can link it.
- **Another bot's:** trades matched to another bot (it says "matched to" and the bot's name), or carrying another bot's tag and not this one's, are in a closed list. You can still tick them, which moves them to this bot (a trade has one bot). Their other tags stay.
- **Also matches:** if a trade looks like another bot's too, it says so.
- **Match selected** links the ticked trades to the bot and gives them its tag, just as adding the tag in the Journal does. The Journal then shows the bot's name on each one. A trade you haven't renamed also takes the bot's name (undo gives it its own name back; a name you gave it stays). Nothing changes until you press it.
- **Split iron condors:** if the bot splits its condors and a ticked trade is a whole condor, it says "will be split into put and call spreads". Match selected splits it, just like **Split into put and call spreads** in the Journal, and links and tags both halves. Undo combines it again.
- **Undo** puts the last batch back as it was: their old tags and bot. It lasts until the app restarts. After that, take a trade off its bot with **Unlink from** (the bot's name) in the trade's gear panel or on its page; taking the tag off in the Journal isn't enough, the trade stays linked.

### Match all bots
**Match all bots**, at the top of the Automation page, looks at every trade in your journal that no bot is linked to and lists it under the live bot it looks most like (Paper, archived and manual bots are left out). It's the quickest way to sort months of past trades when several bots share one tag.

- Each bot has its own section with a count. Each row shows the trade, when it opened, the account and why it matched.
- **Ticked for you:** a trade that matches one bot's entry times and no other bot as well.
- **Also matches:** when two or more bots match a trade equally well, it's listed under one of them, names the others ("also matches ...") and is left unticked. Tick it only if it's that bot's; otherwise match it from the right bot's own **Match trades** page.
- **Trades you tagged earlier** with a bot's tag without matching (the way Match trades worked before) are listed here too, so you can link them without undoing anything.
- **Match selected** links every ticked trade to its bot; **Undo** puts the whole batch back.
