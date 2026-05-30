"""Integration guards against the real Magma install (skipped when it's absent).

These are fast subsets of the validation/ harnesses, kept as regression tests so the spec parser
and extractor stay correct against the shipped package tree.
"""

from __future__ import annotations

import os
import random

import pytest

from magma_lsp.db.package import extract_file
from magma_lsp.db.spec import attached_files

PKG_ROOT = "/opt/magma/package"
SPEC = os.path.join(PKG_ROOT, "spec")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(SPEC), reason="Magma package tree not present"
)


def test_spec_resolves_only_existing_files():
    files = attached_files(SPEC)
    assert len(files) > 2000  # the default spec attaches ~3000 files
    missing = [f for f in files if not os.path.isfile(f)]
    assert missing == [], f"spec resolved {len(missing)} non-existent files, e.g. {missing[:3]}"


def test_extractor_robust_on_attached_sample():
    files = sorted(attached_files(SPEC))
    random.seed(7)
    sample = random.sample(files, min(200, len(files)))
    total = 0
    for path in sample:
        sigs = extract_file(path)  # must not raise
        total += len(sigs)
        for s in sigs:
            # names are identifiers or quoted operators, never comments/garbage
            assert s.name and not s.name.startswith("//"), f"bad name {s.name!r} in {path}"
    assert total > 0
