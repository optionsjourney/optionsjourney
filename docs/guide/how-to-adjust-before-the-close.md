# How to adjust a bot's trade before the close

*Closing part of an open trade, moving its stop or target, taking its exit off, or rolling a leg, while a bot (or a resting stop or target) is looking after it. And what the app does when you do.*

**Make these changes in the app, not at your broker.** The app knows about the orders it sent. It doesn't follow changes you make at your broker, and it may undo them: a stop you cancel there is put back (a stop that rests at your broker, which only a group of one sold leg has). The iron condor in [How to set up your first bot](how-to-first-bot.md) has no such stop: the app watches its stops, and its profit targets rest at your broker.

**Where:** in **Journal**, click the gear (⚙) at the end of the trade's row, or open the trade from **Open bot trades** on the Automation page. Under **Manage this trade** there's a box for each group (an iron condor has a **Call side** and a **Put side**) showing what's **Open**, its **Stop** and its **Target**. Changes there are for this trade only; the bot keeps its own settings.

## Close part of it
1. In the group's box, set **Close** to how many (it starts at all of them), for example `1` of `3`.
2. Pick **Limit** (starts at the middle price and steps toward a fill) or **Market (0DTE)**.
3. Press **Close**.

**What the app does:** it cancels the group's stop first and checks with your broker that it's gone, then closes. While the close runs you can **Cancel** it. When it's done, the stop and target come back for exactly what's left: close 1 of 3 and the stop goes back for 2. If it still hasn't filled after the last try, the bot does what its **Chase** section says under **If a close hasn't filled after the last try**: **First, at market (one-leg groups)** if that's ticked, then the **Then** choice. With **Give up and put the stop back**, everything goes back as it was; **Natural price, then leg out** and **Keep lowering the price until it fills** keep trying to close it (see [If a close hasn't filled after the last try](automation.md#if-a-close-hasnt-filled-after-the-last-try)). The journal row shows a chip like **Closed 1 of 3**. It only works in the market's regular hours, with Freeze off. **Close entire trade** closes every group, one after another.

**At your broker instead:** the bot's stop or target is already a closing order on those contracts, and your broker takes only one closing order for the same contracts, so it may refuse your close. And the app doesn't notice part closed at your broker: the box still shows the old **Open** number, and a stop resting there keeps its old size. Any close the app starts later is sized to what your broker shows you still hold, never more.

## Move the stop or the target
1. **Stop:** under **Stop loss**, type the new stop, pick **%** or **$ a contract**, and press **Preview** to see where it would sit and whether it would fire now. Then press **Change stop**.
2. **Target:** under **Profit target**, type the % of the credit to keep and press **Change target**.
3. **Closing order** (only on some groups): pick how it applies, type the dollars off the entry credit and press **Set**. It moves the stop the same way as Change stop.

**What the app does:** before touching anything it checks the sold option's live price. A stop that would fire at once, or one it can't check because there's no fresh price, is refused and the old stop stays. Otherwise the old stop is cancelled at your broker, the cancel is checked, and the new one is placed: for a second or two the group has no stop. A stop the app watches just changes the level it watches; nothing is sent. A target resting at your broker is cancelled there and placed again at the new price. The journal row shows the change as a chip, for example **Stop 200%**.

**Does the bot change it back?** Not by itself. But the bot's own **time actions** and **profit actions** still run on this trade: one that comes due later moves the stop or target again, and the latest change wins. You can give the trade its own profit actions under **Profit actions for this trade**.

**At your broker instead:** don't. The app doesn't learn your new level. It may see its order as cancelled and put its own stop back at the old level, as below. A profit target resting at your broker that you change there may read as cancelled (it does at Schwab). If it does, you get a "profit target gone" alert, it isn't put back, and the app doesn't know about your new order.

## Take the exit off
In the group's box:
- **Stop off** cancels the stop at your broker (only once your broker confirms it's cancelled), or stops watching it. Its target, timed close and rules still work.
- **Target off** switches the profit target off. If the target rests at your broker, it's cancelled there first. The stop still works.
- **Turn all exits off** does both.

**What the app does:** it doesn't put a stop back that you switched off. Because nothing then protects the group from a loss, it tells you in three places: a red chip on the journal row (**Stop off: no stop**, or **Exits off: not protected**), a red box on the trade's run page, and **Needs a look** at the top of the Automation page, with one alert. To undo it, press **Switch the stop back on** (you can type a new stop %), **Switch the target back on**, or **Turn exits back on**.

The bot's **timed close still closes the group** with its exits off, and its watch rules and profit actions still run. To keep the trade out of the timed close, open its run page and, under **This trade's settings**, clear **Close at** and press **Save**.

**At your broker instead:**
- **A stop that rests at your broker and that you cancel there is put back.** The app sees its stop gone and places it again, usually within 30 seconds, sized to what you still hold sold. It does this up to 3 times a day per group. After that it sends an alert ("re-placed 3 times today; it's left to you"), and after two minutes **Needs a look** shows "*(group)* has no working stop". If you've already bought the sold option back, there's nothing to protect: no stop is sent and the app counts that group as closed.
- **A target you cancel there isn't put back.** You get an alert ("profit target gone") that says what still protects the group. **Restore missing exits** on the run page places it again. If nothing protects the group any more, the run is marked **Needs you**.

## Roll a leg
**Rolling isn't in the app yet.** There's no roll button on a bot's trade, and the Trade page refuses a ticket that closes one position and opens another: "Rolls aren't supported yet: close and open separately." The gear panel's **Risk graph (roll)** helps you plan one.

To do it in the app, in two steps:
1. Close the group from its box (above). The bot takes its own stop and target off first.
2. Open the new position on the Trade page ([How to send a trade](how-to-send-a-trade.md)). To have it looked after, [hand it to a bot](how-to-hand-a-trade-to-a-bot.md) the same day, or give it rules with **Manage this trade…** on its page.

**At your broker instead:** the bot doesn't learn about the roll. It keeps looking after the legs it opened, as described above, and the new option gets no stop or target from it.

## A trade with rules from Manage this trade
A trade you gave a manual bot's rules (or your own) has **Change its rules** and **Remove its rules** on its Manage this trade page. Its stop and target are placed the same way as a bot's, so the same goes for changes at your broker.

More detail: [Managing a trade](automation.md#managing-a-trade), [When the app checks itself](automation.md#when-the-app-checks-itself), [This trade's own timed close and watch rules](automation.md#this-trades-own-timed-close-and-watch-rules).
