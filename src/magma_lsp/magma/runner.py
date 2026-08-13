"""Sandboxed invocation of a real Magma process.

Every call follows the golden recipe verified in CLAUDE.md §3:

    timeout <T> magma -b -n <tempfile> </dev/null 2>&1

- ``-b`` batch (no banner), ``-n`` no startup file (hermetic).
- ``</dev/null`` is mandatory: without it Magma can block forever reading stdin after some
  runtime errors. We pass ``stdin=DEVNULL``.
- external ``timeout`` is the hard wall-clock backstop (kills the process group); a slightly
  larger ``subprocess`` timeout is a second backstop.
- a fresh process per call (cold start ~104 ms), never reused.
"""

from __future__ import annotations

import contextlib
import math
import os
import shutil
import subprocess
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
        resolved = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if resolved:
            return resolved
    return None


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
) -> MagmaResult:
    """Run ``preamble + source`` in a fresh Magma process and return combined output.

    ``cwd`` sets the subprocess working directory: Magma resolves relative ``load`` paths
    against the *process cwd* (verified on 2.29-9), not the script's location, so callers
    checking a file that load-s siblings should pass that file's directory.
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
        argv = ["timeout", str(timeout), magma, "-b", "-n", path]
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
                [magma, "-b", "-n", path],
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
