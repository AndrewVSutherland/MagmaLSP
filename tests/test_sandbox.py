"""OS-level execution sandbox (bubblewrap): policy, argv construction, and end-to-end
write-blocking (CLAUDE.md §3b). The parse-only syntax strategies must stay unsandboxed."""

from __future__ import annotations

import os
import shutil
from types import SimpleNamespace

import pytest

from magma_lsp import frontend
from magma_lsp.magma import runner, validate
from magma_lsp.magma.runner import (
    NO_SANDBOX_ENV,
    SANDBOX_WRITABLE_ENV,
    MagmaResult,
    _sandbox_argv,
    sandbox_state,
)

_HAS_MAGMA = shutil.which("magma") is not None or os.path.exists("/opt/magma/magma")
_HAS_BWRAP = shutil.which("bwrap") is not None
# priming the probe here caches the host's true answer; unit tests pin their own copy
_BWRAP_WORKS = _HAS_BWRAP and runner._bwrap_functional(shutil.which("bwrap"))
magma = pytest.mark.skipif(not _HAS_MAGMA, reason="requires a Magma install")
needs_bwrap = pytest.mark.skipif(not _HAS_BWRAP, reason="requires bubblewrap (bwrap)")
needs_working_bwrap = pytest.mark.skipif(
    not _BWRAP_WORKS, reason="requires bubblewrap able to create namespaces on this host"
)


def _fresh_policy(monkeypatch, *, bwrap="/usr/bin/bwrap", works=True):
    """Deterministic sandbox policy for unit tests: no opt-out, no writable dirs, bwrap as
    given (None = absent), the functional probe pinned (no real bwrap runs), and the
    warn-once registry reset."""
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    monkeypatch.delenv(SANDBOX_WRITABLE_ENV, raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda name: bwrap)
    monkeypatch.setattr(runner, "_bwrap_ok", works)
    monkeypatch.setattr(runner, "_warned_once", set())


def _triple_index(argv, flag, a, b):
    """Index of the mount triple [flag, a, b] in argv (asserts it is present)."""
    for i in range(len(argv) - 2):
        if argv[i : i + 3] == [flag, a, b]:
            return i
    raise AssertionError(f"{[flag, a, b]} not found in {argv}")


# ------------------------------------------------------------------ policy


def test_sandbox_state_policy(monkeypatch):
    _fresh_policy(monkeypatch)
    assert sandbox_state() == "active"
    monkeypatch.setenv(NO_SANDBOX_ENV, "1")
    assert sandbox_state() == "disabled"
    monkeypatch.setenv(NO_SANDBOX_ENV, "0")  # "0" is not an opt-out
    assert sandbox_state() == "active"
    monkeypatch.delenv(NO_SANDBOX_ENV)
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert sandbox_state() == "unavailable"


def test_disabled_env_yields_no_prefix(monkeypatch):
    _fresh_policy(monkeypatch)
    monkeypatch.setenv(NO_SANDBOX_ENV, "1")
    assert _sandbox_argv("/tmp/src.m", "/some/cwd") == []


def test_missing_bwrap_warns_once_and_runs_unsandboxed(monkeypatch, capsys):
    _fresh_policy(monkeypatch, bwrap=None)
    assert _sandbox_argv("/tmp/src.m", None) == []
    assert _sandbox_argv("/tmp/src.m", None) == []
    err = capsys.readouterr().err
    assert err.count("WITHOUT the OS sandbox") == 1


def test_broken_bwrap_warns_once_and_runs_unsandboxed(monkeypatch, capsys):
    # bwrap on PATH but the functional probe fails (e.g. user namespaces disabled in a
    # container): fall back to unsandboxed-with-a-warning instead of failing every run
    _fresh_policy(monkeypatch, works=False)
    assert sandbox_state() == "broken"
    assert _sandbox_argv("/tmp/src.m", None) == []
    assert _sandbox_argv("/tmp/src.m", None) == []
    err = capsys.readouterr().err
    assert err.count("cannot create a sandbox") == 1


# ------------------------------------------------------------------ argv shape


def test_sandbox_argv_shape_and_mount_order(monkeypatch):
    _fresh_policy(monkeypatch)
    src = "/anywhere/tmp-src.m"
    argv = _sandbox_argv(src, "/some/cwd")
    assert argv[0] == "/usr/bin/bwrap"
    # licensing constraint: the network namespace must stay shared (CLAUDE.md §3b)
    assert "--unshare-net" not in argv
    for flag in ("--unshare-pid", "--unshare-ipc", "--new-session", "--die-with-parent"):
        assert flag in argv
    root = _triple_index(argv, "--ro-bind", "/", "/")
    dev = argv.index("--dev")
    proc = argv.index("--proc")
    tmp = argv.index("--tmpfs")
    assert argv[tmp + 1] == "/tmp"
    cwd = _triple_index(argv, "--ro-bind", "/some/cwd", "/some/cwd")
    source = _triple_index(argv, "--ro-bind", src, src)
    # later mounts shadow earlier ones; /dev and /proc must precede every bind that could
    # live beneath them (TMPDIR=/dev/shm puts the source file under /dev)
    assert root < dev < tmp and root < proc < tmp
    assert tmp < cwd < source
    assert argv[argv.index("--chdir") + 1] == "/some/cwd"


def test_sandbox_argv_without_cwd(monkeypatch):
    _fresh_policy(monkeypatch)
    argv = _sandbox_argv("/anywhere/tmp-src.m", None)
    assert "--chdir" not in argv
    assert argv.count("--ro-bind") == 2  # root + source only


def test_writable_dirs_bound_rw_between_cwd_and_source(monkeypatch, tmp_path, capsys):
    _fresh_policy(monkeypatch)
    missing = tmp_path / "nope"
    monkeypatch.setenv(SANDBOX_WRITABLE_ENV, f"{tmp_path}:{missing}")
    src = "/anywhere/tmp-src.m"
    argv = _sandbox_argv(src, "/some/cwd")
    cwd = _triple_index(argv, "--ro-bind", "/some/cwd", "/some/cwd")
    rw = _triple_index(argv, "--bind", str(tmp_path), str(tmp_path))
    source = _triple_index(argv, "--ro-bind", src, src)
    # a writable dir may deliberately override cwd's read-only view, never the source file
    assert cwd < rw < source
    assert str(missing) not in argv
    assert "not a directory" in capsys.readouterr().err


# ------------------------------------------------------------------ who sandboxes


def test_run_source_sandbox_flag_controls_bwrap_prefix(monkeypatch):
    _fresh_policy(monkeypatch)
    seen: dict = {}

    def fake_subprocess_run(argv, **kw):
        seen["argv"] = list(argv)
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(runner, "find_magma", lambda p=None: "/fake/magma")

    runner.run_source("print 1;", sandbox=False)
    assert "/usr/bin/bwrap" not in seen["argv"]
    assert seen["argv"][0] == "timeout"

    runner.run_source("print 1;", sandbox=True)
    assert seen["argv"][0] == "timeout"
    assert seen["argv"][2] == "/usr/bin/bwrap"  # timeout OUTSIDE bwrap kills the whole tree


def test_execution_check_requests_sandbox(monkeypatch):
    seen: dict = {}

    def fake_run_source(src, **kw):
        seen.update(kw)
        return MagmaResult("", 0, False, "/x.m")

    monkeypatch.setattr(validate, "run_source", fake_run_source)
    validate.execution_check("print 1;")
    assert seen.get("sandbox") is True


def test_syntax_check_never_requests_sandbox(monkeypatch):
    seen: dict = {}

    def fake_run_source(src, **kw):
        seen.update(kw)
        return MagmaResult("", 0, False, "/x.m")

    monkeypatch.setattr(validate, "run_source", fake_run_source)
    validate.syntax_check("x := 1;\n")
    assert "sandbox" not in seen  # the hot path must stay bwrap-free


def test_frontend_run_requests_sandbox(monkeypatch):
    seen: dict = {}

    def fake_run_source(src, **kw):
        seen.update(kw)
        return MagmaResult("", 0, False, "/x.m")

    monkeypatch.setattr(frontend, "run_source", fake_run_source)
    frontend.run("print 1;")
    assert seen.get("sandbox") is True


def test_guide_reports_sandbox_state(monkeypatch):
    from magma_lsp.guide import guide

    _fresh_policy(monkeypatch)
    assert "READ-ONLY" in guide()
    monkeypatch.setenv(NO_SANDBOX_ENV, "1")
    assert "DISABLED" in guide()
    monkeypatch.delenv(NO_SANDBOX_ENV)
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert "UNAVAILABLE" in guide()
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(runner, "_bwrap_ok", False)
    assert "BROKEN" in guide()


# ------------------------------------------------------------------ end-to-end (magma + bwrap)


@needs_bwrap
def test_real_bwrap_probe_resolves(monkeypatch):
    # the one test that runs _bwrap_functional for real (not pinned). A host can have bwrap
    # yet restrict user namespaces (some CI/containers), so "broken" is a legitimate outcome
    # there; the write-blocking tests below are the proof of "active" where Magma exists.
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    monkeypatch.setattr(runner, "_bwrap_ok", None)
    assert sandbox_state() in ("active", "broken")
    assert runner._bwrap_ok is not None  # probe ran and cached


@magma
@needs_working_bwrap
def test_sandboxed_write_is_blocked_and_output_intact(tmp_path, monkeypatch):
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    target = tmp_path / "pwned.txt"
    res = frontend.run(f'_ := System("touch {target}");\nprint "alive";', timeout=30)
    assert "alive" in res.output
    assert not target.exists()


@magma
@needs_working_bwrap
def test_sandboxed_execution_check_blocks_write_in_own_cwd(tmp_path, monkeypatch):
    # cwd is ro-bound back over the /tmp tmpfs: visible for reads, still not writable
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    target = tmp_path / "out.txt"
    res = validate.execution_check(
        '_ := System("touch out.txt");\nprint "done";', cwd=str(tmp_path), timeout=30
    )
    assert res.diagnostics == []
    assert not target.exists()


@magma
@needs_working_bwrap
def test_sandboxed_relative_load_still_resolves(tmp_path, monkeypatch):
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    (tmp_path / "sib.m").write_text("sibf := func<n | n + 41>;\n", encoding="utf-8")
    res = frontend.run(
        'load "sib.m";\nprint sibf(1);', filename=str(tmp_path / "main.m"), timeout=30
    )
    assert "42" in res.output
    assert res.returncode == 0


@magma
@needs_working_bwrap
def test_opt_out_env_really_disables_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv(NO_SANDBOX_ENV, "1")
    target = tmp_path / "written.txt"
    res = frontend.run(f'_ := System("touch {target}");\nprint "done";', timeout=30)
    assert "done" in res.output
    assert target.exists()


@magma
@needs_working_bwrap
def test_writable_dir_escape_hatch(tmp_path, monkeypatch):
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    monkeypatch.setenv(SANDBOX_WRITABLE_ENV, str(tmp_path))
    res = frontend.run(
        '_ := System("touch out.txt");\nprint "done";',
        filename=str(tmp_path / "main.m"),
        timeout=30,
    )
    assert "done" in res.output
    assert (tmp_path / "out.txt").exists()


@magma
@needs_working_bwrap
def test_source_under_dev_shm_still_visible(monkeypatch):
    # TMPDIR=/dev/shm is a real configuration: the temp source then lives under /dev, and a
    # --dev mount placed after the source bind would hide it (every sandboxed run failing
    # with Can't open file). tempfile.tempdir is the documented per-process override.
    import tempfile

    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", "/dev/shm")
    res = frontend.run('print "shm ok";', timeout=30)
    assert "shm ok" in res.output
    assert res.returncode == 0


@magma
@needs_working_bwrap
def test_sandboxed_timeout_still_enforced(monkeypatch):
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    res = frontend.run("x := 0;\nwhile true do x +:= 1; end while;", timeout=2)
    assert res.timed_out


@magma
@needs_working_bwrap
def test_sandboxed_memory_limit_still_enforced(monkeypatch):
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    res = validate.execution_check(
        "a := [ i : i in [1..10^9] ];\nprint #a;",
        memory_bytes=256 * 1024 * 1024,
        timeout=60,
    )
    assert any("memory" in d.message.lower() for d in res.diagnostics)
