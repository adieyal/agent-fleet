"""The art bake-off page at /prototype/bakeoff: served but not linked from the deck, reads art/bakeoff/ from the
checkout and nothing beyond it, and draws variant B2 (the one committed on this branch) unlit on a 2D canvas."""

from collections.abc import Iterator
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page


def open_bakeoff(browser: Browser, base_url: str, query: str) -> tuple[Page, list[str]]:
    page = browser.new_page(viewport={"width": 1000, "height": 600})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base_url}/prototype/bakeoff?{query}")
    page.wait_for_function("window.bakeoff && (window.bakeoff.ready || window.bakeoff.error)", timeout=60_000)
    return page, errors


@pytest.fixture
def b2(browser: Browser, base_url: str) -> Iterator[Page]:
    page, errors = open_bakeoff(browser, base_url, "variant=B2&shot&t=0.3")
    yield page
    page.close()
    assert errors == []


def test_the_page_is_served_but_not_linked_from_the_deck(base_url: str) -> None:
    with urlopen(base_url + "/prototype/bakeoff") as response:
        assert response.headers["Content-Type"].startswith("text/html")
    with urlopen(base_url + "/") as response:
        assert b"/prototype/" not in response.read()


@pytest.mark.parametrize("path", ["/art/bakeoff/../../pyproject.toml", "/art/bakeoff/%2e%2e/measure.py",
                                  "/concept/../../README.md", "/prototype/../server.py"])
def test_it_serves_nothing_outside_its_folders(base_url: str, path: str) -> None:
    with pytest.raises(HTTPError):
        urlopen(base_url + path)


def test_b2_draws_the_scene_without_webgl_or_three(b2: Page) -> None:
    stats = b2.evaluate("window.bakeoff.stats()")
    assert stats["frame"]["frames"] > 0
    assert stats["bytes"]["files"] >= 8  # bench, plant, lantern, three robots and their masks
    assert not b2.evaluate("performance.getEntriesByType('resource').some(e => e.name.includes('/vendor/three/'))")
    assert b2.locator("canvas").count() == 1


def pixels(page: Page) -> str:
    return page.evaluate("document.querySelector('canvas').toDataURL()")


def test_zoom_moves_from_l2_framing_to_the_floor(b2: Page) -> None:
    at_l2 = pixels(b2)
    b2.evaluate("window.bakeoff.setZoom(0)")
    b2.wait_for_timeout(100)
    assert pixels(b2) != at_l2


def test_a_missing_variant_is_reported_not_faked(browser: Browser, base_url: str) -> None:
    page, _ = open_bakeoff(browser, base_url, "variant=B2&view=arch")
    assert page.evaluate("window.bakeoff.missing") == []
    page.close()
    page, _ = open_bakeoff(browser, base_url, "variant=B1&view=arch")
    assert any("no floor tile" in m for m in page.evaluate("window.bakeoff.missing"))
    page.close()
