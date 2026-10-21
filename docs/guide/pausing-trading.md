# Pausing trading

Two switches let you pause the app's trading. Open them from the **three dots** in the top bar. While one is on, a banner shows on every page.

## The two switches
| | No new trades | Freeze all orders |
|---|---|---|
| New trades | Refused | Refused |
| Exits, stops, re-arming, closes | Keep working | Nothing is sent |
| Orders already at your broker | Stay | Stay, and can still fill |
| The app watching your open trades | Yes | No |
| Use it when | You don't want more risk today | The app seems to be sending wrong orders, or the broker connection is misbehaving |

Freeze includes No new trades.

### No new trades
*Bots won't open anything new. Open trades are still watched: stops, targets and time exits keep working.*

While it's on, you see an amber banner:

> **No new trades:** bots won't open trades. Open trades are still watched. [Turn off]

### Freeze all orders
*The app stops sending anything to your broker and stops watching your trades.*

Freeze asks you to confirm first:

> **Freeze all orders?**
> - Orders already at your broker stay there and can still fill (stops you placed, resting limits).
> - The app stops watching your open trades: **no new stops, no re-arming, no profit targets, no time exits, no closes.**
> - Your trades stay open. Manage them in your broker's app until you unfreeze.
>
> [Freeze] [Cancel]

While it's on, you see a red banner:

> **Frozen:** the app isn't sending orders or watching your trades. Orders already at your broker are still active. [Unfreeze]

If the app can't tell whether a switch is on, it acts as if Freeze is on.

## What to do while frozen
- Open your broker's app and manage your open trades there.
- Remember that orders already at your broker are still active. Check them.
- When you're ready, click **Unfreeze** in the banner or the menu.

## The order log
**Order log**, in the same menu, lists everything the app tried to do with orders, newest first: previews, orders sent, replaced, cancelled, and ones the app refused (with the reason). You can filter by account, who it was from, and what it was.

## "Unclear: check your broker"
Sometimes the app sends an order and the answer never comes back (the connection dropped, say). The app can't know if the order went through, and it never guesses. It marks the attempt **Unclear: check your broker**, shows it in red, and sends you an alert.

Until it's sorted out, the app won't send another order on the same account for the same options.

It sorts itself out when:
- the app finds the order at your broker, or
- the broker shows no such order a minute or more later (this only works on the same day), or
- you clear it yourself.

### Clearing one yourself
1. Open your broker's app and look for the order. Is it there? Did it fill?
2. If it's there, leave it or cancel it in your broker's app as you like.
3. In the app's **Order log**, click **Clear** on the red row.

Only clear it after you've looked at your broker.
