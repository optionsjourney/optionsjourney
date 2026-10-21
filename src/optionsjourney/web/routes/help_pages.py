"""The Help pages: the plain-language guide (docs/guide) rendered inside the app."""

from flask import Blueprint, abort, render_template, request, send_from_directory

from optionsjourney.web.guide import guide_dir, page_names, render_page, search

bp = Blueprint("help", __name__)


def _pages(root):
    return [render_page(root, "index"), *(render_page(root, n) for n in page_names(root))]


def _show(name: str):
    root = guide_dir()
    try:
        page = render_page(root, name)
    except KeyError:
        abort(404)
    return render_template("help.html", page=page, pages=_pages(root), query="")


@bp.route("/help")
def help_page():
    return _show("index")


@bp.route("/help/search")
def help_search():
    root = guide_dir()
    query = request.args.get("q", "").strip()
    return render_template(
        "help.html", page=None, pages=_pages(root), query=query, results=search(root, query)
    )


@bp.route("/help/<name>")
def help_topic(name: str):
    if name == "index":
        abort(404)
    return _show(name)


@bp.route("/help/images/<path:filename>")
def help_image(filename: str):
    images = guide_dir() / "images"
    if not images.is_dir():
        abort(404)
    return send_from_directory(images, filename)
