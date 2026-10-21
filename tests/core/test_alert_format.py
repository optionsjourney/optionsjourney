import html
import re
from datetime import date
from decimal import Decimal

import pytest

from optionsjourney.core import alert_format as af
from optionsjourney.core.alert_format import SideProtection

BOTH = ["Discord", "Telegram"]


@pytest.mark.parametrize(
    ("title", "detail"),
    [
        ("Iron fly: a stop was not placed", "The put side has no stop."),
        ("Iron fly: stop not re-placed", "The side is unprotected."),
        ("An order's outcome is unclear", "TastyTrade didn't answer."),
        ("Iron fly: stops waiting for Freeze", "Freeze all orders is on."),
        ("Iron fly: a side is held back", "Its close may still be live."),
        ("Iron fly: close didn't fill", "The stop was put back."),
        ("Iron fly: timed close failed", "Check your broker."),
        ("Iron fly: entry unclear", "Recovery will settle it."),
        ("Iron fly: a run needs you", "The legs filled unevenly."),
        ("Iron fly: the wing wasn't sold", "It's still open."),
        (
            "Iron fly: profit target filled",
            "The side with SPX 5700P closed (legged out: the "
            "short was bought back, but its wing SPX 5675P wasn't sold. Check your broker).",
        ),
    ],
)
def test_each_safety_family_reads_as_safety(title, detail):
    assert af.is_safety(title, detail)


def test_a_fill_is_not_a_safety_alert():
    assert not af.is_safety("Iron fly: close filled", "The side with SPX 5700P closed at 0.55.")
    assert not af.is_safety("SPX went below 5,700.00", "now 5,699.00")


def test_the_safety_words_are_read_in_the_detail_too():
    assert af.is_safety("Iron fly: profit action 1", "Closed 1, but the stop couldn't be moved.")


@pytest.mark.parametrize(
    ("configured", "channel", "routine", "safety", "expected"),
    [
        (BOTH, "all", True, False, BOTH),
        (BOTH, "discord", True, False, ["Discord"]),
        (BOTH, "telegram", True, False, ["Telegram"]),
        (BOTH, "discord", False, False, BOTH),  # not routine: everywhere
        (BOTH, "discord", True, True, BOTH),  # safety: everywhere
        (BOTH, None, True, False, BOTH),  # no bot
        (["Discord"], "telegram", True, False, ["Discord"]),  # not set up: everywhere
        (["Telegram"], "telegram", True, False, ["Telegram"]),
        ([], "discord", True, False, []),
        (BOTH, "nonsense", True, False, BOTH),
    ],
)
def test_channels_for(configured, channel, routine, safety, expected):
    assert af.channels_for(configured, channel, routine, safety) == expected


def test_icons():
    assert af.icon_for(bot=True, routine=False, safety=True) == "🔴"
    assert af.icon_for(bot=True, routine=True, safety=False) == "✅"
    assert af.icon_for(bot=True, routine=False, safety=False) == "⚠️"
    assert af.icon_for(bot=False, routine=False, safety=False) == "🔔"
    assert af.icon_for(bot=False, routine=True, safety=True) == "🔴"


def test_a_bot_title_puts_what_happened_first_and_the_alert_name_last():
    out = af.layout(
        "Iron fly 1: close filled", "Closed at 0.55.", icon="✅",
        bot_name="Iron fly 1", alert_name="IF Joint",
    )  # fmt: skip
    assert out.line1 == "✅ close filled · IF Joint"
    assert out.line2 == "Closed at 0.55."
    assert out.details == []


def test_line_one_is_cut_to_40_and_the_full_title_kept():
    title = "Iron fly 1: a stop filled but the wing wasn't handled"
    out = af.layout(title, "Check your broker.", icon="🔴", bot_name="Iron fly 1",
                    alert_name="A very long alert name here")  # fmt: skip
    assert len(out.line1) == af.LINE1_MAX
    assert out.line1.endswith("…")
    assert out.details[0] == (None, title)


def test_context_lines_follow_and_empty_ones_are_dropped():
    out = af.layout("SPX went below 5,700.00", "now 5,699.00", icon="🔔",
                    context=[("Trade", "Iron fly · ••6789 · run 42"), ("Legs", None)])  # fmt: skip
    assert out.line1 == "🔔 SPX went below 5,700.00"
    assert out.details == [("Trade", "Iron fly · ••6789 · run 42")]


def test_discord_and_plain():
    out = af.Layout("🔴 stop not re-placed · IF", "Check your broker.",
                    [("Trade", "IF · run 4"), (None, "free line")])  # fmt: skip
    assert af.to_discord(out) == (
        "**🔴 stop not re-placed · IF**\nCheck your broker.\n\n**Trade:** IF · run 4\nfree line"
    )
    assert af.to_plain(out) == (
        "🔴 stop not re-placed · IF\nCheck your broker.\n\nTrade: IF · run 4\nfree line"
    )


def test_discord_is_cut_to_its_limit():
    out = af.Layout("🔔 x", "y" * 3000, [])
    text = af.to_discord(out)
    assert len(text) <= af.DISCORD_MAX
    assert text.endswith("…(truncated)")


def test_telegram_is_html_with_the_text_escaped():
    out = af.Layout("🔴 <b> & co", "a < b", [("Legs", "−1 SPX 5700P & more")])
    assert af.to_telegram(out) == (
        "<b>🔴 &lt;b&gt; &amp; co</b>\na &lt; b\n\n<b>Legs:</b> −1 SPX 5700P &amp; more"
    )


def test_legs_line_names_the_date_once_per_expiration():
    oct13 = date(2026, 10, 13)
    legs = [(-2, "SPX", oct13, Decimal("5700"), "P"), (2, "SPX", oct13, Decimal("5675"), "P"),
            (-2, "SPX", oct13, Decimal("5800.5"), "C")]  # fmt: skip
    assert af.legs_line(legs) == "−2 SPX 13 Oct 5700P · +2 5675P · −2 5800.5C"


def test_protection_line_says_stop_none_for_a_side_without_one():
    sides = [
        SideProtection("Put side", Decimal("3.2"), Decimal("1.10")),
        SideProtection("Call side", None, Decimal("0.5"), target_armed=True),
        SideProtection("Old side", None, None, closed=True),
    ]
    assert af.protection_line(sides) == (
        "Put side stop 3.20 · target 1.10 · Call side stop NONE · target 0.50 (armed)"
        " · Old side closed"
    )


def test_protection_line_for_one_side_skips_its_name():
    assert af.protection_line([SideProtection("Side", None, None)]) == "stop NONE"


@pytest.mark.parametrize(
    ("title", "detail"),
    [
        (
            "IC: S/L ratio exit PARTLY started",
            "Closing: Put side. Couldn't start the close of "
            "Call side: close it yourself, and check your broker.",
        ),
        ("IC: S/L ratio exit couldn't close the trade", "No side could be closed."),
        ("IC: a profit action on Put side couldn't start", "It won't be tried again."),
        ("IC: watch rules paused", "No fresh prices for 60 seconds."),
    ],
)
def test_slice_7s_watch_problems_are_safety_alerts(title, detail):
    assert af.is_safety(title, detail)
    assert af.channels_for(BOTH, "discord", True, af.is_safety(title, detail)) == BOTH


def test_watch_rules_resuming_is_routine_news():
    assert not af.is_safety("IC: watch rules resumed", "Prices are back: S/L ratio are being "
                            "checked again.")  # fmt: skip


# --- Final review fixes ---


def test_telegram_and_plain_are_cut_to_telegrams_limit_keeping_the_head():
    out = af.Layout("🔴 stop not re-placed · IC", "& " * 2500, [("Trade", "IC · run 4")])
    plain, html_text = af.to_plain(out), af.to_telegram(out)
    assert len(plain) <= af.TELEGRAM_MAX and plain.startswith("🔴 stop not re-placed · IC\n& & ")
    assert plain.endswith("…(truncated)")
    assert html_text.startswith("<b>🔴 stop not re-placed · IC</b>\n&amp; &amp; ")
    # measured as Telegram counts it (the text once the HTML is read), and no entity split
    assert len(html.unescape(re.sub(r"</?b>", "", html_text))) <= af.TELEGRAM_MAX
    assert re.search(r"&[a-z]*$", html_text.removesuffix("\n…(truncated)")) is None


def test_a_bots_name_never_makes_its_alerts_safety_ones():
    assert not af.is_safety(af.what_happened("Failsafe IC: close filled", "Failsafe IC"),
                            "The side closed at 0.55.")  # fmt: skip
    assert af.what_happened("Failsafe IC: close filled", "Failsafe IC") == "close filled"
    assert af.what_happened("An order's outcome is unclear", None) == (
        "An order's outcome is unclear"
    )
