"""Public parser capability for management pages."""
from .domain import page_path, parse, title


class PagesFacade:
    path = staticmethod(page_path)
    parse = staticmethod(parse)
    title = staticmethod(title)
