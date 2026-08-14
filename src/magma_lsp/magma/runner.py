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


def _bwrap_functional(bwrap: str) -> bool:
    """Cached probe that the recipe actually works on this host: bwrap can be installed yet
    unable to create namespaces (unprivileged user namespaces disabled — common inside
    containers), in which case every sandboxed run would fail at launch instead of running
    unsandboxed-with-a-warning. Probed once per process with the real flag set.

    The probed command is our own interpreter — guaranteed to exist and be executable on any
    host that is running this code, with no FHS assumption (`/bin/true` does not exist on
    e.g. NixOS, and a missing probe command would misclassify a working bwrap as broken,
    silently dropping the sandbox). `true` ignores the ``-c pass`` arguments, so the one
    argv shape also serves the no-``sys.executable`` fallback."""
    global _bwrap_ok
    if _bwrap_ok is None:
        exe = sys.executable or shutil.which("true") or "/bin/true"
        try:
            proc = subprocess.run(
                [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                 "--tmpfs", "/tmp", "--unshare-pid", "--unshare-ipc", "--new-session",
                 "--die-with-parent", exe, "-c", "pass"],
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
    out: list[str] = []
    for d in os.environ.get(SANDBOX_WRITABLE_ENV, "").split(":"):
        if not d:
            continue
        ab = os.path.abspath(d)
        if os.path.isdir(ab):
            out.append(ab)
        else:
            _warn_once(
                f"writable:{ab}",
                f"magma-lsp: {SANDBOX_WRITABLE_ENV} entry is not a directory, ignoring: {ab}",
            )
    return out


_MASKED_ROOTS = ("/tmp", "/dev")


def _masked_magma_dirs(magma: str | None) -> list[str]:
    """Directories of the Magma executable that the recipe's masking mounts would hide.

    A Magma install — or a wrapper/symlink to one — under /tmp or /dev would vanish behind
    ``--tmpfs /tmp`` / ``--dev /dev`` and every sandboxed run would fail at exec; ro-bind
    those directories back. Both the invoked path and its realpath matter (a /tmp symlink
    to a persistent install must stay resolvable inside the sandbox)."""
    if not magma:
        return []
    out: list[str] = []
    for p in (magma, os.path.realpath(magma)):
        d = os.path.dirname(os.path.abspath(p))
        if any(d == r or d.startswith(r + "/") for r in _MASKED_ROOTS) and d not in out:
            out.append(d)
    return out


def _sandbox_argv(source_path: str, cwd: str | None, magma: str | None = None) -> list[str]:
    """The bwrap prefix for one execution pass, or ``[]`` when the sandbox is off.

    Mount order matters (later mounts shadow earlier ones):
    1. the whole filesystem read-only;
    2. fresh /dev and /proc — BEFORE any bind that could live beneath them: with
       ``TMPDIR=/dev/shm`` the temp source (and a cwd/writable dir) can sit under /dev, and
       mounting /dev afterwards would hide it, failing every sandboxed run;
    3. a throwaway tmpfs over /tmp (hides host /tmp; gives the program scratch space);
    4. the Magma executable's directory(ies), when they sit under a masked mount
       (:func:`_masked_magma_dirs` — else bwrap could not exec Magma at all);
    5. ``cwd`` read-only again — so a program under /tmp (or /dev/shm) keeps its own
       directory (relative ``load``\\ s) visible through the masking mounts;
    6. user-designated writable dirs (may deliberately override cwd's read-only view);
    7. the temp source file read-only LAST, so it stays read-only even inside a writable dir.

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
    for mdir in _masked_magma_dirs(magma):
        argv += ["--ro-bind", mdir, mdir]
    if cwd:
        cwd = os.path.abspath(cwd)
        argv += ["--ro-bind", cwd, cwd]
    for d in _writable_dirs():
        argv += ["--bind", d, d]
    argv += [
        "--ro-bind", source_path, source_path,
        "--unshare-pid",
        "--unshare-ipc",
        "--new-session",
        "--die-with-parent",
    ]
    if cwd:
        argv += ["--chdir", cwd]
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
