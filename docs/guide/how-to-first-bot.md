# How to set up your first bot

*An SPX iron condor that opens once a day, set up in Paper first so it only pretends.*

The numbers below are made up and round. They show where things go, not what to trade.

**The example:** sell a call and a put at about 10 delta, buy wings 25 points further out, 1 contract, at 10:00 New York time. Stop each side if its sold option doubles, take profit at half the credit, and close what's left at 15:45.

## Build it
1. Open **Automation** in the top bar and press **New bot**. A new bot already starts as an iron condor, with its numbers blank.
2. **Basics:**
   - **Name:** for example `SPX IC example`.
   - **Journal tag:** pick one, or press **+ New tag**, type a name and press **Create**.
   - **Account:** the account it trades in.
   - **Underlying:** `SPX` (already filled in).
   - **Contracts per entry:** `1`. Leave the four limits below it blank: **Max contracts per trade** and **Max contracts per day** then show "Auto", and **Max entries per day** and **Max open trades** show "No limit".
3. **Schedule:** in the first **Entry times** box type `10:00` and leave **On** ticked. **Frequency** stays **Every trading day**.
4. **Legs:** four rows are already there.
   - The **Sell Call** row: **Pick by** **Delta**, **Target** `10`.
   - The **Buy Call** row: **Pick by** stays **Offset**, **From** Leg 1, **Target** `25` (25 points above the sold call).
   - The **Sell Put** row: **Pick by** **Delta**, **Target** `10`.
   - The **Buy Put** row: **Pick by** stays **Offset**, **From** Leg 3, **Target** `-25` (minus: 25 points below the sold put).
5. **Groups:** **Use leg groups** is ticked, with a **Call side** and a **Put side**. Under **Stop and target: Call side**, and again under **Put side**:
   - **Profit target:** `50` and **%**.
   - **Stop:** `100`, **% / basis**, **leg premium**. A call sold at `1.00` is stopped once it costs `2.00`.
   - **At the broker** says **Stop watched by the app**: each side's profit target rests at your broker and the app watches the stop. See [Which exit rests at the broker](automation.md#which-exit-rests-at-the-broker).
6. **Exits:** under **Timed close**, **Close at** `15:45` and **Days to expiration, at most** `0`.
7. Leave **Filters**, **Watch exits**, **Alerts** and **Chase** as they are for now.
8. Press **Save** at the bottom. The page reloads with a summary at the top and a **This bot** section at the bottom. If something needs fixing, the page says what beside the field and nothing is saved.

## Make it Paper and switch it on
9. In **This bot**, **Status** says **Live**: a new bot starts Live and switched off. Press **Switch to Paper**. It now says **Paper** and "Checks orders but doesn't send them."
10. Under **Try it**, press **Preview order**. You see the strikes it would pick right now, the price, each side's stop and target, and a risk graph. Nothing is sent and nothing is saved. Outside market hours the prices are only a guide.
11. Under **Schedule**, tick the box that starts "I understand: the stops of Call side, Put side are watched by this app…", then press **Arm it**. The badge changes from **Off** to **Armed**.

## What you'll see
- **📅 Schedule** on the Automation page shows a line for 10:00 with **Paper**.
- At 10:00 the bot picks its strikes, has your broker check the order, and records a pretend fill at the middle price. Nothing is sent.
- Its pretend trades are in **Journal → Paper trades**, never in your real journal or totals. The app watches each pretend stop, target and timed close from live prices.
- The app has to be running, and the computer awake, at 10:00 for the entry to happen.

Watch it for a few days. When you're ready for real orders, see [How to go from Paper to Live](how-to-paper-to-live.md).

More detail: [Building a bot](automation.md#building-a-bot), [Live and Paper](automation.md#live-and-paper), [Preview order](automation.md#preview-order).
