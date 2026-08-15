"""Sandboxed invocation of a real Magma process.

Every call follows the golden recipe verified in CLAUDE.md §3:

    timeout <T> magma -b -n <tempfile> </dev/null 2>&1

- ``-b`` batch (no banner), ``-n`` no startup file (hermetic).
- ``</dev/null`` is mandatory: without it Magma can block forever reading stdin after some
  runtime errors. We pass ``stdin=DEVNULL``.
- external ``timeout`` is the hard wall-clock backstop (kills the process group); a slightly
  larger ``subprocess`` timeout is a second backstop.
- a fresh process per call (cold start ~11 ms), never reused.

Callers that *execute* user code (as opposed to the parse-only syntax strategies) pass
``sandbox=True``, which additionally wraps the process in a bubblewrap OS sandbox — see
CLAUDE.md §3b for the verified recipe and its constraints (in particular: the network
namespace must stay shared, because Magma's license check reads the host MAC address).
"""

from __future__ import annotations

import contextlib
import math
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass

# Preamble that keeps stdout to results + error blocks and prevents 80-col wrapping
# (which would otherwise break the error-block parser). See CLAUDE.md §3/§5.
SERVER_PREAMBLE = (
    "SetColumns(0);\nSetAutoColumns(false);\nSetEchoInput(false);\nSetIgnorePrompt(true);\n"
)


@dataclass(frozen=True)
class MagmaResult:
    stdout: str  # combined stdout+stderr
    returncode: int
    timed_out: bool
    # The temp file the source ran from: lets callers filter Magma's positioned error blocks
    # to *our* file, so program output that merely looks like an error block is not parsed
    # as a diagnostic. (The file itself is deleted before this returns.)
    source_path: str | None = None


def find_magma(explicit: str | None = None) -> str | None:
    """Resolve the Magma wrapper: explicit path, then ``MAGMA_PATH`` env (set e.g. by the
    plugin's ``.mcp.json``), then PATH, then the known install locations."""
    env = os.environ.get("MAGMA_PATH")
    for candidate in (explicit, env, "magma", "/opt/magma/magma", "/usr/local/bin/magma"):
        if not candidate:
            continue
        # require an EXECUTABLE regular file: an existing-but-unusable candidate (plain file,
        # directory) would win precedence, fail to launch, and read as a clean check
        resolved = shutil.which(candidate)
        if resolved is None and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            resolved = candidate
        if resolved:
            return resolved
    return None


# ----------------------------------------------------------------------------------------------
# OS-level execution sandbox (bubblewrap)
#
# `magma_run` / `magma_check(execute=True)` hand an agent real code execution: Magma's
# System(...) / Pipe(...), file writes, and network all work, bounded only by `timeout` and
# SetMemoryLimit. When bubblewrap is available, execution passes run under a read-only view of
# the filesystem, which blocks the worst vector (filesystem mutation). Deliberately NOT blocked:
# shell-out and network egress — Magma's license check reads the host MAC address, and
# `--unshare-net` makes it fail with "This host has the following MAC address(es): <empty>"
# (verified on 2.29-9; beware that `magma -V` skips the license check, so a -V probe under
# --unshare-net misleadingly succeeds).
#
# The parse-only syntax strategies (function wrap, Attach) execute nothing user-level and stay
# unsandboxed, keeping the every-edit hot path at its measured ~12.5 ms.
# ----------------------------------------------------------------------------------------------

NO_SANDBOX_ENV = "MAGMA_LSP_NO_SANDBOX"
# Colon-separated directories to bind read-write inside the sandbox, for programs that
# legitimately write output files. Unset by default (everything read-only).
SANDBOX_WRITABLE_ENV = "MAGMA_LSP_SANDBOX_WRITABLE"

_warned_once: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned_once:
        _warned_once.add(key)
        print(message, file=sys.stderr)


_bwrap_ok: bool | None = None  # per-process cache of the functional probe


def _probe_argv(bwrap: str) -> list[str]:
    """The functional-probe command, chosen so the probed executable cannot be shadowed by
    the probe's own masking mounts (a false negative here permanently drops the sandbox).

    Preferred: a PATH-resolved ``true`` living outside the masked roots — no FHS assumption
    (NixOS keeps it in the store, not /bin). Fallback: our own interpreter (guaranteed to
    exist on any host running this code), ro-binding its masked directory(ies) back exactly
    as the real Magma invocation does — an ephemeral /tmp virtualenv python would otherwise
    be hidden by ``--tmpfs /tmp`` and misclassify a working bwrap as broken. ``true``
    ignores the ``-c pass`` arguments, so one argv shape serves every fallback."""
    mounts = ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    flags = ["--unshare-pid", "--unshare-ipc", "--new-session", "--die-with-parent"]
    true = shutil.which("true")
    if true and not _exe_needs_unmasking(true):
        return [bwrap, *mounts, *flags, true]
    exe = sys.executable or true or "/bin/true"
    mounts += _masked_exe_binds(exe)
    return [bwrap, *mounts, *flags, exe, "-c", "pass"]


def _bwrap_functional(bwrap: str) -> bool:
    """Cached probe that the recipe actually works on this host: bwrap can be installed yet
    unable to create namespaces (unprivileged user namespaces disabled — common inside
    containers), in which case every sandboxed run would fail at launch instead of running
    unsandboxed-with-a-warning. Probed once per process with the real flag set
    (:func:`_probe_argv`)."""
    global _bwrap_ok
    if _bwrap_ok is None:
        try:
            proc = subprocess.run(
                _probe_argv(bwrap),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10.0,
            )
            _bwrap_ok = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _bwrap_ok = False
    return _bwrap_ok


def _resolve_sandbox() -> tuple[str, str | None]:
    """(state, bwrap path when state == "active")."""
    if os.environ.get(NO_SANDBOX_ENV, "") not in ("", "0"):
        return "disabled", None
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return "unavailable", None
    if not _bwrap_functional(bwrap):
        return "broken", None
    return "active", bwrap


def sandbox_state() -> str:
    """The sandbox policy for this process, as it will apply to execution passes.

    ``"active"``      — bwrap found and working, not opted out: execution passes are sandboxed.
    ``"disabled"``    — opted out via ``MAGMA_LSP_NO_SANDBOX``.
    ``"unavailable"`` — no ``bwrap`` on PATH (e.g. macOS).
    ``"broken"``      — bwrap present but cannot create a sandbox here (user namespaces
    disabled, e.g. in some containers). The last two run execution passes unsandboxed with a
    one-time warning.
    """
    return _resolve_sandbox()[0]


def _writable_dirs() -> list[str]:
    # realpath (not abspath): the bind and the cwd-visibility check must agree on the same
    # canonical path. Granting a symlink like /tmp/work -> /home/user/work must bind (and be
    # chdir-visible as) /home/user/work — which is what _resolved_cwd() also selects — else
    # the program lands on the read-only twin and its relative writes fail.
    out: list[str] = []
    for d in os.environ.get(SANDBOX_WRITABLE_ENV, "").split(":"):
        if not d:
            continue
        rp = os.path.realpath(d)
        if os.path.isdir(rp):
            out.append(rp)
        else:
            _warn_once(
                f"writable:{rp}",
                f"magma-lsp: {SANDBOX_WRITABLE_ENV} entry is not a directory, ignoring: {rp}",
            )
    return out


_MASKED_ROOTS = ("/tmp", "/dev")

# Well-known privileged control sockets. A read-only root leaves these connectable, and a
# container/VM daemon reached through one will mount and mutate arbitrary host paths on the
# caller's behalf — defeating the read-only filesystem entirely. We overmount each existing
# one with /dev/null (verified: connect() then fails). This is BEST-EFFORT defence in depth,
# not a complete boundary: shell-out and IPC/network are not blocked (Magma licensing forbids
# --unshare-net), so a privileged socket at a path we don't know to mask is still reachable.
_PRIVILEGED_SOCKETS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/run/podman/podman.sock",
    "/var/run/podman/podman.sock",
    "/run/containerd/containerd.sock",
    "/var/run/containerd/containerd.sock",
    "/run/crio/crio.sock",
    "/var/run/libvirt/libvirt-sock",
)


def _socket_masks() -> list[str]:
    """``--ro-bind /dev/null <sock>`` args for each privileged control socket that exists on
    the host, including the per-user rootless docker/podman sockets. Deduped by realpath so a
    ``/var/run -> /run`` symlink doesn't double-mount the same target."""
    import stat

    candidates = list(_PRIVILEGED_SOCKETS)
    uid = os.getuid()
    for xdg in (os.environ.get("XDG_RUNTIME_DIR"), f"/run/user/{uid}"):
        if xdg:
            candidates += [f"{xdg}/docker.sock", f"{xdg}/podman/podman.sock"]
    seen: set[str] = set()
    args: list[str] = []
    for path in candidates:
        try:
            real = os.path.realpath(path)
            if real in seen or not stat.S_ISSOCK(os.stat(real).st_mode):
                continue
        except OSError:
            continue  # absent / unreadable → nothing to mask
        seen.add(real)
        args += ["--ro-bind", "/dev/null", path]
    return args


def _under_masked_root(path: str) -> bool:
    """True iff ``path`` is *strictly beneath* a masked root (``/tmp/x``, ``/dev/shm/x``).

    Not the roots themselves, not ancestors like ``/``, not ``/proc`` — only real
    subdirectories where user files can live behind a mask."""
    return any(path.startswith(r + "/") for r in _MASKED_ROOTS)


def _resolved_cwd(cwd: str) -> str:
    """The cwd to ``--chdir`` into inside the sandbox: fully symlink-resolved.

    Resolving lets a symlink that crosses OUT of a mask (``/tmp/project ->
    /home/user/project``) chdir into the visible target instead of the tmpfs-hidden symlink
    path (which would fail ``--chdir`` before Magma starts), while a symlink pointing INTO a
    mask still resolves to a hidden path and is handled by the caller's visibility check."""
    return os.path.realpath(cwd)


def _exe_needs_unmasking(exe: str | None) -> bool:
    """True iff the recipe's masking mounts would hide ``exe`` (its dir is a masked root or
    strictly beneath one). Used to prefer an *unmasked* probe command."""
    if not exe:
        return False
    for p in (exe, os.path.realpath(exe)):
        d = os.path.dirname(os.path.abspath(p))
        if d in _MASKED_ROOTS or _under_masked_root(d):
            return True
    return False


def _masked_exe_binds(exe: str | None) -> list[str]:
    """``--ro-bind`` args that keep an executable the masking mounts would otherwise hide
    (Magma, or the probe's fallback interpreter) reachable inside the sandbox.

    Bind the executable's *directory* when it is strictly beneath a masked root
    (``/tmp/inst``, ``/dev/shm/x``) — that exposes the wrapper's sibling files without
    touching the rest of the root. But when the executable sits *directly* at a masked root
    (``/tmp/magma``, ``/dev/magma``), bind only the FILE: re-binding the whole ``/tmp`` /
    ``/dev`` would undo the throwaway tmpfs / fresh devfs and re-expose the host tree. Both
    the invoked path and its realpath are covered (a /tmp symlink to a persistent install)."""
    if not exe:
        return []
    binds: list[str] = []
    seen: set[tuple[str, str]] = set()
    for p in (exe, os.path.realpath(exe)):
        ap = os.path.abspath(p)
        d = os.path.dirname(ap)
        if _under_masked_root(d):
            key = ("dir", d)
            if key not in seen:
                seen.add(key)
                binds += ["--ro-bind", d, d]
        elif d in _MASKED_ROOTS:
            key = ("file", ap)
            if key not in seen:
                seen.add(key)
                binds += ["--ro-bind", ap, ap]
        # else: dir not masked (e.g. /opt/magma) — visible via the read-only root, no bind
    return binds


def _sandbox_argv(source_path: str, cwd: str | None, magma: str | None = None) -> list[str]:
    """The bwrap prefix for one execution pass, or ``[]`` when the sandbox is off.

    Mount order matters (later mounts shadow earlier ones):
    1. the whole filesystem read-only;
    2. fresh /dev and /proc — BEFORE any bind that could live beneath them: with
       ``TMPDIR=/dev/shm`` the temp source (and a cwd/writable dir) can sit under /dev, and
       mounting /dev afterwards would hide it, failing every sandboxed run;
    3. a throwaway tmpfs over /tmp (hides host /tmp; gives the program scratch space);
    4. the Magma executable's directory(ies), when they sit under a masked mount
       (:func:`_masked_exe_binds` — else bwrap could not exec Magma at all; only the file
       is bound when the executable sits directly at a masked root, never the whole root);
    5. user-designated writable dirs (may deliberately override a read-only view);
    6. the temp source file read-only, so it stays read-only even inside a writable dir;
    7. /dev/null over each present privileged daemon socket LAST (:func:`_socket_masks`) —
       a reachable docker/podman/… socket is a host-mutation channel that survives the
       read-only root, and being last it can't be re-exposed by a writable dir that happens
       to contain it.

    The caller's cwd is NOT rebound (only ``--chdir``\\ ed into when visible): binding it
    repeatedly proved able to re-expose masked mounts, and relative ``load``\\ s already
    resolve through the read-only root. No bind here ever replaces a masked root wholesale.

    ``--unshare-net`` must never be added (breaks Magma licensing — see module comment).
    """
    state, bwrap = _resolve_sandbox()
    if state == "disabled":
        return []
    if state == "unavailable":
        _warn_once(
            "no-bwrap",
            "magma-lsp: bwrap not found — Magma EXECUTION passes run WITHOUT the OS sandbox "
            "(file writes by executed code are not blocked). Install bubblewrap to enable it, "
            f"or set {NO_SANDBOX_ENV}=1 to accept and silence this warning.",
        )
        return []
    if state == "broken":
        _warn_once(
            "broken-bwrap",
            "magma-lsp: bwrap is installed but cannot create a sandbox on this host "
            "(unprivileged user namespaces disabled? common inside containers) — Magma "
            "EXECUTION passes run WITHOUT the OS sandbox (file writes by executed code are "
            f"not blocked). Set {NO_SANDBOX_ENV}=1 to accept and silence this warning.",
        )
        return []
    argv = [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
    ]
    argv += _masked_exe_binds(magma)
    writable = _writable_dirs()
    for d in writable:
        argv += ["--bind", d, d]
    argv += ["--ro-bind", source_path, source_path]
    # Socket masks come LAST among the binds so a caller-designated writable dir that contains
    # (or is an ancestor of) a privileged socket can't re-expose it over the /dev/null mask.
    argv += _socket_masks()
    argv += ["--unshare-pid", "--unshare-ipc", "--new-session", "--die-with-parent"]
    if cwd:
        # We do NOT re-bind the caller's cwd. Reproducing it with a bind repeatedly proved
        # able to re-expose masked mounts (PR #14 rounds 3/6/7/8/10) and buys little: relative
        # `load`s resolve through the read-only root, which already exposes every non-masked
        # directory. chdir into the resolved cwd when it is visible inside the sandbox — i.e.
        # not hidden by a mask, OR covered by an explicit writable bind (the escape hatch's
        # whole point is that the program runs there) — else fall back to "/". Consequence
        # (BY DESIGN): a source strictly under a masked root (/tmp, /dev) with no writable
        # grant cannot resolve a relative sibling `load`; use a normal path or absolute loads.
        cwd = _resolved_cwd(cwd)
        visible = not _under_masked_root(cwd) or any(
            cwd == w or cwd.startswith(w.rstrip("/") + "/") for w in writable
        )
        argv += ["--chdir", cwd if visible else "/"]
    return argv


def sane_timeout(timeout: float, default: float = 10.0) -> float:
    """Coerce a caller-supplied timeout to a positive finite wall clock.

    GNU ``timeout`` parses a negative duration as an option and exits 125 WITHOUT running
    Magma — an empty diagnostic list that reads as a false "OK" — and treats 0 as "no limit";
    NaN/inf are invalid durations. Anything unusable becomes ``default``.
    """
    try:
        v = float(timeout)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v) or v <= 0:
        return default
    return v


def run_source(
    source: str,
    *,
    timeout: float = 10.0,
    magma_path: str | None = None,
    preamble: str = SERVER_PREAMBLE,
    cwd: str | None = None,
    sandbox: bool = False,
) -> MagmaResult:
    """Run ``preamble + source`` in a fresh Magma process and return combined output.

    ``cwd`` sets the subprocess working directory: Magma resolves relative ``load`` paths
    against the *process cwd* (verified on 2.29-9), not the script's location, so callers
    checking a file that load-s siblings should pass that file's directory.

    ``sandbox=True`` marks this call as *executing user code*: when the sandbox policy allows
    (:func:`sandbox_state` — bwrap present, not opted out), the process runs inside bubblewrap
    with the filesystem read-only. `timeout` stays OUTSIDE bwrap so the wall clock kills the
    whole tree; ``--die-with-parent`` handles the inner side.
    """
    timeout = sane_timeout(timeout)
    magma = find_magma(magma_path)
    if magma is None:
        raise FileNotFoundError("Magma executable not found (set magmaPath or put `magma` on PATH)")

    tmpdir = tempfile.gettempdir()
    path = os.path.join(tmpdir, f"magma-lsp-{uuid.uuid4().hex}.m")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(preamble)
        fh.write(source)
        if not source.endswith("\n"):
            fh.write("\n")
    try:
        wrap = _sandbox_argv(path, cwd, magma) if sandbox else []
        argv = ["timeout", str(timeout), *wrap, magma, "-b", "-n", path]
        try:
            proc = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout + 5.0,
                start_new_session=True,
                cwd=cwd,
            )
        except FileNotFoundError:
            # No `timeout` binary; fall back to subprocess-level timeout only.
            proc = subprocess.run(
                [*wrap, magma, "-b", "-n", path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                start_new_session=True,
                cwd=cwd,
            )
        out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
        # `timeout` exits 124 when it had to kill the child.
        return MagmaResult(
            stdout=out,
            returncode=proc.returncode,
            timed_out=proc.returncode == 124,
            source_path=path,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
        return MagmaResult(stdout=out, returncode=124, timed_out=True, source_path=path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
