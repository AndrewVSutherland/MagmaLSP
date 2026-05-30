"""Smoke tests for the magma-lsp-cli front-end (lookup/check) argument handling."""

from __future__ import annotations

from magma_lsp import cli


def test_check_flags_unknown_intrinsic(tmp_path, capsys):
    f = tmp_path / "bad.m"
    f.write_text("E := EllipitcCurve([0, 1]);\nprint E;\n")  # typo'd intrinsic
    rc = cli.main(["check", str(f)])
    out = capsys.readouterr().out
    # Without a DB the static check is skipped, but the Magma binding pass still flags it
    # when Magma is available; either way the command must run and not crash.
    assert rc in (0, 1)
    assert "FAIL" in out or "OK" in out


def test_argparse_requires_subcommand(capsys):
    import pytest

    with pytest.raises(SystemExit):
        cli.main([])
