import re
from pathlib import Path

import pytest

from optionsjourney.app import create_app
from optionsjourney.web.guide import guide_dir, page_names, render_page

SRC = Path(__file__).resolve().parents[2] / "src" / "optionsjourney" / "web"
_HELP_LINK = re.compile(r"/help/([\w-]+)(?:#([\w-]+))?")


def help_links(text: str) -> list[tuple[str, str]]:
    """Every /help/<page>[#anchor] address in text, as (page, anchor); anchor may be ''."""
    return [(m.group(1), m.group(2) or "") for m in _HELP_LINK.finditer(text)]


def test_help_links_helper():
    found = help_links('more="/help/trade#order-ticket" and "/help/journal" but not /helpful')
    assert found == [("trade", "order-ticket"), ("journal", "")]


@pytest.fixture
def client(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app.test_client()


def test_help_index(client):
    root = guide_dir()
    first = (root / "README.md").read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
    resp = client.get("/help")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert first in body
    for name in page_names(root):
        assert f'href="/help/{name}"' in body


def test_help_topic(client):
    resp = client.get("/help/trade")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h1" in body
    assert 'class="help__list"' in body


@pytest.mark.parametrize(
    "path", ["/help/nope", "/help/..%2f..%2fpyproject", "/help/images/../../pyproject.toml"]
)
def test_help_unknown_and_traversal_404(client, path):
    assert client.get(path).status_code == 404


def test_help_image_404_without_images_folder(client):
    assert client.get("/help/images/none.png").status_code == 404


def test_nav_has_help(client):
    assert 'href="/help"' in client.get("/journal").get_data(as_text=True)


def _check(page_anchors: dict[str, frozenset[str]], links: list[tuple[str, str]]) -> list[str]:
    bad = []
    for page, anchor in links:
        if page == "images":
            continue
        if page not in page_anchors:
            bad.append(f"/help/{page}")
        elif anchor and anchor not in page_anchors[page]:
            bad.append(f"/help/{page}#{anchor}")
    return bad


def _pages():
    root = guide_dir()
    return root, {n: render_page(root, n) for n in page_names(root)}


def test_every_guide_link_resolves():
    root, pages = _pages()
    anchors = {n: p.anchors for n, p in pages.items()}
    anchors["index"] = render_page(root, "index").anchors
    bad = []
    for name, page in [*pages.items(), ("index", render_page(root, "index"))]:
        hrefs = re.findall(r'href="([^"]*)"', page.html)
        for href in hrefs:
            if href == "/help":
                continue
            if href.startswith("#"):
                if href[1:] not in page.anchors:
                    bad.append(f"{name}: {href}")
            elif href.startswith("/help/"):
                bad += [f"{name}: {b}" for b in _check(anchors, help_links(href))]
    assert not bad


def test_every_learn_more_link_resolves():
    _, pages = _pages()
    anchors = {n: p.anchors for n, p in pages.items()}
    links = []
    for pattern in ("templates/**/*.html", "static/js/**/*.js"):
        for path in SRC.glob(pattern):
            if "vendor" in path.parts:
                continue
            links += help_links(path.read_text(encoding="utf-8"))
    assert not _check(anchors, links)


def test_check_catches_a_bad_link():
    anchors = {"trade": frozenset({"order-ticket"})}
    assert _check(anchors, [("trade", "order-ticket")]) == []
    assert _check(anchors, [("trade", "nope"), ("gone", "")]) == ["/help/trade#nope", "/help/gone"]


def test_index_lists_every_page():
    root = guide_dir()
    index = (root / "README.md").read_text(encoding="utf-8")
    for name in page_names(root):
        assert f"({name}.md)" in index


def test_help_search_box_on_every_page(client):
    body = client.get("/help/trade").get_data(as_text=True)
    assert 'action="/help/search"' in body and 'name="q"' in body


def test_help_search_results(client):
    resp = client.get("/help/search?q=stop+loss")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'value="stop loss"' in body
    assert "<mark>" in body
    for page, _anchor in help_links(body.split("help__results", 1)[1]):
        assert page in page_names(guide_dir())


def test_help_search_nothing_found_and_escaped(client):
    body = client.get("/help/search?q=%3Cb%3Eunicornzz").get_data(as_text=True)
    assert "Nothing found" in body
    assert "<b>unicornzz" not in body


def test_help_search_empty(client):
    assert client.get("/help/search").status_code == 200
