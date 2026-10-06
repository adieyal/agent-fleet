"""The canvas page uses the shared document renderer and reader controls."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Browser, Route, expect
import pytest

from fleet_web.documents import render_markdown


MARKDOWN = """# Canvas report

## Code

```python
def greet():
    return 'hello'
```

## Diagram

```mermaid
graph LR
  Canvas --> Reader
```

## Assets

![local](img/local.svg)
![remote](https://example.com/remote.png)

| Name | Count |
| --- | --- |
| fleet | 42 |
"""


def test_canvas_reader_renders_and_navigates_job_documents(
        browser: Browser, base_url: str, request: pytest.FixtureRequest) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script("localStorage.setItem('fleet.reader.theme', 'dark')")
    page = context.new_page()
    errors: list[str] = []
    reads: list[dict[str, list[str]]] = []
    requests: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda req: requests.append(req.url))
    failed = True

    def serve(route: Route) -> None:
        query = parse_qs(urlsplit(route.request.url).query)
        reads.append(query)
        if query["id"] == ["brief"] and failed:
            route.fulfill(status=503, json={"error": "Worker unavailable"})
        else:
            route.fulfill(json={"name": "Canvas report" if query["id"] == ["report"] else "Job brief",
                                **render_markdown(MARKDOWN if query["id"] == ["report"] else "# Job brief")})

    page.route("**/api/doc?*", serve)
    page.route("**/api/doc/asset?*", lambda route: route.fulfill(
        content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="gold"/></svg>'))
    try:
        page.goto(base_url + "/canvas")
        page.evaluate("""async () => {
          const module = await import('/js/canvas/reader.js');
          const opener = document.createElement('button');
          opener.id = 'testOpen'; opener.textContent = 'Open report'; document.body.append(opener);
          opener.onclick = () => module.openJobDocument('home', 'test-job', [
            {id: 'brief', kind: 'brief', name: 'Job brief', step: 0},
            {id: 'report', kind: 'report', name: 'Canvas report', mtime: 10}
          ], 'report', 'codex');
        }""")
        page.locator('#testOpen').click()
        body = page.locator('#rdBody')
        expect(body.locator('h1')).to_have_text('Canvas report')
        expect(body.locator('.hljs-keyword').first).to_have_text('def')
        page.evaluate("""() => {
          navigator.clipboard.writeText = () => Promise.reject(new Error('Clipboard denied'));
          window.fallbackCopies = [];
          document.execCommand = command => {
            const selected = document.activeElement;
            fallbackCopies.push({command, text: selected.value, tag: selected.tagName});
            return true;
          };
        }""")
        body.locator('.rd-code-copy').first.click()
        expect(body.locator('.rd-code-copy').first).to_have_text('Copied')
        assert page.evaluate('fallbackCopies') == [{
            'command': 'copy', 'text': "def greet():\n    return 'hello'\n", 'tag': 'TEXTAREA'}]
        expect(page.locator('body > textarea')).to_have_count(0)
        diagram = body.locator('.rd-diagram-view svg')
        expect(diagram).to_be_visible()
        expect(diagram).to_contain_text('Canvas')
        body.locator('.rd-diagram-toggle').click()
        expect(body.locator('.rd-diagram .rd-code')).to_be_visible()
        image = body.locator('img[alt="local"]')
        expect(image).to_be_visible()
        page.wait_for_function('img => img.complete && img.naturalWidth === 80', arg=image.element_handle())
        asset = parse_qs(urlsplit(image.get_attribute('src')).query)
        assert asset == {'host': ['home'], 'job': ['test-job'], 'id': ['report'], 'path': ['img/local.svg']}
        expect(body.locator('.rd-table .rd-scroller td.num')).to_have_text('42')
        expect(body.locator('.rd-toc')).to_be_visible()
        expect(body.locator('.rd-toc a').first).to_have_attribute('href', '#doc-canvas-report')
        expect(page.locator('#rdMeta')).to_contain_text('home · test-job · codex')
        expect(page.locator('#rdPos')).to_have_text('1 / 2')
        expect(page.locator('#rdPrev')).to_be_disabled()
        assert page.locator('.rd-sheet').evaluate('el => getComputedStyle(el).animationName') == 'none'
        assert page.locator('.rd-scrim').evaluate('el => getComputedStyle(el).animationName') == 'none'
        for theme in ('dark', 'paper'):
            if page.locator('.rd-sheet').get_attribute('data-theme') != theme:
                page.locator('#rdTheme').click()
            expect(page.locator('.rd-sheet')).to_have_attribute('data-theme', theme)
            expect(diagram).to_be_visible()
            if shots := request.config.getoption('--shots'):
                page.locator('.rd-sheet').screenshot(path=str(Path(shots) / f'canvas-reader-{theme}.png'))
        assert page.evaluate("localStorage.getItem('fleet.reader.theme')") == 'paper'
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.locator('.rd-actions').evaluate('el => getComputedStyle(el).flexBasis') == '100%'
        assert page.locator('.rd-step').evaluate('el => getComputedStyle(el).justifyContent') == 'center'
        assert page.locator('.rd-sheet').evaluate('el => el.scrollWidth <= el.clientWidth')
        if shots := request.config.getoption('--shots'):
            page.locator('.rd-sheet').screenshot(path=str(Path(shots) / 'canvas-reader-phone.png'))
        page.keyboard.press('ArrowRight')
        expect(body.locator('[role="alert"]')).to_contain_text('Worker unavailable')
        failed = False
        body.locator('#rdRetry').click()
        expect(body.locator('h1')).to_have_text('Job brief')
        expect(page.locator('#rdPos')).to_have_text('2 / 2')
        expect(page.locator('#rdNext')).to_be_disabled()
        page.keyboard.press('ArrowLeft')
        expect(body.locator('h1')).to_have_text('Canvas report')
        page.keyboard.press('Escape')
        expect(page.locator('#reader')).to_be_hidden()
        expect(page.locator('#testOpen')).to_be_focused()
        assert [query['id'][0] for query in reads] == ['report', 'brief', 'brief', 'report']
        assert all(query['host'] == ['home'] and query['job'] == ['test-job'] for query in reads)
        assert not any('example.com' in url for url in requests)
        assert errors == []
    finally:
        context.close()
