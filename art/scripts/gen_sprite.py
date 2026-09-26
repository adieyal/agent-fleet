#!/usr/bin/env python3
"""Generate one transparent sprite with ChatMock's image_generation tool.

    gen_sprite.py "a potted plant in a square concrete planter" -o art/bakeoff/B2/plant.png \
        --ref "docs/images/concept/l2.png@1500,470,120,260"

Each --ref is an image path, optionally cropped with @x,y,w,h (pixels). The request is
built only from the arguments (fixed instructions, style text and tool settings), so the
same arguments send the same request; the image model itself has no seed. The PNG is
written with a JSON sidecar (<out>.json) recording prompt, references, models and time.
An existing output is never regenerated unless --force.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = 'http://127.0.0.1:8010'
DEFAULT_MODEL = 'gpt-5.5'
INSTRUCTIONS = ('You are a sprite generator. Always answer by calling the image_generation tool '
                'exactly once. Do not reply with text.')
STYLE = ('Style: match the attached reference crops from the concept art exactly. Soft, clean, '
         'slightly stylised 3D render of an isometric office, three-quarter view from above at the '
         "same camera angle as the references (about 30 degrees down, the object's long axis running "
         'from lower left to upper right). Warm soft key light from the upper left, gentle ambient '
         'occlusion, no harsh shadows. Pale, desaturated palette. Render only the object, centred, '
         'whole and uncropped, on a fully transparent background: no floor, no ground shadow, no '
         'backdrop, no text, no border.')


class GenError(RuntimeError):
    pass


@dataclass(frozen=True)
class Ref:
    path: Path
    crop: tuple[int, int, int, int] | None = None

    @classmethod
    def parse(cls, spec: str) -> Ref:
        path, _, box = spec.partition('@')
        if not box:
            return cls(Path(path))
        parts = [int(p) for p in box.split(',')]
        if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
            raise GenError(f'bad crop {box!r}: want x,y,w,h')
        return cls(Path(path), (parts[0], parts[1], parts[2], parts[3]))

    def spec(self) -> str:
        return f'{self.path}@{",".join(map(str, self.crop))}' if self.crop else str(self.path)

    def png(self) -> bytes:
        """The reference as PNG bytes, cropped if asked."""
        if self.crop is None and self.path.suffix.lower() == '.png':
            return self.path.read_bytes()
        from PIL import Image  # dev dependency; only needed to crop or convert
        x, y, w, h = self.crop or (0, 0, 0, 0)
        with Image.open(self.path) as im:
            if self.crop:
                im = im.crop((x, y, x + w, y + h))
            buf = io.BytesIO()
            im.save(buf, 'PNG')
        return buf.getvalue()


def build_prompt(description: str, style: str = STYLE) -> str:
    return f'Generate a single game sprite: {description.strip()}\n\n{style}'


def build_request(description: str, refs: list[tuple[Ref, bytes]], *, model: str = DEFAULT_MODEL,
                  quality: str = 'high', style: str = STYLE) -> dict:
    """The /v1/responses body: prompt text, then each reference as an input image."""
    content: list[dict] = [{'type': 'input_text', 'text': build_prompt(description, style)}]
    for _, data in refs:
        content.append({'type': 'input_image',
                        'image_url': 'data:image/png;base64,' + base64.b64encode(data).decode()})
    return {
        'model': model,
        'stream': True,
        'instructions': INSTRUCTIONS,
        'input': [{'type': 'message', 'role': 'user', 'content': content}],
        'tools': [{'type': 'image_generation', 'background': 'transparent',
                   'output_format': 'png', 'quality': quality}],
        'tool_choice': {'type': 'image_generation'},
    }


def request_digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def sse_events(lines: Iterable[bytes | str]) -> Iterator[dict]:
    """JSON payloads of `data:` lines; stops at [DONE]."""
    for raw in lines:
        line = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
        line = line.rstrip('\r\n')
        if not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if data == '[DONE]':
            return
        if data:
            yield json.loads(data)


@dataclass
class Generated:
    png: bytes
    revised_prompt: str | None
    size: str | None
    quality: str | None
    response_id: str | None
    model: str | None
    image_model: str | None


def parse_stream(lines: Iterable[bytes | str]) -> Generated:
    """The generated image and its metadata from a /v1/responses event stream."""
    item: dict | None = None
    response: dict = {}
    for evt in sse_events(lines):
        kind = evt.get('type')
        if kind in ('response.failed', 'error'):
            err = (evt.get('response') or {}).get('error') or evt.get('error') or evt
            raise GenError(f'generation failed: {err}')
        if kind == 'response.output_item.done' and evt.get('item', {}).get('type') == 'image_generation_call':
            item = evt['item']
        if isinstance(evt.get('response'), dict):
            response = evt['response']
    if item is None or not item.get('result'):
        raise GenError('stream ended without an image (no completed image_generation_call)')
    tools = [t for t in response.get('tools') or [] if t.get('type') == 'image_generation']
    return Generated(
        png=base64.b64decode(item['result']),
        revised_prompt=item.get('revised_prompt'),
        size=item.get('size'),
        quality=item.get('quality'),
        response_id=response.get('id'),
        model=response.get('model'),
        image_model=tools[0].get('model') if tools else None,
    )


def post_stream(url: str, body: dict, timeout: float = 600) -> Generated:
    req = urllib.request.Request(url.rstrip('/') + '/v1/responses', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_stream(r)
    except urllib.error.HTTPError as e:
        raise GenError(f'HTTP {e.code}: {e.read()[:500].decode("utf-8", "replace")}') from e


def sidecar_path(out: Path) -> Path:
    return out.with_suffix('.json')


def generate(description: str, refs: list[Ref], out: Path, *, url: str = DEFAULT_URL,
             model: str = DEFAULT_MODEL, quality: str = 'high', style: str = STYLE,
             force: bool = False, transport=post_stream) -> dict | None:
    """Write out and its sidecar; return the sidecar, or None when a cached output was kept."""
    loaded = [(r, r.png()) for r in refs]
    body = build_request(description, loaded, model=model, quality=quality, style=style)
    digest = request_digest(body)
    if out.exists() and not force:
        meta = sidecar_path(out)
        old = json.loads(meta.read_text()).get('request_sha256') if meta.exists() else None
        note = '' if old == digest else ' (request differs from the one that made it; --force to redo)'
        print(f'cached: {out}{note}', file=sys.stderr)
        return None
    started = time.time()
    got = transport(url, body)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(got.png)
    meta = {
        'description': description,
        'prompt': build_prompt(description, style),
        'instructions': INSTRUCTIONS,
        'references': [{'path': r.spec(), 'sha256': hashlib.sha256(d).hexdigest()} for r, d in loaded],
        'model': got.model or model,
        'image_model': got.image_model,
        'quality_requested': quality,
        'quality': got.quality,
        'size': got.size,
        'revised_prompt': got.revised_prompt,
        'response_id': got.response_id,
        'request_sha256': digest,
        'output_sha256': hashlib.sha256(got.png).hexdigest(),
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'seconds': round(time.time() - started, 1),
    }
    sidecar_path(out).write_text(json.dumps(meta, indent=2) + '\n')
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('description', help='what to draw, e.g. "a potted plant in a square concrete planter"')
    ap.add_argument('-o', '--out', type=Path, required=True, help='output PNG; sidecar goes to <out>.json')
    ap.add_argument('--ref', action='append', default=[], metavar='PATH[@x,y,w,h]',
                    help='reference image, optionally cropped; repeatable, order is kept')
    ap.add_argument('--style', help='replace the built-in style text')
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--quality', default='high', choices=['low', 'medium', 'high', 'auto'])
    ap.add_argument('--url', default=DEFAULT_URL, help='ChatMock base URL')
    ap.add_argument('--force', action='store_true', help='regenerate even if the output exists')
    args = ap.parse_args(argv)
    try:
        refs = [Ref.parse(s) for s in args.ref]
        meta = generate(args.description, refs, args.out, url=args.url, model=args.model,
                        quality=args.quality, style=args.style or STYLE, force=args.force)
    except (GenError, OSError, ValueError) as e:
        print(f'gen_sprite: {e}', file=sys.stderr)
        return 1
    if meta:
        print(f'{args.out} {meta["size"]} {meta["seconds"]}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
