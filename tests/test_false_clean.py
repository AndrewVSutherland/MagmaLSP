"""Issue #15 hardening: an invalid/non-Magma executable must never yield a clean result, the
runner must bound its output capture, and the no-`timeout`-binary fallback must kill the whole
process group. None of these tests need a real Magma (they use stub executables), so they run
on any CI host.
"""

from __future__ import annotations

import os
import re
import stat
import time

import pytest

from magma_lsp import frontend
from magma_lsp.magma import runner, validate
from magma_lsp.magma.runner import (
    OUTPUT_HEAD_CAP,
    OUTPUT_TAIL_CAP,
    MagmaResult,
    ready_sentinel,
    run_source,
)
from magma_lsp.magma.validate import _launch_failed, execution_check, syntax_check


def _stub(tmp_path, body: str, name: str = "fakemagma") -> str:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


# ------------------------------------------------------------------ ready sentinel


def test_ready_sentinel_output_not_in_source():
    line, expect = ready_sentinel()
    # the expected output must NOT appear in the source line, else a binary that merely
    # echoes its input (cat) would pass the readiness check
    assert expect not in line
    assert line.endswith(";\n")


def test_launch_failed_rules():
    ok = MagmaResult("", 0, False)
    bad_exit = MagmaResult("", 3, False)
    hung = MagmaResult("", 124, True)
    assert _launch_failed([], ok, ready=False)  # /usr/bin/true: silent zero exit
    assert not _launch_failed([], ok, ready=True)  # clean check
    assert _launch_failed([], bad_exit, ready=True)  # crashed mid-check, no diagnostics
    assert not _launch_failed([], hung, ready=False)  # timeout is reported separately
    assert not _launch_failed([object()], bad_exit, ready=False)  # diagnostics exist


def test_true_binary_is_launch_failure_not_clean():
    """The audit's headline repro: /usr/bin/true exits 0 with no output."""
    res = syntax_check("x := 1;\n", magma_path="/usr/bin/true")
    assert res.launch_failed and not res.diagnostics


def test_check_with_true_binary_is_inconclusive():
    out = frontend.check("x := 1;\n", magma_path="/usr/bin/true", index=None)
    assert not out.ok
    assert "INCONCLUSIVE" in out.report


def test_echoing_binary_does_not_fake_readiness(tmp_path):
    # `cat` the source file: the output contains the sentinel's SOURCE (printf format
    # string), not its computed output — must still read as a launch failure
    fake = _stub(tmp_path, 'for a in "$@"; do [ -f "$a" ] && cat "$a"; done\nexit 0\n')
    res = syntax_check("x := 1;\n", magma_path=fake)
    assert res.launch_failed


def test_execution_check_with_true_binary_reports_error():
    res = execution_check("print 1;\n", magma_path="/usr/bin/true")
    assert res.diagnostics
    assert any("did not start" in d.message for d in res.diagnostics)


def test_run_with_true_binary_reports_error():
    out = frontend.run("print 1;\n", magma_path="/usr/bin/true")
    assert "did not start" in out.output
    assert out.returncode != 0


def test_run_missing_magma_is_explicit(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no magma")

    monkeypatch.setattr(frontend, "run_source", boom)
    out = frontend.run("print 1;\n")
    assert out.returncode == 127
    assert "Magma not found" in out.output


@pytest.mark.magma
def test_real_run_output_has_no_sentinel():
    out = frontend.run('print "hello";\n')
    assert out.output.strip() == "hello"
    assert "MLSP-" not in out.output


@pytest.mark.magma
def test_real_syntax_check_not_launch_failed():
    res = syntax_check("x := 1;\n")
    assert not res.launch_failed and not res.diagnostics
    err = syntax_check("x := ;\n")
    assert err.diagnostics and not err.launch_failed


# ------------------------------------------------------------------ bounded capture


def test_output_capture_is_bounded(tmp_path):
    # ~24 MB of output vs a 6 MB head+tail bound: the kept string stays bounded and the
    # drop is visible in both the marker and bytes_dropped
    fake = _stub(tmp_path, "head -c 25000000 /dev/zero | tr '\\0' 'a'\necho END-MARKER\n")
    res = run_source("print 1;\n", magma_path=fake, timeout=60)
    assert res.bytes_dropped > 0
    assert len(res.stdout) <= OUTPUT_HEAD_CAP + OUTPUT_TAIL_CAP + 4096
    assert "bytes of output dropped" in res.stdout
    assert res.stdout.rstrip().endswith("END-MARKER")  # the tail survives


def test_small_output_not_truncated(tmp_path):
    fake = _stub(tmp_path, "echo hello-world\n")
    res = run_source("print 1;\n", magma_path=fake, timeout=30)
    assert res.bytes_dropped == 0
    assert "hello-world" in res.stdout


# ------------------------------------------------------------------ fallback timeout


def test_fallback_timeout_kills_descendants(tmp_path, monkeypatch):
    """Without a `timeout` binary, the Python-side wall clock must SIGKILL the process
    GROUP: a child spawned by the (fake) Magma must not survive."""
    fake = _stub(tmp_path, "sleep 300 &\necho CHILD $!\nsleep 300\n")
    orig_which = runner.shutil.which
    monkeypatch.setattr(
        runner.shutil, "which", lambda cmd: None if cmd == "timeout" else orig_which(cmd)
    )
    t0 = time.monotonic()
    res = run_source("print 1;\n", magma_path=fake, timeout=1.0)
    assert time.monotonic() - t0 < 30  # nowhere near the child's 300 s
    assert res.timed_out and res.returncode == 124
    m = re.search(r"CHILD (\d+)", res.stdout)
    assert m, res.stdout
    pid = int(m.group(1))
    for _ in range(40):  # allow up to ~2 s for the reparented child to be reaped
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"descendant {pid} survived the fallback timeout")


# ------------------------------------------------------------------ attach failure


def test_attach_failure_never_reads_clean(monkeypatch):
    """Attach fails without a parse diagnostic (e.g. unreadable file): the result must
    carry an error, not read as a clean check of a file Magma never parsed."""

    def fake_run_source(src, **kw):
        m = re.search(r'printf "MLSP-%o-([0-9a-f]+)', src)
        ready = f"MLSP-1337-{m.group(1)}\n" if m else ""
        return MagmaResult(ready + "Cannot attach intrinsics\n", 0, False, "/x.m")

    monkeypatch.setattr(validate, "run_source", fake_run_source)
    res = validate._attach_check("intrinsic F()\n{doc}\n  return;\nend intrinsic;\n",
                                 magma_path=None, timeout=5.0)
    assert res.diagnostics and "could not Attach" in res.diagnostics[0].message
