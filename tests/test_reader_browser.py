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
    page.locator('[data-lib-view="all"]').click()                        # the library opens on its overview
    page.locator('#libList .tree-folder[data-folder="notes:docs/"] > summary').click()
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


@pytest.fixture
def cached_reader(reader: Reader) -> Reader:
    reader.page.evaluate("""async () => {
      window.readerModule = await import('/js/reader.js');
      window.reads = []; window.pendingReads = [];
      const originalFetch = window.fetch;
      window.fetch = (url, ...args) => {
        if (!String(url).startsWith('/api/doc?')) return originalFetch(url, ...args);
        const id = new URL(url, location.origin).searchParams.get('id');
        reads.push(id);
        return new Promise(resolve => pendingReads.push({id, resolve}));
      };
      window.finishRead = (id, error = false, image = false) => {
        const index = pendingReads.findIndex(p => p.id === id);
        const pending = pendingReads.splice(index, 1)[0];
        pending.resolve({ok: !error, json: async () => error ? {error: 'read failed'} : {
          name: id, html: image ? '<img src="architecture.svg">' : '<h1>' + id + '</h1>',
          media: image ? 'image' : 'markdown', path: 'outbox/architecture.svg', toc: []}});
      };
      window.testJob = {id: 'prefetch-job', status: 'succeeded', documents:
        Array.from({length: 5}, (_, i) => ({id: 'doc' + i, name: 'doc' + i,
          kind: 'report', mtime: 10 - i, size: 100}))};
      window.openTestDoc = i => readerModule.openReader({host: 'home', job: testJob}, testJob.documents[i]);
      openTestDoc(1);
    }""")
    return reader


def test_prefetch_neighbors_render_immediately_and_share_pending_reads(cached_reader: Reader) -> None:
    page = cached_reader.page
    page.evaluate("finishRead('doc1')")
    expect(page.locator('#rdBody h1')).to_have_text('doc1')
    assert page.evaluate('reads') == ['doc1', 'doc2', 'doc3', 'doc0']
    page.locator('#rdNext').click()
    assert page.evaluate("reads.filter(id => id === 'doc2').length") == 1
    page.evaluate("finishRead('doc2')")
    expect(page.locator('#rdBody h1')).to_have_text('doc2')
    page.evaluate("""() => {
      document.getElementById('rdPrev').click();
      window.instant = {title: document.querySelector('#rdBody h1')?.textContent,
        skeleton: !!document.querySelector('.rd-skel')};
    }""")
    assert page.evaluate('instant') == {'title': 'doc1', 'skeleton': False}
    cached_reader.shot('prefetched')


def test_stale_cache_keeps_content_and_marks_refresh(cached_reader: Reader) -> None:
    page = cached_reader.page
    page.evaluate("finishRead('doc1')")
    expect(page.locator('#rdBody h1')).to_have_text('doc1')
    page.evaluate("""() => {
      readerModule.closeReader(); testJob.documents[1].size++;
      openTestDoc(1);
      window.staleView = {title: document.querySelector('#rdBody h1')?.textContent,
        marker: document.querySelector('#rdMeta').textContent, skeleton: !!document.querySelector('.rd-skel')};
    }""")
    result = page.evaluate('staleView')
    assert result['title'] == 'doc1' and not result['skeleton']
    assert 'updating live' in result['marker']
    cached_reader.shot('stale-refresh')
    page.evaluate("finishRead('doc1', true)")
    expect(page.locator('#rdMeta')).to_contain_text('couldn’t refresh')
    page.evaluate('openTestDoc(1)')
    assert page.evaluate("reads.filter(id => id === 'doc1').length") == 3
    page.evaluate("finishRead('doc1')")
    expect(page.locator('#rdMeta')).not_to_contain_text('updating live')
    expect(page.locator('#rdMeta')).not_to_contain_text('couldn’t refresh')


def test_prefetch_image_decodes_and_skips_oversized_images(cached_reader: Reader) -> None:
    page = cached_reader.page
    page.evaluate("""() => {
      window.decoded = [];
      window.Image = class { decode() { decoded.push(this.src); return Promise.resolve(); } };
      testJob.documents[2].media = 'image';
      testJob.documents[3].media = 'image'; testJob.documents[3].size = 16 * 1024 * 1024 + 1;
      finishRead('doc1');
    }""")
    expect(page.locator('#rdBody h1')).to_have_text('doc1')
    assert page.evaluate('reads') == ['doc1', 'doc2', 'doc0']
    page.evaluate("finishRead('doc2', false, true)")
    page.wait_for_function('decoded.length === 1')
    assert 'v=8-100' in page.evaluate('decoded[0]')
    page.evaluate('openTestDoc(3)')
    assert page.evaluate('reads').count('doc3') == 1  # the cap applies only to speculation


def test_failed_prefetch_retries_and_lru_evicts(cached_reader: Reader) -> None:
    page = cached_reader.page
    page.evaluate("finishRead('doc1')")
    expect(page.locator('#rdBody h1')).to_have_text('doc1')
    page.evaluate("finishRead('doc2', true)")
    page.evaluate('openTestDoc(2)')
    assert page.evaluate('reads').count('doc2') == 2
    page.evaluate("finishRead('doc2')")
    expect(page.locator('#rdBody h1')).to_have_text('doc2')
    page.evaluate("""async () => {
      for (let i = 0; i < 13; i++) {
        const doc = {id: 'evict' + i, name: 'evict' + i, kind: 'report', mtime: i, size: 1};
        readerModule.openReader({host: 'home', job: {id: 'eviction', documents: [doc]}}, doc);
        finishRead(doc.id);
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      openTestDoc(1);
    }""")
    assert page.evaluate('reads').count('doc1') == 2


def test_library_cache_and_immutable_stored_copies(reader: Reader) -> None:
    page = reader.page
    page.evaluate("""async () => {
      const module = await import('/js/reader.js');
      const {libraryDocs} = await import('/js/library.js');
      const doc = libraryDocs.find(d => d.id === 'docs/rich.md');
      window.sourceReads = [];
      const originalFetch = window.fetch;
      window.fetch = (url, ...args) => {
        if (String(url).startsWith('/api/library/doc?')) {
          sourceReads.push(url);
          return new Promise(resolve => window.finishLibrary = () => resolve({ok: true,
            json: async () => ({name: doc.name, html: '<h1>Updated library</h1>', toc: []})}));
        }
        if (String(url).startsWith('/api/library/job?') || String(url).startsWith('/api/library/working?')) {
          sourceReads.push(url);
          return Promise.resolve({ok: true, json: async () => ({name: 'Stored copy', html: '<h1>Stored copy</h1>', toc: []})});
        }
        return originalFetch(url, ...args);
      };
      module.closeReader(); module.openLibraryReader(doc);
      window.libraryInstant = !!document.querySelector('#rdBody h1') && !document.querySelector('.rd-skel');
      module.closeReader(); module.openLibraryReader({...doc, mtime: doc.mtime + 1});
      window.libraryStale = document.querySelector('#rdMeta').textContent;
      window.libraryModule = module;
    }""")
    assert page.evaluate('libraryInstant') is True
    assert len(page.evaluate('sourceReads')) == 1
    assert 'updating live' in page.evaluate('libraryStale')
    page.evaluate('finishLibrary()')
    expect(page.locator('#rdBody h1')).to_have_text('Updated library')
    for endpoint in ('job', 'working'):
        page.evaluate("""async endpoint => {
          const url = '/api/library/' + endpoint + '?project=notes&id=copy';
          const doc = {id: 'copy', name: 'Stored copy', kind: 'report', mtime: 1, size: 1};
          libraryModule.openStoredReader(url, doc);
          await new Promise(resolve => setTimeout(resolve, 0));
          libraryModule.closeReader();
          libraryModule.openStoredReader(url, {...doc, mtime: 2, size: 2});
          window.storedInstant = document.querySelector('#rdBody h1')?.textContent === 'Stored copy'
            && !document.querySelector('.rd-skel');
        }""", endpoint)
        assert page.evaluate('storedInstant') is True
        assert len([url for url in page.evaluate('sourceReads') if f'/api/library/{endpoint}?' in url]) == 1


def test_follow_doc_catches_updates_during_pending_read(cached_reader: Reader) -> None:
    page = cached_reader.page
    page.evaluate("""() => {
      testJob.documents[1] = {...testJob.documents[1], mtime: 20, size: 101};
      readerModule.followDoc({host: 'home', job: testJob});
      finishRead('doc1');
    }""")
    expect(page.locator('#rdBody h1')).to_have_text('doc1')
    expect(page.locator('#rdMeta')).to_contain_text('updating live')
    assert page.evaluate('reads').count('doc1') == 2
    page.evaluate("""() => {
      testJob.documents[1] = {...testJob.documents[1], size: 102};
      readerModule.followDoc({host: 'home', job: testJob});
      finishRead('doc1');
    }""")
    page.wait_for_function("reads.filter(id => id === 'doc1').length === 3")
    page.evaluate("finishRead('doc1')")
    expect(page.locator('#rdMeta')).not_to_contain_text('updating live')
    page.evaluate('openTestDoc(1)')
    assert page.evaluate('reads').count('doc1') == 3
