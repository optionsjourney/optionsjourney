# 0075. Arm, turn off and switch Live or Paper for many bots at once

- **Status:** Accepted
- **Date:** 2026-10-19

## Context
The Automation list's bar for ticked bots offers Skip day and Tags only. With many bots (for example while Tracker2 still places the real orders and the bots here run on Paper), arming, turning off or switching mode one bot at a time is slow. Tracker2 has no bulk version of these, so this goes beyond parity.

## Decision
- The bar gets **Arm**, **Turn off**, **Paper** and **Live**.
- **Turn off** and **Paper** act at once: neither can send an order.
- **Arm** and **Live** first show one page listing every ticked bot (mode, account, per-trade and per-day caps, where its stops are held), and act only once confirmed. Arming needs the box ticked whenever any ticked bot is Live or has stops the app watches; going Live needs it when any has watched stops. The box is checked on the server, as for one bot.
- Each bot goes through the same store calls as the one-bot buttons (`set_armed`, `set_mode`), so archived and futures bots aren't armed, going Live closes paper trades and leaves the bot off, and a bot whose paper trades can't be closed stays in Paper. Bots refused or failed are named.

## Why
- Decision 0061's rule, that a Live bot is armed only after its summary and box, still holds: the page is the same summary for several bots.
- One code path per change keeps the bulk and single buttons from drifting apart.

## Consequences
- One tick of the box arms many Live bots at once. The list on the page is what keeps that deliberate.
