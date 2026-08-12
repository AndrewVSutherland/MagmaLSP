"""Smoke tests for the MCP front-end (magma_lsp.mcp_server).

The FastMCP ``@tool()`` decorator returns the original function, so each tool is callable directly.
Magma-dependent assertions are marked so they skip when Magma is absent. Mirrors the CLI tests,
since both adapters share ``magma_lsp.frontend``.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from magma_lsp import mcp_server as m

_HAS_MAGMA = shutil.which("magma") is not None or shutil.which("/opt/magma/magma") is not None


def test_tools_registered():
    names = {t.name for t in asyncio.run(m.mcp.list_tools())}
    assert {"magma_lookup", "magma_check", "magma_run"} <= names


def test_lookup_returns_text():
    # Without a DB this reports the build hint; with one it returns markdown. Either is a str.
    out = m.magma_lookup(["Factorization"])
    assert isinstance(out, str) and out


@pytest.mark.magma
def test_check_flags_unknown_intrinsic():
    if not _HAS_MAGMA:
        pytest.skip("Magma not available")
    out = m.magma_check("E := EllipitcCurve([0, 1]);\nprint E;\n")  # typo
    assert "FAIL" in out


@pytest.mark.magma
def test_run_executes():
    if not _HAS_MAGMA:
        pytest.skip("Magma not available")
    assert m.magma_run("print 6*7;").strip() == "42"
