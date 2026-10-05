"""Public parser capability for management pages."""
from .domain import page_path, parse, title, validate
from .domain.annotations import attachment, prose_text, validate_selector


class PagesFacade:
    path = staticmethod(page_path)
    parse = staticmethod(parse)
    validate = staticmethod(validate)
    title = staticmethod(title)

    prose_text = staticmethod(prose_text)
    attachment = staticmethod(attachment)
    validate_selector = staticmethod(validate_selector)
