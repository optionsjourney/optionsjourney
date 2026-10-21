# How to go from Paper to Live

*Switching a bot you've watched in Paper to real orders.*

Before you start, look at its paper trades in **Journal → Paper trades**. Did it enter when you expected, and did its stops, targets and timed close do what you meant? Paper fills are at the middle price and every stop is watched by the app, so real results will differ (see [Reading paper results honestly](automation.md#reading-paper-results-honestly)).

1. Open the bot from **Automation**. Check **Contracts per entry** in **Basics**: start Live with `1`. If you change it, press **Save**.
2. Go to **This bot** at the bottom and press **Switch to Live**.
3. A page asks **Switch (bot name) to Live?** It lists the account, the most it can send per trade and per day, and where its stops are held. If the app watches any of its stops, tick the box that starts "I understand: the stops of …". The first time any bot goes Live, there's also a box saying you're responsible for its orders (see the [disclaimer](disclaimer.md)).
4. Press **Switch to Live**. Any paper trade still open is closed at the middle price first ("switched to Live"), so pretend and real trades never mix. Its paper history stays in Paper trades.
5. You're back on the bot's page. **Status** says **Live**, and the bot is **switched off**, even if it was armed in Paper.
6. Under **Schedule**, press **Arm it (sends real orders)**.
7. A page asks **Arm (bot name)?** with the same list. Tick the box ("I understand this bot sends real orders", or the one about stops the app watches) and press **Arm it**.

## What you'll see
- The bot's row on the Automation page shows its account chip with a green dot: armed and Live.
- At its next entry time it sends a real order, chases the price, and once it fills puts its stop and target in place. The run's page shows each step.
- A stop the app watches works only while the app is running and the computer is on and awake. The menu bar app's **Keep awake** helps.

**To stop it at any time:** press **Turn off** in **This bot**, or use **No new trades** or **Freeze all orders** in the top bar's trading switches menu (see [Pausing trading](pausing-trading.md)). Turning a bot off or No new trades only stops new entries: a trade already open keeps its stops and targets. Freeze stops the app sending anything at all.

**Going back:** **Switch to Paper** in **This bot** works straight away. A Live trade that's already open stays real and is still looked after.

More detail: [Switching to Live](automation.md#switching-to-live), [Arming](automation.md#arming).
