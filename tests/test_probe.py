"""Parsing of the `name;` probe output that recovers variadic intrinsics."""

from __future__ import annotations

from magma_lsp.db.probe import build_probe_script, parse_probe_output

# Real captured output shape (Explode is variadic; Foo123 is not an intrinsic).
SAMPLE = """@@@Explode
Intrinsic 'Explode'

Signatures:

(x::SeqEnum) -> ., ...
(x::Tup) -> ., ...

The explosion of x; i.e., the elements of x as a list of expressions

@@@Sprintf
Intrinsic 'Sprintf'

Signatures:

(S::MonStgElt, ...) -> MonStgElt

The string resulting from the formatted printing of S and the other arguments

@@@Foo123
"""


def test_build_script_uses_eval_wrapper():
    # eval(...) is essential: a bare `delete;` would abort the whole batch at a reserved word.
    script = build_probe_script(["Explode", "delete"])
    assert 'eval("Explode;")' in script
    assert 'eval("delete;")' in script
    assert "try" in script and "catch" in script


def test_parse_recovers_variadic_and_skips_non_intrinsic():
    res = parse_probe_output(SAMPLE)
    assert set(res) == {"Explode", "Sprintf"}  # Foo123 (no "Intrinsic '") skipped

    explode = res["Explode"]
    assert len(explode) == 2
    assert explode[0].render() == "Explode(x::SeqEnum) -> ., ..."
    assert explode[0].doc.startswith("The explosion of x")
    assert all(s.kind == "kernel" for s in explode)

    (sprintf,) = res["Sprintf"]
    assert sprintf.render() == "Sprintf(S::MonStgElt, ...) -> MonStgElt"
    assert sprintf.doc.startswith("The string resulting")
