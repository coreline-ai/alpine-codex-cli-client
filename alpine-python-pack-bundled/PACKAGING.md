# Alpine Python package pack input

This module never downloads a runtime package. Supply an already-local production pack through
`ALPINE_PYTHON_PACKAGE_DIR`, or place it at `src/main/python-pack`.

The in-tree default directory is Git-ignored and must remain untracked. Prefer the environment
variable in release automation so production package bytes stay outside the repository checkout.

The input directory must contain exactly:

```text
python-pack.lock.json
sbom.spdx.json
packages/*.apk
```

The versioned lock schema and all integrity rules are implemented by
`scripts/python_package_pack.py`. A public release requires `production: true`, exact SHA-256 and
size coverage, an `aarch64` package set containing `python3`, and an SPDX 2.3 SBOM. Individual
package metadata may be `aarch64` or Alpine `noarch`; foreign executable architectures are rejected.
Test fixtures are rejected from production assets. If the directory is absent, normal source
verification can continue, but release packaging fails closed.
