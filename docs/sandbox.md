# The execution sandbox, in detail

This page holds the full details behind the README's short "Execution sandbox" section: what
is and is not blocked, why, and the environment variables that control it. It matters mostly
to people **hosting** the MCP/LSP server for others (or pointing an untrusted agent at it);
for ordinary interactive use the README summary is all you need.

## What every run gets (sandbox or not)

Every `run`/`check` is a **fresh, hermetic Magma process**: no startup file (`-n`), batch
mode, stdin closed, a hard wall-clock `timeout`, and an in-process `SetMemoryLimit`. Output
capture is bounded, so a program printing until the wall clock cannot exhaust the server's
memory. A fresh process per call is cheap (~11 ms cold start) and means no state leaks
between checks.

## What bubblewrap adds

The passes that actually **execute** user code — `magma_run`, `magma_check(execute=True)`,
and the CLI equivalents — additionally run inside a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox whenever `bwrap` is on PATH:

- the **entire filesystem is remounted read-only**, with `/tmp` replaced by a throwaway
  tmpfs (scratch space that vanishes with the process);
- the read-only remount is **recursive**: separately-mounted writable filesystems (a
  separate `/home`, the `/run/user/<uid>` tmpfs, ...) are covered too — bubblewrap remounts
  inherited submounts read-only, using `mount_setattr` on kernels ≥ 5.12 — and the test
  suite asserts this against a real submount of the host it runs on;
- fresh **PID and IPC namespaces** and no controlling terminal;
- well-known **privileged control sockets** (Docker, Podman, containerd, CRI-O, libvirt,
  including the rootless per-user ones) are masked with `/dev/null` where present, since a
  container daemon reached through one would mutate host paths on the caller's behalf and
  defeat the read-only root.

The parse-only diagnostics passes (the never-called-function wrap and the `Attach` strategy)
execute nothing user-level and are **not** sandboxed, which keeps the every-edit syntax
check at its measured ~12.5 ms.

## What is deliberately NOT blocked, and why

Precisely stated: the sandbox blocks **filesystem mutation** through the normal filesystem —
the worst vector — but does **not** block `System(...)`/`Pipe(...)` shell-out per se and
does **not** block network egress.

The network namespace must stay shared because **Magma's license check reads the host MAC
address** — an unshared network namespace makes licensing fail ("This host has the following
MAC address(es): <empty>"). A shell can therefore still be spawned, but it runs against the
same read-only filesystem.

The socket masking is best-effort defence in depth, not a complete boundary: because
IPC/network isn't blocked, a privileged daemon socket at a path we don't know to mask
remains reachable. **Treat the sandbox as preventing casual and accidental filesystem writes
by generated code, not as a hardened boundary against code actively trying to escape.**

## `load` semantics inside the sandbox

Relative `load`s resolve through the read-only root, which exposes every directory except
the masked ones — so a source at a normal path loads its siblings fine. A source anywhere
**under `/tmp` or `/dev`** cannot load a dependency that is *also* under a masked root: the
throwaway tmpfs/devfs hides it whether it's referenced relatively or by absolute path (only
the generated source file is bound back), and the sandbox deliberately never re-binds a
caller-controlled directory over the masks. Keep the source and its `load` dependencies at a
normal path, or grant their directory via `MAGMA_LSP_SANDBOX_WRITABLE` (an absolute `load`
of a file that already lives outside the masked roots works from anywhere).

## Policy & knobs

- **On automatically** when `bwrap` is present and working. A one-time probe detects hosts
  where bwrap exists but cannot create namespaces (unprivileged user namespaces disabled,
  common inside containers) and falls back to unsandboxed-with-a-warning rather than failing
  every run.
- `MAGMA_LSP_NO_SANDBOX=1` in the server's environment opts out (and silences the warning).
- Without (working) bwrap — e.g. macOS, untested anyway — execution passes run unsandboxed
  and a loud one-time warning on stderr says so.
- `MAGMA_LSP_SANDBOX_WRITABLE=/path/a:/path/b` grants specific directories read-write
  (bind-mounted), for programs that legitimately write output files. Unset by default.
- `magma_guide()` reports the live sandbox state, and the `magma_run`/`magma_check` tool
  docs tell the agent up front that writes will fail.
