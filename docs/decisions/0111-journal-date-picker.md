# 0111. The Journal's dates are one picker, as Tracker2

- **Status:** Accepted
- **Date:** 2026-10-23
- **Amends:** the status filter of #68 (Open + closed today, Open and closed, Open, Closed) and the period filter.

## Context
The Journal's filter row had three date controls: a status menu (Open + closed today, Open and closed, Open, Closed), a period menu (All time, 7 days, 30 days, 90 days, 1 year) and two "Opened … to …" boxes. Tracker2 has one: a button that opens presets (Open + Today (Default), Yesterday, Last 7/14/30 Days, This Month, Last Month, Year to Date, All Time, Custom Range) beside a two-month calendar. The user asked for Tracker2's.

## Decision
- **One date picker** replaces the three, with Tracker2's presets, labels and layout.
- **As in Tracker2,** Open + Today shows open trades plus those closed today, and a date range shows every trade, open or closed, opened in it.
- **Presets roll:** a preset is kept by name (`range=7d`), not as two dates, so a saved dashboard keeps meaning "the last 7 days". Tracker2 fixes the dates when picked; it has no saved views to keep them for.
- **All Time** is the app's "Open and closed" status, not Tracker2's fixed first date.
- **Open only and Closed only** aren't offered any more (Tracker2 has neither). Old links and dashboards that use them still work, and the button then says "Open" or "Closed".
- **It works without scripts:** the presets are submit buttons and the custom range has two date boxes; the script adds the calendar.

## Why
- One control for "which trades by date" is easier to read than three that can contradict each other.
- Following Tracker2 keeps the two apps alike for its users.

## Consequences
- The period menu's 90 days and 1 year are gone from the menu (Year to Date and Custom Range cover them); old `days=` links still work.
