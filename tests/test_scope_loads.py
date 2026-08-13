"""`load` directive resolution and call-site argument counting in analysis/scope.py."""

from __future__ import annotations

from magma_lsp.analysis.scope import analyze, load_defined_symbols, load_targets


def test_load_targets_single():
    assert load_targets('load "a.m";\n') == ["a.m"]


def test_load_targets_multiple():
    src = 'load "a.m";\nload "sub/b.m";\nx := 1;\n'
    assert set(load_targets(src)) == {"a.m", "sub/b.m"}


def test_load_targets_none():
    assert load_targets("x := 1;\n") == []
    assert load_targets("") == []


def test_load_defined_symbols_resolves_relative_path(tmp_path):
    (tmp_path / "helper.m").write_text("Helper := function(n) return n; end function;\n")
    src = 'load "helper.m";\nprint Helper(3);\n'
    names, unresolved = load_defined_symbols(src, str(tmp_path))
    assert "Helper" in names
    assert unresolved == 0


def test_load_defined_symbols_counts_missing_file(tmp_path):
    names, unresolved = load_defined_symbols('load "missing.m";\n', str(tmp_path))
    assert unresolved == 1
    assert names == set()


def test_load_defined_symbols_transitive(tmp_path):
    """a.m load-s b.m; b.m's symbols reach the top-level document. Nested relative targets
    resolve against the SAME base dir (Magma resolves loads against the process cwd — verified
    on 2.29-9), so b.m's path here is base-relative even though the load appears in sub/a.m."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.m").write_text(
        'load "sub/b.m";\nfunction FromA(x) return x; end function;\n'
    )
    (tmp_path / "sub" / "b.m").write_text("function FromB(x) return x; end function;\n")
    names, unresolved = load_defined_symbols('load "sub/a.m";\n', str(tmp_path))
    assert unresolved == 0
    assert {"FromA", "FromB"} <= names


def test_load_defined_symbols_transitive_missing_counts_unresolved(tmp_path):
    (tmp_path / "a.m").write_text('load "nowhere.m";\nfunction FromA(x) return x; end function;\n')
    names, unresolved = load_defined_symbols('load "a.m";\n', str(tmp_path))
    assert "FromA" in names
    assert unresolved == 1  # the nested miss disables name checking, not silently ignored


def test_load_defined_symbols_cycle_terminates(tmp_path):
    (tmp_path / "a.m").write_text('load "b.m";\nfunction FromA(x) return x; end function;\n')
    (tmp_path / "b.m").write_text('load "a.m";\nfunction FromB(x) return x; end function;\n')
    names, unresolved = load_defined_symbols('load "a.m";\n', str(tmp_path))
    assert unresolved == 0
    assert {"FromA", "FromB"} <= names


def test_call_args_exclude_optional_parameters():
    _, calls = analyze("Foo(a, b : Opt := 1);\n")
    (cs,) = calls
    assert cs.name == "Foo"
    assert cs.n_args == 2  # `: Opt := 1` is not a positional argument
    assert not cs.has_ref_arg


def test_call_with_no_args():
    _, calls = analyze("Bar();\n")
    (cs,) = calls
    assert cs.n_args == 0
    assert not cs.has_ref_arg


def test_call_with_ref_arg():
    _, calls = analyze("Baz(~x, y);\n")
    (cs,) = calls
    assert cs.name == "Baz"
    assert cs.n_args == 2
    assert cs.has_ref_arg
