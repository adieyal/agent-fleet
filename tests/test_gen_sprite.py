"""art/scripts/gen_sprite.py: request building, SSE parsing against a recorded ChatMock stream,
and the output cache. No network."""

import base64
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).parent.parent
FIXTURE = Path(__file__).parent / 'fixtures' / 'chatmock_image.sse'

_spec = importlib.util.spec_from_file_location('gen_sprite', ROOT / 'art' / 'scripts' / 'gen_sprite.py')
gs = sys.modules['gen_sprite'] = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)


def recorded() -> list[bytes]:
    return FIXTURE.read_bytes().splitlines(keepends=True)


@pytest.fixture
def ref_png(tmp_path: Path) -> Path:
    p = tmp_path / 'concept.png'
    im = Image.new('RGB', (40, 30), (10, 20, 30))
    im.paste((250, 0, 0), (5, 6, 15, 16))
    im.save(p)
    return p


def test_ref_spec_parses_path_and_crop() -> None:
    assert gs.Ref.parse('a/b.png') == gs.Ref(Path('a/b.png'))
    r = gs.Ref.parse('a/b.png@5,6,10,10')
    assert r.crop == (5, 6, 10, 10) and r.spec() == 'a/b.png@5,6,10,10'
    with pytest.raises(gs.GenError):
        gs.Ref.parse('a.png@1,2,3')


def test_crop_reads_the_named_box(ref_png: Path) -> None:
    im = Image.open(io.BytesIO(gs.Ref.parse(f'{ref_png}@5,6,10,10').png()))
    assert im.size == (10, 10)
    assert im.getpixel((0, 0)) == (250, 0, 0)


def test_request_carries_prompt_refs_in_order_and_the_transparent_tool(ref_png: Path) -> None:
    refs = [gs.Ref.parse(f'{ref_png}@5,6,10,10'), gs.Ref.parse(str(ref_png))]
    body = gs.build_request('a bench', [(r, r.png()) for r in refs], model='m', quality='low')
    assert body['model'] == 'm' and body['stream'] is True
    assert body['tools'] == [{'type': 'image_generation', 'background': 'transparent',
                              'output_format': 'png', 'quality': 'low'}]
    assert body['tool_choice'] == {'type': 'image_generation'}
    text, *images = body['input'][0]['content']
    assert text['type'] == 'input_text' and 'a bench' in text['text'] and 'transparent' in text['text']
    sizes = [Image.open(io.BytesIO(base64.b64decode(i['image_url'].split(',', 1)[1]))).size for i in images]
    assert sizes == [(10, 10), (40, 30)]


def test_textures_can_ask_for_an_opaque_background() -> None:
    assert gs.build_request('floor', [], background='opaque')['tools'][0]['background'] == 'opaque'


def test_same_arguments_build_the_same_request(ref_png: Path) -> None:
    r = gs.Ref.parse(f'{ref_png}@5,6,10,10')
    a = gs.build_request('x', [(r, r.png())])
    b = gs.build_request('x', [(r, r.png())])
    assert gs.request_digest(a) == gs.request_digest(b)
    assert gs.request_digest(a) != gs.request_digest(gs.build_request('y', [(r, r.png())]))


def test_recorded_stream_yields_the_image_and_metadata() -> None:
    got = gs.parse_stream(recorded())
    assert Image.open(io.BytesIO(got.png)).size == (4, 3)
    assert got.image_model == 'gpt-image-2-codex'
    assert got.model == 'gpt-5.5'
    assert got.response_id.startswith('resp_')
    assert got.revised_prompt and got.size == '4x3'


def test_stream_without_an_image_is_an_error() -> None:
    lines = [ln for ln in recorded() if b'image_generation_call"' not in ln or b'output_item.done' not in ln]
    with pytest.raises(gs.GenError, match='without an image'):
        gs.parse_stream(lines)


def test_failed_response_is_an_error() -> None:
    evt = {'type': 'response.failed', 'response': {'error': {'message': 'moderation'}}}
    with pytest.raises(gs.GenError, match='moderation'):
        gs.parse_stream([b'event: response.failed\n', f'data: {json.dumps(evt)}\n'.encode()])


def test_generate_writes_png_and_sidecar_then_keeps_the_cache(tmp_path: Path, ref_png: Path) -> None:
    calls = []

    def fake(url: str, body: dict):
        calls.append(body)
        return gs.parse_stream(recorded())

    out = tmp_path / 'b2' / 'bench.png'
    refs = [gs.Ref.parse(f'{ref_png}@5,6,10,10')]
    meta = gs.generate('a bench', refs, out, transport=fake)
    assert out.exists() and len(calls) == 1
    side = json.loads(out.with_suffix('.json').read_text())
    assert side == meta
    assert side['description'] == 'a bench' and 'a bench' in side['prompt']
    assert side['references'][0]['path'].endswith('@5,6,10,10')
    assert side['references'][0]['sha256'] == hashlib.sha256(refs[0].png()).hexdigest()
    assert side['model'] == 'gpt-5.5' and side['image_model'] == 'gpt-image-2-codex'
    assert side['timestamp'].endswith('Z')

    assert gs.generate('a bench', refs, out, transport=fake) is None
    assert gs.generate('something else', refs, out, transport=fake) is None
    assert len(calls) == 1

    assert gs.generate('a bench', refs, out, transport=fake, force=True) is not None
    assert len(calls) == 2
