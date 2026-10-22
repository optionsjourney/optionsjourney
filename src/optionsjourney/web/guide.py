"""Render the plain-language guide (docs/guide/*.md) for the in-app Help pages."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.anchors import anchors_plugin


@dataclass(frozen=True)
class GuidePage:
    name: str
    title: str
    html: str
    anchors: frozenset[str]


def guide_dir(candidates: Sequence[Path] | None = None) -> Path:
    """The first folder that holds the guide's README.md."""
    if candidates is None:
        candidates = [
            Path(str(resources.files("optionsjourney") / "guide")),
            Path(__file__).resolve().parents[3] / "docs" / "guide",
        ]
    for candidate in candidates:
        if (candidate / "README.md").is_file():
            return candidate
    raise FileNotFoundError("The guide folder was not found")


def page_names(root: Path) -> list[str]:
    return sorted(p.stem for p in root.glob("*.md") if p.stem != "README")


def slug(heading_text: str) -> str:
    """GitHub's anchor rule (duplicates are numbered by the anchors plugin)."""
    text = heading_text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return text.replace(" ", "-")


def _md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    md.use(anchors_plugin, max_level=4, slug_func=slug)
    return md


def help_url(stem: str) -> str:
    """The in-app address of a guide page."""
    return "/help" if stem == "README" else f"/help/{stem}"


def _rewrite_href(href: str, page_url: Callable[[str], str]) -> str | None:
    """The address for a guide link, or None to drop the link."""
    if href.startswith(("http://", "https://", "#")):
        return href
    if href.startswith("../"):
        return None
    path, sep, fragment = href.partition("#")
    if path.endswith(".md") and "/" not in path:
        return f"{page_url(path[:-3])}{sep}{fragment}"
    return href


def _rewrite_inline(
    children: list[Token], page_url: Callable[[str], str], image_prefix: str
) -> list[Token]:
    out: list[Token] = []
    dropping = False
    for tok in children:
        if tok.type == "link_open":
            new = _rewrite_href(str(tok.attrGet("href") or ""), page_url)
            if new is None:
                dropping = True
                continue
            tok.attrSet("href", new)
        elif tok.type == "link_close" and dropping:
            dropping = False
            continue
        elif tok.type == "image":
            src = str(tok.attrGet("src") or "")
            if src.startswith("images/"):
                tok.attrSet("src", f"{image_prefix}{src}")
        out.append(tok)
    return out


def render_page(
    root: Path,
    name: str,
    page_url: Callable[[str], str] = help_url,
    image_prefix: str = "/help/",
) -> GuidePage:
    """A guide page as HTML. `page_url` gives the address of another guide page from its
    file name without .md; `image_prefix` goes in front of images/... sources."""
    if name == "index":
        path = root / "README.md"
    elif name in page_names(root):
        path = root / f"{name}.md"
    else:
        raise KeyError(name)
    md = _md()
    tokens = md.parse(path.read_text(encoding="utf-8"))
    title = name
    anchors: set[str] = set()
    found_title = False
    for i, tok in enumerate(tokens):
        if tok.type == "inline" and tok.children:
            tok.children = _rewrite_inline(tok.children, page_url, image_prefix)
        elif tok.type == "heading_open":
            anchor = tok.attrGet("id")
            if anchor:
                anchors.add(str(anchor))
            if tok.tag == "h1" and not found_title:
                found_title = True
                title = tokens[i + 1].content
    return GuidePage(name, title, md.renderer.render(tokens, md.options, {}), frozenset(anchors))


@dataclass(frozen=True)
class SearchHit:
    """One guide section that holds every word searched for."""

    page: str
    page_title: str
    heading: str
    anchor: str
    snippet: tuple[tuple[str, bool], ...]  # (text, is a searched word)
    score: int


@dataclass
class _Section:
    heading: str
    anchor: str
    text: list[str]


_SNIPPET = 200


def _plain(children: list[Token]) -> str:
    return "".join(
        " " if c.type in ("softbreak", "hardbreak") else c.content
        for c in children
        if c.type in ("text", "code_inline", "softbreak", "hardbreak")
    )


def _sections(root: Path, name: str) -> tuple[str, list[_Section]]:
    path = root / ("README.md" if name == "index" else f"{name}.md")
    md = _md()
    tokens = md.parse(path.read_text(encoding="utf-8"))
    title: str | None = None
    sections: list[_Section] = []
    heading_next: _Section | None = None
    heading_tag = ""
    for tok in tokens:
        if tok.type == "heading_open":
            heading_next = _Section("", str(tok.attrGet("id") or ""), [])
            heading_tag = tok.tag
            sections.append(heading_next)
        elif tok.type == "inline":
            text = _plain(tok.children or [])
            if heading_next is not None:
                heading_next.heading = text
                if title is None and heading_tag == "h1":
                    title = text
                heading_next = None
            elif sections:
                sections[-1].text.append(text)
            else:
                sections.append(_Section("", "", [text]))
    return title or name, sections


def _snippet(text: str, words: list[str], phrase: str) -> tuple[tuple[str, bool], ...]:
    """About 200 characters of `text` around the phrase (else the first word found),
    split so the searched words can be marked."""
    lower = text.lower()
    found = (
        [lower.find(phrase)]
        if phrase in lower
        else [i for i in (lower.find(w) for w in words) if i >= 0]
    )
    start = 0
    if found and len(text) > _SNIPPET:
        start = max(0, min(found) - _SNIPPET // 3)
        if start:
            space = text.find(" ", start)
            start = space + 1 if 0 <= space < min(found) else start
    end = start + _SNIPPET
    if end < len(text):
        space = text.rfind(" ", start, end)
        end = space if space > start else end
    cut = text[start:end]
    pattern = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I)
    parts: list[tuple[str, bool]] = [("…", False)] if start else []
    pos = 0
    for m in pattern.finditer(cut):
        if m.start() > pos:
            parts.append((cut[pos : m.start()], False))
        parts.append((m.group(), True))
        pos = m.end()
    if pos < len(cut):
        parts.append((cut[pos:], False))
    if end < len(text):
        parts.append(("…", False))
    return tuple(parts)


def search(root: Path, query: str, limit: int = 50) -> list[SearchHit]:
    """Guide sections holding every word of `query` (any case), best first: a word in
    the heading counts most, then how often the words appear in the text (up to 3 times,
    so a long section doesn't win by length), and the words together as typed count extra."""
    words = list(dict.fromkeys(w.lower() for w in query.split()))
    if not words:
        return []
    phrase = " ".join(words)
    hits: list[SearchHit] = []
    for name in ["index", *page_names(root)]:
        title, sections = _sections(root, name)
        for section in sections:
            text = " ".join(section.text)
            heading, body = section.heading.lower(), text.lower()
            if not all(w in heading or w in body for w in words):
                continue
            score = sum(10 * (w in heading) + min(body.count(w), 3) for w in words)
            if len(words) > 1:
                score += 20 * (phrase in heading) + 10 * min(body.count(phrase), 3)
            hits.append(
                SearchHit(
                    name,
                    title,
                    section.heading or title,
                    section.anchor,
                    _snippet(text, words, phrase),
                    score,
                )
            )
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]
