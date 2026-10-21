import pytest
from flask import Flask

from optionsjourney.web.pagination import paginate


@pytest.fixture
def app():
    app = Flask(__name__)

    @app.route("/list")
    def listing():
        return ""

    return app


def test_pages(app):
    items = list(range(250))
    with app.test_request_context("/list?status=open&page=3"):
        page = paginate(items, 100)
        assert page.items == list(range(200, 250))
        assert (page.first, page.last, page.pages) == (201, 250, 3)
        assert page.url(2) == "/list?status=open&page=2"


@pytest.mark.parametrize(("asked", "shown"), [("0", 1), ("-4", 1), ("99", 3), ("x", 1)])
def test_out_of_range_pages_are_clamped(app, asked, shown):
    with app.test_request_context(f"/list?page={asked}"):
        assert paginate(list(range(250)), 100).number == shown


def test_empty_list(app):
    with app.test_request_context("/list"):
        page = paginate([], 100)
        assert (page.items, page.first, page.last, page.pages) == ([], 0, 0, 1)
