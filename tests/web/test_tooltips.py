"""Tooltip styles and the info() macro (#230)."""

import re
from pathlib import Path

import pytest

from optionsjourney.app import create_app

STATIC = Path(__file__).parents[2] / "src/optionsjourney/web/static"


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


def test_info_macro_learn_more(app):
    src = '{% from "_tooltip.html" import info %}{{ info("x", "T", "X", more="/help/trade#pop") }}'
    with app.test_request_context():
        html = app.jinja_env.from_string(src).render()
    # a new tab, so the screen the person was on keeps its place
    assert '<a class="tip__more" href="/help/trade#pop" target="_blank" rel="noopener">' in html
    assert "Learn more" in html


def test_tip_styles_on_both_layouts(app):
    client = app.test_client()
    for path in ("/journal", "/trade"):
        body = client.get(path).get_data(as_text=True)
        assert "css/tooltips.css" in body, path
        assert "js/tooltips.js" in body, path


def test_shown_tips_take_clicks():
    # an open tip and a hovered one (a button's tip has no ⓘ to open it): Learn more can be clicked
    css = (STATIC / "css/tooltips.css").read_text()
    rule = css[css.rindex(".tip--open > .tip__text {") :]
    assert "pointer-events: auto" in rule.split("}")[0]
    selector = css[: css.rindex(".tip--open > .tip__text {")].rsplit("}", 1)[1]
    assert ".tip:hover > .tip__text" in selector and ".tip--hover > .tip__text" in selector


def test_tip_text_is_never_a_heading_style():
    css = (STATIC / "css/tooltips.css").read_text()
    base = css[css.index(".tip__text {") :].split("}")[0]
    for rule in ("text-transform: none", "letter-spacing: normal", "font-weight: 400"):
        assert rule in base, rule
    assert "th .tip__text" not in css  # no per-container overrides left


def test_accounts_waits_on_the_same_shown_tips_as_tip_js():
    js = (STATIC / "js").joinpath
    shown = re.search(r"TIP_SHOWN = '([^']+)'", js("tip.js").read_text()).group(1)
    assert "focus-within" not in shown
    assert f'"{shown}"' in js("accounts.js").read_text()


def test_tooltips_js_is_a_module_on_every_layout():
    for layout in ("base.html", "tracker_base.html", "chart_bare_base.html"):
        html = (STATIC.parent / "templates" / layout).read_text()
        tag = "<script type=\"module\" src=\"{{ url_for('static', filename='js/tooltips.js')"
        assert tag in html, layout


def test_app_css_has_no_tip_rules():
    assert ".tip__text" not in (STATIC / "css/app.css").read_text()
