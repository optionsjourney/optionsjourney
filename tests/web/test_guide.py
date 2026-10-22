import re
from pathlib import Path

import pytest

from optionsjourney.web.guide import guide_dir, page_names, render_page, search, slug


def test_slug_matches_github():
    assert (
        slug("Calendars, diagonals and trades held overnight")
        == "calendars-diagonals-and-trades-held-overnight"
    )
    assert slug("Which exit rests at the broker") == "which-exit-rests-at-the-broker"
    assert slug("P/L Day") == "pl-day"


def test_guide_dir_prefers_package(tmp_path):
    pkg, repo = tmp_path / "pkg", tmp_path / "repo"
    pkg.mkdir()
    repo.mkdir()
    (pkg / "README.md").write_text("x")
    (repo / "README.md").write_text("x")
    assert guide_dir([pkg, repo]) == pkg
    (pkg / "README.md").unlink()
    assert guide_dir([pkg, repo]) == repo
    (repo / "README.md").unlink()
    with pytest.raises(FileNotFoundError):
        guide_dir([pkg, repo])


def _root(tmp_path: Path, a: str) -> Path:
    (tmp_path / "README.md").write_text("# Index\n")
    (tmp_path / "a.md").write_text(a)
    (tmp_path / "b.md").write_text("# B\n")
    return tmp_path


def test_render_rewrites_links(tmp_path):
    root = _root(
        tmp_path,
        "# A\n\n[B](b.md) [B sec](b.md#x) [Index](README.md) [Road](../roadmap.md) "
        "[here](#y)\n\n<script>bad()</script>\n\n![p](images/x.png)\n",
    )
    html = render_page(root, "a").html
    assert 'href="/help/b"' in html
    assert 'href="/help/b#x"' in html
    assert 'href="/help"' in html
    assert 'href="#y"' in html
    assert "Road" in html and "roadmap" not in html
    assert "<script>" not in html
    assert 'src="/help/images/x.png"' in html


def test_render_headings_get_ids(tmp_path):
    root = _root(
        tmp_path, "# Title here\n\n## Which exit rests at the broker\n\n## Dup\n\n## Dup\n"
    )
    page = render_page(root, "a")
    assert 'id="which-exit-rests-at-the-broker"' in page.html
    assert "which-exit-rests-at-the-broker" in page.anchors
    assert page.title == "Title here"
    assert {"dup", "dup-1"} <= page.anchors
    assert render_page(root, "index").title == "Index"


def test_unknown_page_raises(tmp_path):
    root = _root(tmp_path, "# A\n")
    with pytest.raises(KeyError):
        render_page(root, "../secret")


def test_real_guide_renders():
    root = guide_dir()
    for name in [*page_names(root), "index"]:
        assert render_page(root, name).title


NEW_PAGES = [  # later tasks add their pages here
    "behind-the-scenes",
    "how-to-first-bot",
    "how-to-paper-to-live",
    "how-to-send-a-trade",
    "how-to-hand-a-trade-to-a-bot",
    "how-to-adjust-before-the-close",
    "how-to-phone-alerts",
    "updating",
    "faq",
    "transactions",
    "tags",
]
_JARGON = {"thread", "loop", "api", "cron", "endpoint", "reconcile"}


def test_new_pages_have_no_jargon():
    root = guide_dir()
    for name in NEW_PAGES:
        path = root / f"{name}.md"
        assert path.is_file(), name
        words = set(re.findall(r"[a-z]+", path.read_text(encoding="utf-8").lower()))
        assert not words & _JARGON, (name, words & _JARGON)


def _search_root(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# Help home\n\nStart here.\n")
    (tmp_path / "alpha.md").write_text(
        "# Alpha page\n\nIntro about bots.\n\n"
        "## Stop loss\n\nA **stop** closes the trade when the loss grows.\n\n"
        "## Profit target\n\nThe target closes it at a gain. No stop here... wait, stop.\n"
    )
    (tmp_path / "beta.md").write_text("# Beta\n\n## Alerts\n\nAlerts reach your phone.\n")
    return tmp_path


def test_search_finds_sections_with_every_word(tmp_path):
    hits = search(_search_root(tmp_path), "stop LOSS")
    assert [(h.page, h.anchor, h.heading) for h in hits] == [("alpha", "stop-loss", "Stop loss")]
    assert hits[0].page_title == "Alpha page"


def test_search_ranks_heading_matches_first(tmp_path):
    hits = search(_search_root(tmp_path), "stop")
    assert [h.anchor for h in hits] == ["stop-loss", "profit-target"]


def test_search_top_of_page_and_index(tmp_path):
    hits = search(_search_root(tmp_path), "start")
    assert [(h.page, h.anchor, h.heading) for h in hits] == [("index", "help-home", "Help home")]


def test_search_snippet_marks_words_and_reads_plain(tmp_path):
    (hit,) = search(_search_root(tmp_path), "grows")
    assert (
        "".join(text for text, _ in hit.snippet) == "A stop closes the trade when the loss grows."
    )
    assert [text for text, marked in hit.snippet if marked] == ["grows"]


def test_search_snippet_is_cut_around_the_word(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Home\n\n" + "word " * 100 + "needle " + "word " * 100 + "\n"
    )
    (hit,) = search(tmp_path, "needle")
    text = "".join(t for t, _ in hit.snippet)
    assert text.startswith("…") and text.endswith("…") and len(text) < 260
    assert ("needle", True) in hit.snippet


def test_search_empty_or_no_match(tmp_path):
    root = _search_root(tmp_path)
    assert search(root, "   ") == []
    assert search(root, "stop unicorn") == []


def test_search_limit(tmp_path):
    assert len(search(_search_root(tmp_path), "a", limit=2)) == 2


def test_search_ranks_the_phrase_higher(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Home\n\n## Stop styles\n\nA stop, a stop, a stop. Loss.\n\n"
        "## Exits\n\nThe stop loss closes it. Each stop loss rests.\n"
    )
    assert [h.anchor for h in search(tmp_path, "stop loss")] == ["exits", "stop-styles"]


def test_render_with_custom_link_targets(tmp_path):
    root = _root(
        tmp_path, "# A\n[b](b.md#x) [i](README.md) [w](https://e.com) ![p](images/p.png)\n"
    )
    page = render_page(
        root, "a", page_url=lambda stem: f"https://gh/{stem}.md", image_prefix="/img/"
    )
    assert 'href="https://gh/b.md#x"' in page.html
    assert 'href="https://gh/README.md"' in page.html
    assert 'href="https://e.com"' in page.html
    assert 'src="/img/images/p.png"' in page.html
