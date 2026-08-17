"""OS-level execution sandbox (bubblewrap): policy, argv construction, and end-to-end
write-blocking (CLAUDE.md §3b). The parse-only syntax strategies must stay unsandboxed."""

from __future__ import annotations

import os
import shutil

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
    # Neutralize host daemon sockets so argv-shape assertions are deterministic regardless of
    # the machine: a CI runner with Docker installed has /var/run/docker.sock, which would add
    # `--ro-bind /dev/null <sock>` entries and break exact-count checks (real GitHub CI failure).
    monkeypatch.setattr(runner, "_PRIVILEGED_SOCKETS", ())


def _nonmasked_base() -> str:
    """A writable directory whose realpath is OUTSIDE the masked roots (/tmp, /dev), for
    relative-load tests that need the source visible via the read-only root. Skips if the
    only writable locations resolve under a mask (e.g. HOME=/tmp/ci-user)."""
    for cand in (os.path.expanduser("~"), "/var/tmp", os.getcwd()):
        rp = os.path.realpath(cand)  # realpath: HOME itself may be a symlink into /tmp
        if rp == "/" or any(rp == m or rp.startswith(m + "/") for m in ("/tmp", "/dev")):
            continue
        if os.path.isdir(rp) and os.access(rp, os.W_OK):
            return rp
    pytest.skip("no writable directory outside the masked roots (/tmp, /dev)")


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
    argv = _sandbox_argv(src, "/home/user/proj")
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
    source = _triple_index(argv, "--ro-bind", src, src)
    # later mounts shadow earlier ones; /dev and /proc must precede every bind that could
    # live beneath them (TMPDIR=/dev/shm puts the source file under /dev)
    assert root < dev < tmp and root < proc < tmp < source
    # the cwd is never rebound, only chdir'd into (visible via the read-only root)
    assert _count_triple(argv, "--ro-bind", "/home/user/proj", "/home/user/proj") == 0
    assert argv[argv.index("--chdir") + 1] == "/home/user/proj"


def _count_triple(argv: list[str], flag: str, a: str, b: str) -> int:
    return sum(1 for i in range(len(argv) - 2) if argv[i : i + 3] == [flag, a, b])


def test_cwd_never_rebound_and_chdir_visibility(monkeypatch):
    _fresh_policy(monkeypatch)
    src = "/anywhere/tmp-src.m"
    # NO cwd is ever rebound. A visible cwd (not hidden by a mask) is chdir'd into as-is; a
    # cwd strictly beneath /tmp or /dev is hidden by the mask, so chdir falls back to "/".
    visible = {
        "/home/user/proj": "/home/user/proj",
        "/": "/",
        "/proc": "/proc",
        "/tmp": "/tmp",  # the mask root itself exists (tmpfs), so chdir into it is fine
        "/dev": "/dev",
    }
    for cwd, chdir in visible.items():
        argv = _sandbox_argv(src, cwd)
        assert _count_triple(argv, "--ro-bind", cwd, cwd) == (1 if cwd == "/" else 0)
        assert argv[argv.index("--chdir") + 1] == chdir
    for cwd in ("/tmp/proj", "/dev/shm/work"):  # hidden beneath a mask → chdir "/"
        argv = _sandbox_argv(src, cwd)
        assert _count_triple(argv, "--ro-bind", cwd, cwd) == 0
        assert argv[argv.index("--chdir") + 1] == "/"


def test_symlinked_cwd_resolves_for_chdir_and_rebind(monkeypatch):
    # a symlink under /tmp crossing OUT of the mask (/tmp/link -> ~/real): --chdir must use the
    # resolved target (visible via the read-only root), NOT the /tmp path (hidden by the tmpfs,
    # which made bwrap fail before Magma started); and since the target escapes the mask it is
    # not rebound
    import tempfile

    _fresh_policy(monkeypatch)
    # a stable target OUTSIDE the masked roots: the repo's own directory
    target = os.path.realpath(os.getcwd())
    link = os.path.join(tempfile.gettempdir(), f"sbx-cwdlink-{os.getpid()}")
    if os.path.islink(link) or os.path.exists(link):
        os.unlink(link)
    os.symlink(target, link)
    try:
        argv = _sandbox_argv("/anywhere/tmp-src.m", link)
        assert argv[argv.index("--chdir") + 1] == target  # resolved, not the /tmp symlink
        assert _count_triple(argv, "--ro-bind", link, link) == 0  # symlink path not bound
        assert _count_triple(argv, "--ro-bind", target, target) == 0  # target escapes mask
    finally:
        os.unlink(link)


def test_sandbox_argv_without_cwd(monkeypatch):
    _fresh_policy(monkeypatch)
    monkeypatch.setattr(runner, "_socket_masks", list)  # exact-count check: no host sockets
    argv = _sandbox_argv("/anywhere/tmp-src.m", None)
    assert "--chdir" not in argv
    assert argv.count("--ro-bind") == 2  # root + source only


def test_probe_argv_prefers_unmasked_true(monkeypatch):
    _fresh_policy(monkeypatch)
    monkeypatch.setattr(
        runner.shutil, "which", lambda name: {"true": "/usr/bin/true"}.get(name, "/usr/bin/bwrap")
    )
    argv = runner._probe_argv("/usr/bin/bwrap")
    assert argv[-1] == "/usr/bin/true"
    assert argv.count("--ro-bind") == 1  # just the root; nothing to un-mask


def test_probe_argv_binds_masked_interpreter(monkeypatch):
    # no `true` anywhere, interpreter in an ephemeral /tmp venv: the probe must ro-bind the
    # interpreter's dir back or a working bwrap gets misclassified as broken
    _fresh_policy(monkeypatch)
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(runner.sys, "executable", "/tmp/venv/bin/python")
    argv = runner._probe_argv("/usr/bin/bwrap")
    assert argv[-3:] == ["/tmp/venv/bin/python", "-c", "pass"]
    bind = _triple_index(argv, "--ro-bind", "/tmp/venv/bin", "/tmp/venv/bin")
    assert argv.index("--tmpfs") < bind


def test_probe_argv_masked_true_falls_back_to_interpreter(monkeypatch):
    _fresh_policy(monkeypatch)
    monkeypatch.setattr(
        runner.shutil, "which", lambda name: "/tmp/odd/true" if name == "true" else None
    )
    monkeypatch.setattr(runner.sys, "executable", "/usr/bin/python3")
    argv = runner._probe_argv("/usr/bin/bwrap")
    assert argv[-3:] == ["/usr/bin/python3", "-c", "pass"]
    assert argv.count("--ro-bind") == 1  # interpreter is not masked; no extra binds


@needs_working_bwrap
def test_real_probe_with_tmp_interpreter(tmp_path, monkeypatch):
    # exercise the masked-interpreter probe path for real: a /tmp symlink to our interpreter
    # with `true` hidden — the probe must bind the /tmp dir back and still report working
    import sys as real_sys

    orig_which = shutil.which
    link = tmp_path / "python"
    link.symlink_to(real_sys.executable)
    monkeypatch.setattr(runner.sys, "executable", str(link))
    monkeypatch.setattr(
        runner.shutil, "which", lambda name: None if name == "true" else orig_which(name)
    )
    monkeypatch.setattr(runner, "_bwrap_ok", None)
    assert runner._bwrap_functional(orig_which("bwrap")) is True


def test_socket_masks_existing_privileged_sockets(monkeypatch, tmp_path):
    import socket as socketmod

    real = tmp_path / "docker.sock"
    srv = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
    srv.bind(str(real))
    try:
        monkeypatch.setattr(runner, "_PRIVILEGED_SOCKETS", (str(real), "/nonexistent/x.sock"))
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        args = runner._socket_masks()
        # existing socket masked with /dev/null; absent path skipped
        assert args == ["--ro-bind", "/dev/null", str(real)]
    finally:
        srv.close()


def test_socket_masks_skips_non_socket_and_dedupes(monkeypatch, tmp_path):
    import socket as socketmod

    regular = tmp_path / "not-a-sock"
    regular.write_text("")  # a regular file at a socket path must NOT be masked
    real = tmp_path / "podman.sock"
    link = tmp_path / "podman-link.sock"  # symlink to the same socket → one bind only
    srv = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
    srv.bind(str(real))
    link.symlink_to(real)
    try:
        monkeypatch.setattr(runner, "_PRIVILEGED_SOCKETS", (str(regular), str(real), str(link)))
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        args = runner._socket_masks()
        assert args.count("--ro-bind") == 1
        assert str(regular) not in args
    finally:
        srv.close()


def test_socket_masks_placed_in_argv(monkeypatch, tmp_path):
    import socket as socketmod

    real = tmp_path / "docker.sock"
    srv = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
    srv.bind(str(real))
    try:
        _fresh_policy(monkeypatch)
        monkeypatch.setattr(runner, "_PRIVILEGED_SOCKETS", (str(real),))
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        # a writable dir containing the socket is the remaining re-exposure risk: the mask
        # must come AFTER the writable bind (and the source bind)
        monkeypatch.setenv(SANDBOX_WRITABLE_ENV, str(tmp_path))
        argv = _sandbox_argv("/anywhere/tmp-src.m", None)
        mask = _triple_index(argv, "--ro-bind", "/dev/null", str(real))
        rw_bind = _triple_index(argv, "--bind", str(tmp_path), str(tmp_path))
        source_bind = _triple_index(argv, "--ro-bind", "/anywhere/tmp-src.m", "/anywhere/tmp-src.m")
        assert mask > rw_bind and mask > source_bind
    finally:
        srv.close()


@needs_working_bwrap
def test_privileged_socket_unreachable_in_sandbox(monkeypatch):
    # end-to-end: a live listening socket at a "privileged" path is connectable under a bare
    # --ro-bind / / (the read-only root does not stop connect()) but NOT once _socket_masks
    # overmounts it with /dev/null. The socket must live OUTSIDE the masked roots (/tmp, /dev)
    # so the tmpfs isn't what hides it — else the test would pass for the wrong reason.
    import contextlib
    import socket as socketmod
    import subprocess
    import sys as realsys
    import tempfile
    import threading

    # The socket must live on a writable host path OUTSIDE the sandbox's masked roots
    # (/tmp, /dev, /proc) — else the tmpfs, not the /dev/null mask, would be why it's
    # unreachable. AF_UNIX paths are also capped at ~108 bytes, ruling out deep temp dirs.
    # Try a few candidate bases; skip cleanly where none is writable (e.g. a read-only $HOME
    # in a container), since this scenario can't be constructed there.
    base = None
    for cand in (os.path.expanduser("~"), "/var/tmp", os.getcwd()):
        real = os.path.realpath(cand)
        if real.startswith(("/tmp", "/dev", "/proc")) or not os.access(real, os.W_OK):
            continue
        base = real
        break
    if base is None:
        pytest.skip("no writable host dir outside the masked roots to place a test socket")
    try:
        workdir = tempfile.mkdtemp(prefix=".sbx-sock-", dir=base)
    except OSError:
        pytest.skip("could not create a writable socket dir outside the masked roots")
    sock = os.path.join(workdir, "d.sock")  # short name: AF_UNIX path length limit
    src = os.path.join(workdir, "src.m")
    open(src, "w").close()
    srv = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
    srv.bind(sock)
    srv.listen(1)

    def _accept():
        with contextlib.suppress(OSError):
            srv.accept()

    threading.Thread(target=_accept, daemon=True).start()
    monkeypatch.setattr(runner, "_PRIVILEGED_SOCKETS", (sock,))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    probe = (
        f'import socket\ns=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n'
        f'try:\n s.connect({sock!r}); print("CONNECTED")\n'
        f'except OSError as e: print("blocked", e.__class__.__name__)'
    )
    bwrap = shutil.which("bwrap")
    try:
        control = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                   "--tmpfs", "/tmp", "--die-with-parent", realsys.executable, "-c", probe]
        assert "CONNECTED" in subprocess.run(control, capture_output=True, text=True,
                                             timeout=30).stdout  # baseline: reachable
        masked = [*runner._sandbox_argv(src, None), realsys.executable, "-c", probe]
        out = subprocess.run(masked, capture_output=True, text=True, timeout=30).stdout
        assert "blocked" in out and "CONNECTED" not in out
        # regression: cwd == the socket's own directory. The cwd rebind must NOT re-expose
        # the socket the mask covered (the mask is placed after all directory rebinds).
        masked_cwd = [*runner._sandbox_argv(src, workdir), realsys.executable, "-c", probe]
        out2 = subprocess.run(masked_cwd, capture_output=True, text=True, timeout=30).stdout
        assert "blocked" in out2 and "CONNECTED" not in out2
    finally:
        srv.close()
        with contextlib.suppress(OSError):
            os.unlink(sock)
            os.unlink(src)
            os.rmdir(workdir)


def test_magma_under_masked_mount_is_bound_back(monkeypatch):
    _fresh_policy(monkeypatch)
    monkeypatch.setattr(runner, "_socket_masks", list)  # exact-count check: no host sockets
    src = "/anywhere/tmp-src.m"
    # an install (or wrapper symlink) under /tmp would be hidden by the tmpfs: its dir must
    # be ro-bound back, after the masks and before the source
    argv = _sandbox_argv(src, None, "/tmp/minst/magma")
    inst = _triple_index(argv, "--ro-bind", "/tmp/minst", "/tmp/minst")
    assert argv.index("--tmpfs") < inst < _triple_index(argv, "--ro-bind", src, src)
    # a normally-placed install needs no extra bind
    argv2 = _sandbox_argv(src, None, "/opt/magma/magma")
    assert argv2.count("--ro-bind") == 2  # root + source only


def test_magma_directly_at_masked_root_binds_only_the_file(monkeypatch):
    # MAGMA_PATH="/tmp/magma": binding its DIR would be `--ro-bind /tmp /tmp`, re-exposing the
    # host /tmp and undoing the throwaway tmpfs. Only the FILE must be bound.
    _fresh_policy(monkeypatch)
    src = "/anywhere/tmp-src.m"
    argv = _sandbox_argv(src, None, "/tmp/magma")
    assert _count_triple(argv, "--ro-bind", "/tmp", "/tmp") == 0  # whole root NOT re-bound
    assert _triple_index(argv, "--ro-bind", "/tmp/magma", "/tmp/magma") > argv.index("--tmpfs")
    # same for a device-tree executable
    argv2 = _sandbox_argv(src, None, "/dev/magma")
    assert _count_triple(argv2, "--ro-bind", "/dev", "/dev") == 0
    assert _triple_index(argv2, "--ro-bind", "/dev/magma", "/dev/magma") > 0


def test_writable_dirs_bound_rw_between_cwd_and_source(monkeypatch, tmp_path, capsys):
    _fresh_policy(monkeypatch)
    missing = tmp_path / "nope"
    monkeypatch.setenv(SANDBOX_WRITABLE_ENV, f"{tmp_path}:{missing}")
    src = "/anywhere/tmp-src.m"
    argv = _sandbox_argv(src, None)
    rw = _triple_index(argv, "--bind", str(tmp_path), str(tmp_path))
    source = _triple_index(argv, "--ro-bind", src, src)
    # writable binds come before the source file (which stays read-only even inside them)
    assert rw < source
    assert str(missing) not in argv
    assert "not a directory" in capsys.readouterr().err


# ------------------------------------------------------------------ who sandboxes


def test_run_source_sandbox_flag_controls_bwrap_prefix(monkeypatch):
    _fresh_policy(monkeypatch)
    seen: dict = {}

    def fake_run_capped(argv, **kw):
        seen["argv"] = list(argv)
        return b"", 0, False, 0

    monkeypatch.setattr(runner, "_run_capped", fake_run_capped)
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
def test_sandboxed_relative_load_resolves_for_normal_cwd(monkeypatch):
    # relative loads work when the source is at a NORMAL (non-masked) path: the dir is visible
    # through the read-only root and chdir'd into. (A source under /tmp is unsupported by
    # design — see test_tmp_direct_relative_load_is_unsupported.)
    import contextlib
    import tempfile

    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    d = tempfile.mkdtemp(prefix=".sbx-relload-", dir=_nonmasked_base())
    try:
        with open(os.path.join(d, "sib.m"), "w") as fh:
            fh.write("sibf := func<n | n + 41>;\n")
        res = frontend.run(
            'load "sib.m";\nprint sibf(1);', filename=os.path.join(d, "main.m"), timeout=30
        )
        assert "42" in res.output, res.output
        assert res.returncode == 0
    finally:
        import shutil as _sh

        with contextlib.suppress(OSError):
            _sh.rmtree(d)


@magma
@needs_working_bwrap
def test_tmp_direct_relative_load_is_unsupported(tmp_path, monkeypatch):
    # BY DESIGN (drop-cwd-rebind): a source under /tmp cannot resolve a relative sibling load
    # — the throwaway tmpfs hides the sibling and cwd is not rebound. This documents the
    # deliberate limitation (workaround: a normal path, or an absolute load path).
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    (tmp_path / "sib.m").write_text("sibf := func<n | n + 41>;\n", encoding="utf-8")
    res = frontend.run(
        'load "sib.m";\nprint sibf(1);', filename=str(tmp_path / "main.m"), timeout=30
    )
    assert "42" not in res.output  # the load does not resolve


@magma
@needs_working_bwrap
def test_sandboxed_relative_load_through_symlinked_tmp_cwd(monkeypatch):
    # a /tmp symlink whose target is a NORMAL dir (crosses OUT of /tmp): chdir resolves to the
    # visible target (not the tmpfs-hidden symlink path), so relative loads still work.
    import contextlib
    import shutil as _sh
    import tempfile

    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    real = tempfile.mkdtemp(prefix=".sbx-symreal-", dir=_nonmasked_base())
    with open(os.path.join(real, "sib.m"), "w") as fh:
        fh.write("sibf := func<n | n + 41>;\n")
    link = os.path.join(runner.tempfile.gettempdir(), f"sbx-cwdlink-{os.getpid()}")
    with contextlib.suppress(OSError):
        os.unlink(link)
    os.symlink(real, link)  # /tmp/sbx-cwdlink -> ~/.sbx-symreal-* (a normal dir)
    try:
        res = frontend.run(
            'load "sib.m";\nprint sibf(1);', filename=os.path.join(link, "main.m"), timeout=30
        )
        assert "42" in res.output, res.output
        assert res.returncode == 0
    finally:
        with contextlib.suppress(OSError):
            os.unlink(link)
            _sh.rmtree(real)


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


def test_writable_dirs_resolves_symlinks(monkeypatch, tmp_path):
    # a granted writable dir given via a symlink must bind (and chdir to) its realpath, so it
    # agrees with _resolved_cwd(); else the bind and cwd disagree and writes hit the ro twin
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv(SANDBOX_WRITABLE_ENV, str(link))
    assert runner._writable_dirs() == [str(real)]


@magma
@needs_working_bwrap
def test_writable_dir_escape_hatch_via_symlink(monkeypatch):
    # grant a /tmp symlink pointing at a normal dir: a relative write from a source in it must
    # land in the real dir (codex PR #14 — bind and cwd must use the same realpath)
    import contextlib
    import shutil as _sh
    import tempfile

    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    real = tempfile.mkdtemp(prefix=".sbx-wrsym-", dir=_nonmasked_base())
    link = os.path.join(runner.tempfile.gettempdir(), f"sbx-wrlink-{os.getpid()}")
    with contextlib.suppress(OSError):
        os.unlink(link)
    os.symlink(real, link)
    monkeypatch.setenv(SANDBOX_WRITABLE_ENV, link)
    try:
        res = frontend.run(
            '_ := System("touch out.txt");\nprint "done";',
            filename=os.path.join(link, "main.m"),
            timeout=30,
        )
        assert "done" in res.output
        assert os.path.exists(os.path.join(real, "out.txt"))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(link)
            _sh.rmtree(real)


def _writable_submount() -> str | None:
    """First host mountpoint below / (not overmounted by the sandbox recipe) where this
    user can write — e.g. a separate /home partition or the /run/user/<uid> tmpfs."""
    skip = ("/tmp", "/dev", "/proc", "/sys")
    try:
        with open("/proc/self/mounts", encoding="utf-8") as fh:
            for line in fh:
                fields = line.split()
                mp, fstype = fields[1], fields[2]
                if mp == "/" or "\\" in mp or fstype.startswith("fuse"):
                    continue
                if any(mp == s or mp.startswith(s + "/") for s in skip):
                    continue
                if os.path.isdir(mp) and os.access(mp, os.W_OK):
                    return mp
    except OSError:
        pass
    return None


@magma
@needs_working_bwrap
def test_separate_writable_submounts_are_read_only(monkeypatch):
    # --ro-bind / / must cover recursively inherited submounts: a separate /home, the
    # /run/user/<uid> tmpfs, ... (bubblewrap remounts them read-only — via
    # mount_setattr(AT_RECURSIVE) on kernels >= 5.12). This asserts it empirically against
    # a real submount of the machine running the suite.
    import contextlib

    mp = _writable_submount()
    if mp is None:
        pytest.skip("no separate user-writable submount on this host")
    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    target = os.path.join(mp, f"magma-lsp-sbx-submount-{os.getpid()}.txt")
    try:
        res = frontend.run(f'_ := System("touch {target}");\nprint "alive";', timeout=30)
        assert "alive" in res.output
        assert not os.path.exists(target)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(target)


@magma
@needs_working_bwrap
def test_magma_invoked_via_tmp_symlink_still_runs(tmp_path, monkeypatch):
    # a wrapper symlink under /tmp (masked by the sandbox tmpfs) must still exec: its dir is
    # ro-bound back, and the symlink target resolves through the read-only root
    from magma_lsp.magma.runner import find_magma, run_source

    monkeypatch.delenv(NO_SANDBOX_ENV, raising=False)
    real = find_magma(None)
    link = tmp_path / "magma"
    link.symlink_to(real)
    res = run_source("print 41 + 1;", magma_path=str(link), sandbox=True, timeout=30)
    assert "42" in res.stdout
    assert res.returncode == 0


@needs_working_bwrap
def test_cwd_at_root_or_proc_does_not_reexpose_host_mounts(monkeypatch, tmp_path):
    # filename="/main.m" -> cwd="/", or filename="/proc/x.m" -> cwd="/proc": the cwd rebind
    # must NOT re-expose the host procfs (its /proc/<pid>/root is a mutation path) or undo the
    # /tmp tmpfs. A fresh procfs under --unshare-pid shows only a couple of pids; the host one
    # shows hundreds. Also assert a host /tmp marker stays hidden by the tmpfs.
    import contextlib
    import subprocess
    import sys as realsys

    monkeypatch.setattr(runner, "_PRIVILEGED_SOCKETS", ())
    marker = f"/tmp/magma-lsp-hosttmp-marker-{os.getpid()}"
    open(marker, "w").close()
    src = tmp_path / "s.m"
    src.write_text("")
    probe = (
        'import os\n'
        f'print("marker", os.path.exists({marker!r}))\n'
        'print("pids", len([p for p in os.listdir("/proc") if p.isdigit()]))'
    )
    try:
        for cwd in ("/", "/proc"):
            argv = [*runner._sandbox_argv(str(src), cwd), realsys.executable, "-c", probe]
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            out = r.stdout
            assert "marker False" in out, f"host /tmp re-exposed with cwd={cwd}: {out or r.stderr}"
            npids = int(out.split("pids")[1].strip())
            assert npids < 25, f"host /proc re-exposed with cwd={cwd}: {npids} pids"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(marker)


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
