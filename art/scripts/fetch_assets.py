#!/usr/bin/env python3
"""Download the pinned CC0 sources into art/sources/.

Default: fetch exactly what art/assets.lock.json pins and verify every sha256.
--resolve: ask the providers (Poly Haven, ambientCG) for the files named in
art/assets.json, check them against the provider's own hash or size, and
rewrite the lock and art/CREDITS.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ART = Path(__file__).resolve().parent.parent
SOURCES = ART / 'sources'
MANIFEST = ART / 'assets.json'
LOCK = ART / 'assets.lock.json'
CREDITS = ART / 'CREDITS.md'
UA = 'fleet-art-fetch/1 (+https://github.com/; agent-fleet asset pipeline)'
LICENSES = {'polyhaven': 'CC0-1.0', 'ambientcg': 'CC0-1.0'}


class FetchError(RuntimeError):
    pass


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def download(url: str, dest: Path) -> tuple[str, str, int]:
    """Stream url to dest; return (sha256, md5, size)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    tmp = dest.with_suffix(dest.suffix + '.part')
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, 'wb') as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    tmp.replace(dest)
    return sha.hexdigest(), md5.hexdigest(), size


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# --- resolving: provider APIs -> lock entries -------------------------------

def resolve_polyhaven(a: dict) -> dict:
    files = get_json(f"https://api.polyhaven.com/files/{a['id']}")
    info = get_json(f"https://api.polyhaven.com/info/{a['id']}")
    if a['type'] == 'hdri':
        entry = files['hdri'][a['res']][a['format']]
        wanted = {Path(entry['url']).name: entry}
    else:
        entry = files['gltf'][a['res']]['gltf']
        wanted = {Path(entry['url']).name: entry, **entry.get('include', {})}
    out = []
    for rel, f in sorted(wanted.items()):
        dest = SOURCES / 'polyhaven' / a['id'] / rel
        sha, md5, size = download(f['url'], dest)
        if md5 != f['md5']:
            raise FetchError(f"{a['id']}/{rel}: md5 {md5} != Poly Haven's {f['md5']}")
        out.append({'path': str(dest.relative_to(SOURCES)), 'url': f['url'], 'sha256': sha, 'size': size})
    return {'id': a['id'], 'source': 'polyhaven', 'license': LICENSES['polyhaven'],
            'page': f"https://polyhaven.com/a/{a['id']}", 'title': info['name'],
            'authors': sorted(info.get('authors', {})), 'files': out}


def resolve_ambientcg(a: dict) -> dict:
    data = get_json(f"https://ambientcg.com/api/v2/full_json?id={a['id']}&include=downloadData,displayData")
    asset = data['foundAssets'][0]
    downloads = asset['downloadFolders']['default']['downloadFiletypeCategories']['zip']['downloads']
    d = next(x for x in downloads if x['attribute'] == a['res'])
    dest = SOURCES / 'archives' / d['fileName']
    sha, _, size = download(d['downloadLink'], dest)
    if size != d['size']:
        raise FetchError(f"{a['id']}: {size} bytes != ambientCG's {d['size']}")
    return {'id': a['id'], 'source': 'ambientcg', 'license': LICENSES['ambientcg'],
            'page': f"https://ambientcg.com/view?id={a['id']}", 'title': asset.get('displayName', a['id']),
            'authors': ['ambientCG'],
            'files': [{'path': str(dest.relative_to(SOURCES)), 'url': d['downloadLink'], 'sha256': sha, 'size': size}]}


def resolve() -> dict:
    resolvers = {'polyhaven': resolve_polyhaven, 'ambientcg': resolve_ambientcg}
    lock = {'assets': []}
    for a in json.loads(MANIFEST.read_text())['assets']:
        print(f"resolve {a['source']}:{a['id']}")
        lock['assets'].append(resolvers[a['source']](a))
    LOCK.write_text(json.dumps(lock, indent=2) + '\n')
    return lock


# --- locked fetch -------------------------------------------------------------

def fetch_locked(lock: dict) -> None:
    locked = {a['id'] for a in lock['assets']}
    missing = [a['id'] for a in json.loads(MANIFEST.read_text())['assets'] if a['id'] not in locked]
    if missing:
        raise FetchError(f"not in {LOCK.name}: {', '.join(missing)}; run with --resolve")
    for entry in lock['assets']:
        for f in entry['files']:
            dest = SOURCES / f['path']
            if dest.exists() and sha256_of(dest) == f['sha256']:
                continue
            print(f"fetch {f['path']}")
            sha, _, _ = download(f['url'], dest)
            if sha != f['sha256']:
                dest.unlink()
                raise FetchError(f"{f['path']}: sha256 {sha} != locked {f['sha256']}")


def unpack(lock: dict) -> None:
    manifest = {a['id']: a for a in json.loads(MANIFEST.read_text())['assets']}
    for entry in lock['assets']:
        if entry['source'] != 'ambientcg':
            continue
        maps = manifest[entry['id']]['maps']
        out = SOURCES / 'ambientcg' / entry['id']
        with zipfile.ZipFile(SOURCES / entry['files'][0]['path']) as z:
            for name in z.namelist():
                if any(name.endswith(f'_{m}.jpg') for m in maps) and not (out / name).exists():
                    z.extract(name, out)


def write_credits(lock: dict) -> None:
    rows = ['# Art sources', '',
            'Written by `art/scripts/fetch_assets.py --resolve` from `art/assets.lock.json`, which lists every '
            'downloaded file with its URL and sha256. Downloaded files live in `art/sources/` and are never committed.',
            '', '| Asset | Source | Authors | Licence | Download |', '|---|---|---|---|---|']
    for a in lock['assets']:
        main = next((f for f in a['files'] if f['path'].endswith(('.gltf', '.hdr', '.zip'))), a['files'][0])
        rows.append(f"| [{a['title']}]({a['page']}) | {a['source']} | {', '.join(a['authors'])} "
                    f"| {a['license']} | {main['url']} |")
    rows += ['', '## Sources already in the repository', '', '| Asset | Path | Authors | Licence | Used for |',
             '|---|---|---|---|---|']
    for a in json.loads(MANIFEST.read_text()).get('local', []):
        rows.append(f"| [{a['title']}]({a['url']}) | `{a['path']}` | {', '.join(a['authors'])} | {a['license']} "
                    f"| {a['use']} |")
    CREDITS.write_text('\n'.join(rows) + '\n')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--resolve', action='store_true', help='re-resolve from provider APIs and rewrite the lock')
    args = p.parse_args()
    try:
        if args.resolve:
            lock = resolve()
            write_credits(lock)
        else:
            if not LOCK.exists():
                raise FetchError(f'{LOCK.name} missing; run with --resolve')
            lock = json.loads(LOCK.read_text())
            fetch_locked(lock)
        unpack(lock)
    except FetchError as e:
        print(f'fetch_assets: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
