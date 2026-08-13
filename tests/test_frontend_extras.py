"""Agent-facing frontend behavior added in the comprehensive review: honest degraded modes,
diagnostic dedup with suggestions, run output remapping/truncation, search, staleness note."""

from __future__ import annotations

import os
import shutil

import pytest

from magma_lsp import frontend
from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.model import Intrinsic, MagmaDB
from magma_lsp.magma.validate import CheckResult

_HAS_MAGMA = shutil.which("magma") is not None or os.path.exists("/opt/magma/magma")
_HAS_DB = frontend.load_index() is not None
magma = pytest.mark.skipif(not _HAS_MAGMA, reason="requires a Magma install")
needs_db = pytest.mark.skipif(not _HAS_DB, reason="requires a built signature DB")


def test_check_inconclusive_on_timeout(monkeypatch):
    monkeypatch.setattr(
        frontend, "syntax_check", lambda *a, **k: CheckResult(diagnostics=[], timed_out=True)
    )
    out = frontend.check("x := 1;\n", index=None)
    assert not out.ok
    assert "INCONCLUSIVE" in out.report


def test_check_execute_timeout_is_inconclusive_not_fail(monkeypatch):
    """A long-running computation is not invalid code (codex #12 round 6)."""
    from magma_lsp.magma.diagnostics import MagmaDiagnostic

    monkeypatch.setattr(
        frontend, "syntax_check", lambda *a, **k: CheckResult(diagnostics=[], timed_out=False)
    )
    timeout_diag = MagmaDiagnostic(1, 1, "warning", "Magma check timed out", positionless=True)
    monkeypatch.setattr(
        frontend,
        "execution_check",
        lambda *a, **k: CheckResult(diagnostics=[timeout_diag], timed_out=True),
    )
    out = frontend.check("x := Factorial(10^9);\n", index=None, execute=True)
    assert not out.ok
    assert "INCONCLUSIVE" in out.report and "FAIL" not in out.report


def test_check_degrades_without_magma(monkeypatch):
    def raise_missing(*a, **k):
        raise FileNotFoundError("no magma")

    monkeypatch.setattr(frontend, "syntax_check", raise_missing)
    out = frontend.check("x := 1;\nprint x;\n", index=None)
    assert out.ok  # static-only clean
    assert "Magma not found" in out.report


@magma
@needs_db
def test_check_dedups_static_and_magma_undefined():
    out = frontend.check("print FactorInteger(12);\n")
    assert not out.ok
    # one merged diagnostic, not a static + Magma duplicate (source-echo lines don't count)
    lines = [
        ln
        for ln in out.report.splitlines()
        if "FactorInteger" in ln and ("not a known intrinsic" in ln or "not been declared" in ln)
    ]
    assert len(lines) == 1, out.report
    assert "did you mean" in lines[0]


@magma
def test_run_remaps_lines_and_sets_exit_code():
    res = frontend.run('print "before";\nx := 1/0;\n')
    assert "In your program, line 2" in res.output
    assert "magma-lsp-" not in res.output  # no temp path leak
    assert res.returncode != 0


@magma
def test_run_truncates_preserving_tail():
    res = frontend.run(
        "for i in [1..20000] do print i; end for;\nprint \"THE-END\";\n", max_output=4000
    )
    assert res.truncated
    assert "elided" in res.output
    assert "THE-END" in res.output  # the tail (results/errors) survives
    assert len(res.output) < 6000


@needs_db
def test_search_finds_by_keyword():
    res = frontend.search("integer factorization")
    assert res.n_hits > 0
    assert "Factorization" in res.text


@needs_db
def test_search_matches_camelcase_query():
    """Searching an exact CamelCase intrinsic name must hit — the query is split with the
    same camel tokenizer as the indexed names (codex #12 round 9)."""
    res = frontend.search("NumberOfPoints")
    assert res.n_hits > 0
    assert "NumberOfPoints" in res.text


@needs_db
def test_search_limit_clamped_at_lower_bound():
    """limit<=0 must not slice as scored[:-n] and dump the index (codex #12 round 5)."""
    for bad in (0, -1):
        res = frontend.search("group", limit=bad)
        assert 0 < res.n_hits <= 1, res.n_hits  # clamped to 1, not "everything"


@needs_db
def test_lookup_resolves_case_and_operators():
    res = frontend.lookup(["ellipticcurve"], handbook=False)
    assert res.all_found
    assert "resolved from" in res.text
    res = frontend.lookup(["#"], handbook=False)
    assert res.all_found


@needs_db
def test_lookup_suggests_on_miss():
    res = frontend.lookup(["FactorInteger"], handbook=False)
    assert not res.all_found
    assert "did you mean" in res.text
    assert "Factorization" in res.text


def test_staleness_note():
    installed = frontend.installed_magma_version()
    if installed is None:
        pytest.skip("no Magma install to compare against")
    idx = SignatureIndex(MagmaDB(version="0.0-0", intrinsics={"Foo": Intrinsic(name="Foo")}))
    note = frontend.staleness_note(idx)
    assert note is not None and "rebuild" in note
    idx_ok = SignatureIndex(MagmaDB(version=installed, intrinsics={}))
    assert frontend.staleness_note(idx_ok) is None


@magma
def test_check_execute_resolves_relative_loads(tmp_path):
    """The execution pass runs in the source file's directory, so a relative `load` that the
    static pass resolved does not then fail at execution time (codex #12 round 2 P1)."""
    (tmp_path / "helpers.m").write_text("function Helper(x) return x + 1; end function;\n")
    src = 'load "helpers.m";\nprint Helper(3);\n'
    out = frontend.check(src, execute=True, filename=str(tmp_path / "main.m"))
    assert out.ok, out.report


@magma
def test_run_resolves_relative_loads(tmp_path):
    (tmp_path / "helpers.m").write_text("function Helper(x) return x + 1; end function;\n")
    res = frontend.run('load "helpers.m";\nprint Helper(41);\n', filename=str(tmp_path / "m.m"))
    assert res.returncode == 0 and "42" in res.output, res.output


def test_check_arity_skips_loaded_redefinitions(tmp_path):
    """A loaded helper that shadows a DB intrinsic must not be arity-checked against the DB's
    overloads (codex #12 round 2)."""
    from magma_lsp.db.index import SignatureIndex
    from magma_lsp.db.model import Intrinsic, MagmaDB, Param, Signature

    one_arg = Signature(name="Weight", args=[Param("x", "RngIntElt")], returns=["RngIntElt"])
    db = MagmaDB(version="0", intrinsics={"Weight": Intrinsic("Weight", [one_arg])})
    idx = SignatureIndex(db)
    (tmp_path / "helpers.m").write_text("function Weight(a, b) return a + b; end function;\n")
    src = 'load "helpers.m";\nx := Weight(1, 2);\n'
    out = frontend.check(src, index=idx, filename=str(tmp_path / "main.m"))
    assert "no overload" not in out.report, out.report
    # control: without the load, the 2-arg call against the 1-arg intrinsic IS flagged
    out2 = frontend.check("x := Weight(1, 2);\n", index=idx)
    assert "no overload" in out2.report, out2.report


def test_sane_timeout():
    from magma_lsp.magma.runner import sane_timeout

    assert sane_timeout(5.0) == 5.0
    for bad in (-1.0, 0, float("nan"), float("inf"), "soon"):
        assert sane_timeout(bad, default=30.0) == 30.0


@magma
def test_check_negative_timeout_still_checks():
    """timeout=-1 became `timeout -1 magma ...` -> GNU timeout exit 125, Magma never ran, and
    the empty diagnostics read as a false OK (codex #12 round 8)."""
    out = frontend.check("x := 1/0;\nprint x;\n", timeout=-1.0, execute=True)
    assert not out.ok
    assert "zero denominator" in out.report


def test_truncate_output_helper():
    out, truncated = frontend._truncate_output("x" * 10, cap=100)
    assert not truncated and out == "x" * 10
    long = "\n".join(f"line{i}" for i in range(1000))
    out, truncated = frontend._truncate_output(long, cap=500)
    assert truncated and "elided" in out and out.endswith("line999")


def test_truncate_output_nonpositive_cap_still_truncates():
    """cap=0 must not invert the budget: out[-0:] is the WHOLE string (codex #12 round 4)."""
    long = "\n".join(f"line{i}" for i in range(1000))
    for cap in (0, -5):
        out, truncated = frontend._truncate_output(long, cap=cap)
        assert truncated and len(out) < 2 * frontend._MIN_OUTPUT_CAP + 100, len(out)
