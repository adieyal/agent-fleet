# Recogito browser distribution

Pinned `@recogito/text-annotator` 4.3.6, BSD-3-Clause. Browser assets are served locally; there is no CDN request. `provenance.json` records npm integrity, upstream commit, emitted SHA-256 hashes, license sources and bundled dependency versions from that commit's package lock.

Reproduce from the repository root:

```sh
python scripts/vendor_recogito.py
```

The script checks every downloaded tarball's integrity and every emitted file's expected checksum. It copies the published UMD and CSS, replacing the UMD's single `process.env.NODE_ENV` expression with the production string. The upstream UMD otherwise references Node's `process` when creating an annotator in a browser. This is a build-time environment definition; no runtime shim, build tool or remote module is needed.

The npm archive omits LICENSE. LICENSE is copied from its exact upstream Git commit. THIRD_PARTY_LICENSES.txt retains notices for dependencies included by the upstream bundle; the core package's omitted license also comes from its exact upstream commit. This is a distributed prebuilt bundle, not a claim that rebuilding the upstream source yields identical bytes.
