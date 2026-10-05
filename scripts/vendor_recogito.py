"""Reproduce the pinned Recogito browser assets, with integrity and license checks."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'packages/fleet-web/src/fleet_web/static/vendor/recogito'
META = json.loads((TARGET / 'provenance.json').read_text())


def fetch(url):
    with urlopen(url, timeout=30) as response:
        return response.read()


def package(url, integrity):
    data = fetch(url)
    algorithm, expected = integrity.split('-', 1)
    assert base64.b64encode(hashlib.new(algorithm, data).digest()).decode() == expected, url
    return tarfile.open(fileobj=io.BytesIO(data), mode='r:gz')


with package(META['tarball'], META['integrity']) as archive:
    raw = archive.extractfile('package/dist/text-annotator.umd.js').read()
    assert hashlib.sha256(raw).hexdigest() == META['upstream_js_sha256']
    assert raw.count(b'process.env.NODE_ENV') == 1
    files = {'text-annotator.umd.js': raw.replace(b'process.env.NODE_ENV', b'"production"'),
             'text-annotator.css': archive.extractfile('package/dist/text-annotator.css').read(),
             'LICENSE': fetch(META['license_sources']['LICENSE'])}

notices = []
for name, info in META['bundled'].items():
    with package(info['resolved'], info['integrity']) as archive:
        licenses = [archive.extractfile(member).read().decode() for member in archive.getmembers()
                    if member.isfile() and member.name.split('/')[-1].lower() in ('license', 'license.md', 'license.txt')]
    if name == '@annotorious/core':
        license_body = fetch(META['license_sources'][name])
        assert hashlib.sha256(license_body).hexdigest() == META['core_license_sha256']
        licenses = [license_body.decode()]
    assert licenses, f'No license for {name}'
    notices.append(f"Package: {name} {info['version']}\nSource: {info['resolved']}\n\n" + '\n'.join(licenses))
files['THIRD_PARTY_LICENSES.txt'] = '\n\n'.join(notices).encode()
for name, body in files.items():
    assert hashlib.sha256(body).hexdigest() == META['sha256'][name], name
    (TARGET / name).write_bytes(body)
print(f"Verified and wrote {META['package']} {META['version']}: {', '.join(files)}")
