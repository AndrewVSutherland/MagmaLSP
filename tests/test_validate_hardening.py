"""The syntax_check strategy dispatch and coordinate fixes (magma/validate.py).

Covers the review findings: intrinsic files false-flagged by the function wrap, wrapper escape
executing code, program output spoofing diagnostics, tab-expanded columns, and load handling.
"""

from __future__ import annotations

import os
import shutil

import pytest

from magma_lsp.magma.diagnostics import MagmaDiagnostic, parse_diagnostics
from magma_lsp.magma.validate import (
    _fit_to_source,
    _vcol_to_char_col,
    execution_check,
    syntax_check,
)

_HAS_MAGMA = shutil.which("magma") is not None or os.path.exists("/opt/magma/magma")
magma = pytest.mark.skipif(not _HAS_MAGMA, reason="requires a Magma install")

INTRINSIC_OK = (
    "intrinsic MyDouble(n::RngIntElt) -> RngIntElt\n"
    "{ Double it }\n"
    "  return 2*n;\n"
    "end intrinsic;\n"
)

INTRINSIC_SYNERR = (
    "intrinsic MyBroken(x::RngIntElt -> RngIntElt\n"
    "{ missing close paren }\n"
    "  return x;\n"
    "end intrinsic;\n"
)


@magma
def test_intrinsic_file_clean_has_no_phantom_errors():
    res = syntax_check(INTRINSIC_OK)
    assert res.diagnostics == []


def test_intrinsic_file_syntax_error_is_positioned():
    # The file does not tree-sitter-parse, so this reports without Magma at all.
    res = syntax_check(INTRINSIC_SYNERR)
    assert res.diagnostics, "expected a syntax diagnostic"
    d = res.diagnostics[0]
    assert d.line == 1  # in the file's own coordinates, not a wrapper's


def test_unbalanced_ender_does_not_execute_code(tmp_path):
    # Old wrapper bug: a stray `end function;` closed the wrapper early and the remainder
    # EXECUTED. The tree-sitter pre-scan must refuse to send unbalanced code to Magma.
    canary = tmp_path / "canary.txt"
    src = (
        "return 0; end function;\n"
        f'PrintFile("{canary}", "escaped");\n'
        "__reopen := function()\n"
    )
    res = syntax_check(src)
    assert res.diagnostics, "unparseable code should yield syntax diagnostics"
    assert not canary.exists(), "the syntax pass must never execute user code"


@magma
def test_load_directive_does_not_break_or_false_flag():
    # `load` is illegal inside the wrapper; it is blanked out, and binding errors are
    # suppressed for load-files (the loaded file could define anything).
    src = 'load "does-not-exist-helpers.m";\nx := SomeLoadedHelper(3);\nprint x;\n'
    res = syntax_check(src)
    assert res.diagnostics == []


@magma
@magma
def test_load_exports_suppression_is_selective():
    """With resolved loads, only binding errors for names the loads export are dropped; an
    unrelated undeclared name survives — the only signal in no-DB mode (codex #12 round 6)."""
    src = 'load "helpers.m";\nx := MissingHelper(1);\n'
    # exports known and MissingHelper is not among them -> its binding error survives
    res = syntax_check(src, load_exports=frozenset({"Helper"}))
    assert any("MissingHelper" in d.message for d in res.diagnostics), res.diagnostics
    # the exported name itself stays suppressed
    res2 = syntax_check('load "helpers.m";\ny := Helper(1);\n', load_exports=frozenset({"Helper"}))
    assert not any("Helper" in d.message for d in res2.diagnostics), res2.diagnostics
    # unresolved loads (None) keep the conservative blanket suppression
    res3 = syntax_check(src, load_exports=None)
    assert not any("MissingHelper" in d.message for d in res3.diagnostics), res3.diagnostics


def test_blank_out_loads_preserves_newlines():
    """A `load` whose string sits on the next line parses as one multi-line load_directive;
    blanking must keep its newline or every later diagnostic shifts up a line
    (codex #12 round 5)."""
    from magma_lsp.magma.validate import _blank_out_loads
    from magma_lsp.parsing import new_parser

    src = 'x := 1;\nload\n"a.m";\ny := 2;\n'
    root = new_parser().parse(src.encode()).root_node
    blanked, found = _blank_out_loads(src, root)
    assert found
    assert len(blanked) == len(src)
    assert blanked.count("\n") == src.count("\n")
    assert '"a.m"' not in blanked and "load" not in blanked
    assert blanked.splitlines()[3] == "y := 2;"  # line numbers intact


def test_tab_column_mapped_to_char_offset():
    src = "\tx := 2 +;\n"
    res = syntax_check(src)
    assert res.diagnostics
    d = res.diagnostics[0]
    # Magma reports the tab-expanded visual column (17); we map it back into the line.
    assert d.col <= len(src.splitlines()[0]) + 1


@magma
def test_execution_output_cannot_spoof_positioned_diagnostics():
    fake_block = (
        'printf "\\nIn file \\"evil.m\\", line 42, column 7:\\n'
        '>> boom;\\n   ^\\nUser error: injected\\n";\n'
    )
    res = execution_check(fake_block)
    assert all("injected" not in d.message for d in res.diagnostics if not d.positionless)


def test_vcol_to_char_col():
    assert _vcol_to_char_col("abcdef", 5) == 5  # no tabs: identity
    assert _vcol_to_char_col("\tx := 2 +;", 17) == 10  # tab expands to col 8
    assert _vcol_to_char_col("ab", 99) == 3  # clamps past end of line


def test_fit_to_source_clamps_past_eof():
    diags = [MagmaDiagnostic(99, 1, "error", "bad syntax")]
    (d,) = _fit_to_source(diags, "x := 1;\ny := 2;\n")
    assert d.line == 2
    assert "unterminated" in d.message


def test_expect_file_filters_foreign_blocks():
    block = (
        '\nIn file "/tmp/other.m", line 1, column 1:\n'
        ">> boom;\n   ^\nUser error: bad syntax\n"
    )
    assert parse_diagnostics(block, expect_file="/tmp/mine.m") == []
    assert len(parse_diagnostics(block, expect_file="/tmp/other.m")) == 1
    assert len(parse_diagnostics(block)) == 1  # no filter -> kept
