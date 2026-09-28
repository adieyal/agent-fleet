"""The reader draws code, diagrams and images inline, in both of its themes."""

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from fleet.web.fixture import FixtureState
from fleet.web.library import ProjectLibrary
from fleet.web.server import make_handler

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80" viewBox="0 0 240 80">
<script>document.title = 'svg script ran'</script>
<rect x="4" y="4" width="232" height="72" rx="10" fill="#e2b56d"/><text x="120" y="48" font-size="20" text-anchor="middle">fleetd</text></svg>"""
LONG_LINE = "x = " + " + ".join(f"value_{i}" for i in range(80))
DOCUMENT = f"""# Rich document

Some code:

```python
def greet(name: str) -> str:
    return f"hello {{name}}"  # a comment
{LONG_LINE}
```

```json
{{"ok": true, "count": 3}}
```

A diagram:

```mermaid
graph LR
  Deck --> Fleetd
  Fleetd --> Agent
```

A broken one:

```mermaid
graph LR
  A -->
  this is not mermaid ((
```

```dot
digraph {{ reader -> fleetd }}
```

![architecture](img/architecture.svg)

![remote chart](https://example.com/chart.png)
"""


@dataclass
class Reader:
    page: Page
    shots: str | None
    requests: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def shot(self, name: str) -> None:
        if self.shots:
            self.page.locator("#reader .rd-sheet").screenshot(path=str(Path(self.shots) / f"reader-{name}.png"))


@pytest.fixture(scope="module")
def library_url(tmp_path_factory: pytest.TempPathFactory, deck_state: FixtureState) -> Iterator[str]:
    root = tmp_path_factory.mktemp("library")
    (root / "docs" / "img").mkdir(parents=True)
    (root / "docs" / "rich.md").write_text(DOCUMENT)
    (root / "docs" / "img" / "architecture.svg").write_text(SVG)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(deck_state, ProjectLibrary({"notes": str(root)})))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def reader(browser: Browser, library_url: str, request: pytest.FixtureRequest) -> Iterator[Reader]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce",
                                  permissions=["clipboard-read", "clipboard-write"])
    context.add_init_script("localStorage.setItem('fleet.reader.theme', 'dark')")
    page = context.new_page()
    opened = Reader(page, request.config.getoption("--shots"))
    page.on("request", lambda req: opened.requests.append(req.url))
    page.on("console", lambda message: message.type == "error" and opened.errors.append(message.text))
    page.on("pageerror", lambda error: opened.errors.append(str(error)))
    page.goto(library_url + "/")
    page.wait_for_function("window.fleetDeck")
    page.locator("#libraryOpen").click()
    page.locator('#libList .lib-doc[data-id="docs/rich.md"]').click()
    expect(page.locator("#rdBody h1")).to_have_text("Rich document")
    yield opened
    context.close()


def test_code_is_highlighted_scrolls_inside_its_block_and_copies(reader: Reader) -> None:
    page = reader.page
    block = page.locator(".prose .rd-code").first
    expect(block.locator("code.hljs .hljs-keyword").first).to_have_text("def")
    expect(block.locator(".hljs-comment")).to_have_text("# a comment")
    keyword = block.locator(".hljs-keyword").first.evaluate("el => getComputedStyle(el).color")
    assert keyword != block.locator("pre").evaluate("el => getComputedStyle(el).color")
    # the long line scrolls the block, not the reader
    assert block.locator("pre").evaluate("el => el.scrollWidth > el.clientWidth")
    assert page.locator("#rdBody").evaluate("el => el.scrollWidth <= el.clientWidth")
    block.locator(".rd-code-copy").click()
    expect(block.locator(".rd-code-copy")).to_have_text("Copied")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied.startswith("def greet(name: str) -> str:") and LONG_LINE in copied
    assert reader.errors == []


def test_mermaid_draws_inline_with_its_source_a_toggle_away(reader: Reader) -> None:
    page = reader.page
    figure = page.locator('.rd-diagram[data-diagram="mermaid"]:not(.failed)')
    expect(figure.locator(".rd-diagram-view svg")).to_be_visible()
    expect(figure.locator(".rd-diagram-view svg")).to_contain_text("Fleetd")
    source = figure.locator(".rd-code")
    expect(source).to_be_hidden()
    figure.locator(".rd-diagram-toggle").click()
    expect(source).to_be_visible()
    expect(source.locator("pre")).to_contain_text("Deck --> Fleetd")
    figure.locator(".rd-diagram-toggle").click()
    expect(source).to_be_hidden()
    assert reader.errors == []


def test_a_broken_mermaid_block_shows_its_source_and_the_error(reader: Reader) -> None:
    figure = reader.page.locator(".rd-diagram.failed")
    expect(figure).to_have_count(1)
    expect(figure.locator("[role=alert]")).to_contain_text("This diagram could not be drawn.")
    expect(figure.locator(".rd-diagram-error code")).not_to_be_empty()
    expect(figure.locator(".rd-code pre")).to_be_visible()
    expect(figure.locator(".rd-code pre")).to_contain_text("this is not mermaid ((")
    expect(figure.locator(".rd-diagram-toggle")).to_have_attribute("aria-expanded", "true")


def test_graphviz_draws_as_an_image(reader: Reader) -> None:
    image = reader.page.locator('.rd-diagram[data-diagram="dot"] .rd-diagram-view img')
    expect(image).to_be_visible()
    assert image.evaluate("img => img.complete && img.naturalWidth > 0")


def test_linked_svg_shows_inline_in_both_themes_and_remote_images_are_not_fetched(reader: Reader) -> None:
    page = reader.page
    image = page.locator('.prose img[alt="architecture"]')
    diagram = page.locator('.rd-diagram[data-diagram="mermaid"]:not(.failed) .rd-diagram-view svg')
    for theme in ("dark", "paper"):
        if page.locator("#reader .rd-sheet").get_attribute("data-theme") != theme:
            page.locator("#rdTheme").click()
        expect(page.locator("#reader .rd-sheet")).to_have_attribute("data-theme", theme)
        expect(image).to_be_visible()
        page.wait_for_function("img => img.complete && img.naturalWidth === 240", arg=image.element_handle())
        expect(diagram).to_be_visible()
        page.wait_for_timeout(200)
        image.scroll_into_view_if_needed()
        reader.shot(f"images-{theme}")
        page.locator(".prose .rd-code").first.scroll_into_view_if_needed()
        reader.shot(f"code-{theme}")
        diagram.scroll_into_view_if_needed()
        reader.shot(f"diagram-{theme}")
    assert image.get_attribute("src").startswith("/api/library/asset?")
    assert page.evaluate("document.title") != "svg script ran"
    expect(page.locator(".prose .rd-img-note a")).to_have_attribute("href", "https://example.com/chart.png")
    expect(page.locator(".prose .rd-img-note a")).to_contain_text("remote chart")
    assert not [url for url in reader.requests if "example.com" in url]
    assert reader.errors == []
