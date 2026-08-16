"""Issue #15 (medium 4): a timed-out / crashed authoritative enumeration or probe must never
be folded into the DB as if complete — the build degrades loudly and records why."""

from __future__ import annotations

import pytest

from magma_lsp.db import build, listsig, probe
from magma_lsp.db.model import Param, Signature
from magma_lsp.magma.runner import MagmaResult


def _sig(name: str) -> Signature:
    return Signature(name=name, args=[Param(name="x", type="RngIntElt")], returns=["RngIntElt"])


def test_enumeration_rejects_timed_out_output(monkeypatch):
    # partial-but-plausible stdout with status 124: must raise, not return one signature
    def fake_run_source(src, **kw):
        if "ListCategories" in src:
            return MagmaResult("RngInt\n", 0, False)
        return MagmaResult("Foo(x::RngIntElt) -> RngIntElt\n", 124, True)

    monkeypatch.setattr(listsig, "run_source", fake_run_source)
    with pytest.raises(RuntimeError, match="timed out"):
        listsig.enumerate_signatures()


def test_enumeration_rejects_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        listsig, "run_source", lambda src, **kw: MagmaResult("RngInt\n", 2, False)
    )
    with pytest.raises(RuntimeError, match="exited 2"):
        listsig.enumerate_signatures()


def test_probe_rejects_timed_out_chunk(monkeypatch):
    monkeypatch.setattr(
        probe,
        "run_source",
        lambda src, **kw: MagmaResult("@@@Sprintf\nIntrinsic 'Sprintf'\n", 124, True),
    )
    with pytest.raises(RuntimeError, match="timed out"):
        probe.probe_names(["Sprintf"])


def test_build_records_kernel_enumeration_failure(tmp_path, monkeypatch):
    (tmp_path / "a.m").write_text(
        "intrinsic Foo(x::RngIntElt) -> RngIntElt\n{doc}\n  return x;\nend intrinsic;\n"
    )

    def boom(**kw):
        raise RuntimeError("ListSignatures enumeration timed out")

    monkeypatch.setattr(build, "enumerate_signatures", boom)
    monkeypatch.setattr(build, "find_magma", lambda p=None: "/fake/magma")
    db = build.build_db(package_root=str(tmp_path), probe_missing=False, harvest_docs=False)
    assert "Foo" in db.intrinsics  # package half still built
    assert "timed out" in db.stats["kernel_enumeration_failed"]


def test_build_records_incomplete_probe(tmp_path, monkeypatch):
    (tmp_path / "a.m").write_text(
        "intrinsic Foo(x::RngIntElt) -> RngIntElt\n{doc}\n  return Sprintf(\"%o\", x);\n"
        "end intrinsic;\n"
    )
    monkeypatch.setattr(build, "enumerate_signatures", lambda **kw: [_sig("Bar")])
    monkeypatch.setattr(build, "find_magma", lambda p=None: "/fake/magma")

    def probe_boom(names, **kw):
        raise RuntimeError("`name;` probe of a 1-name batch timed out")

    monkeypatch.setattr(build, "probe_names", probe_boom)
    db = build.build_db(package_root=str(tmp_path), harvest_docs=False)
    assert "timed out" in db.stats["probe_incomplete"]
    assert "Bar" in db.intrinsics
