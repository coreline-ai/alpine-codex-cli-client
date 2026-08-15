# Alpine Python package pack input

This module never downloads a runtime package. Supply an already-local production pack through
`ALPINE_PYTHON_PACKAGE_DIR`, or place it at `src/main/python-pack`.

The input directory must contain exactly:

```text
python-pack.lock.json
sbom.spdx.json
packages/*.apk
```

The versioned lock schema and all integrity rules are implemented by
`scripts/python_package_pack.py`. A public release requires `production: true`, exact SHA-256 and
size coverage, an `aarch64` package set containing `python3`, and an SPDX 2.3 SBOM. Test fixtures
are rejected from production assets. If the directory is absent, normal source verification can
continue, but release packaging fails closed.
