"""Showing a long list a page at a time."""

from dataclasses import dataclass

from flask import request, url_for


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    number: int  # 1-based
    per_page: int
    total: int

    @property
    def first(self) -> int:
        """1-based position of the first item shown."""
        return (self.number - 1) * self.per_page + 1 if self.total else 0

    @property
    def last(self) -> int:
        return self.first + len(self.items) - 1 if self.total else 0

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))

    def url(self, number: int) -> str:
        """This page's URL with every other argument kept."""
        args = request.args.to_dict(flat=False)
        args["page"] = [str(number)]
        return url_for(request.endpoint, **request.view_args, **args)


def paginate[T](items: list[T], per_page: int) -> Page[T]:
    """The page of `items` named by the request's ?page=, clamped to range."""
    total = len(items)
    pages = max(1, -(-total // per_page))
    number = min(max(request.args.get("page", 1, type=int), 1), pages)
    start = (number - 1) * per_page
    return Page(items[start : start + per_page], number, per_page, total)
