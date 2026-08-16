"""End-to-end LSP handshake over stdio: initialize -> didOpen -> pushed diagnostics.

Spawns the server as a subprocess and speaks raw JSON-RPC, proving the Claude Code <-> server
channel and the auto-pushed diagnostics path. Magma diagnostics are disabled so the test is
hermetic (only the static unused-variable lint runs).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time


def _frame(obj: dict) -> bytes:
    body = json.dumps(obj).encode()
    return b"Content-Length: %d\r\n\r\n%s" % (len(body), body)


def _read(stream) -> dict:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line in (b"\r\n", b""):
            break
        k, _, v = line.decode().partition(":")
        headers[k.strip().lower()] = v.strip()
    n = int(headers["content-length"])
    return json.loads(stream.read(n))


def test_initialize_and_diagnostics_push():
    proc = subprocess.Popen(
        [sys.executable, "-m", "magma_lsp.server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "processId": None,
                        "rootUri": None,
                        "capabilities": {},
                        "initializationOptions": {"magmaDiagnostics": False, "lints": True},
                    },
                }
            )
        )
        proc.stdin.flush()
        resp = _read(proc.stdout)
        caps = resp["result"]["capabilities"]
        for provider in (
            "hoverProvider",
            "completionProvider",
            "signatureHelpProvider",
            "definitionProvider",
            "documentSymbolProvider",
        ):
            assert provider in caps, f"missing {provider}"

        proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "initialized", "params": {}}))
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": "file:///tmp/t.magma",
                            "languageId": "magma",
                            "version": 1,
                            "text": "f := function(n)\n    dead := 1;\n    return n;\n"
                            "end function;\n",
                        }
                    },
                }
            )
        )
        proc.stdin.flush()

        diags = None
        for _ in range(8):
            msg = _read(proc.stdout)
            if msg.get("method") == "textDocument/publishDiagnostics":
                diags = msg["params"]["diagnostics"]
                break
        assert diags is not None
        assert any("dead" in d["message"] and "never used" in d["message"] for d in diags)

        proc.stdin.write(_frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None}))
        proc.stdin.flush()
        _read(proc.stdout)
        proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "exit", "params": None}))
        proc.stdin.flush()
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_workspace_definition_and_completion(tmp_path):
    """rootUri workspace scan feeds definition (list of locations) and completion:
    a project-defined intrinsic in a sibling file is a jump target (issue #16)."""
    (tmp_path / "helper.m").write_text(
        "intrinsic MyProjHelper(x::RngIntElt) -> RngIntElt\n"
        "{Adds one}\n  return x + 1;\nend intrinsic;\n"
    )
    main_uri = f"file://{tmp_path}/main.magma"
    proc = subprocess.Popen(
        [sys.executable, "-m", "magma_lsp.server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    def request(rid: int, method: str, params) -> dict:
        proc.stdin.write(_frame({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        proc.stdin.flush()
        while True:  # skip interleaved notifications (publishDiagnostics, logs)
            msg = _read(proc.stdout)
            if msg.get("id") == rid:
                return msg

    try:
        request(
            1,
            "initialize",
            {
                "processId": None,
                "rootUri": f"file://{tmp_path}",
                "capabilities": {},
                "initializationOptions": {"magmaDiagnostics": False, "lints": False},
            },
        )
        proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "initialized", "params": {}}))
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": main_uri,
                            "languageId": "magma",
                            "version": 1,
                            "text": "y := MyProjHelper(1);\n",
                        }
                    },
                }
            )
        )
        proc.stdin.flush()

        # the workspace scan runs in a background thread after `initialized`: poll
        locs = None
        for rid in range(10, 40):
            resp = request(
                rid,
                "textDocument/definition",
                {"textDocument": {"uri": main_uri}, "position": {"line": 0, "character": 8}},
            )
            if resp.get("result"):
                locs = resp["result"]
                break
            time.sleep(0.25)
        assert locs, "definition of the project intrinsic never resolved"
        assert isinstance(locs, list) and locs[0]["uri"].endswith("helper.m")

        resp = request(
            50,
            "workspace/symbol",
            {"query": "myproj"},
        )
        assert any(s["name"] == "MyProjHelper" for s in resp["result"] or [])

        resp = request(
            51,
            "textDocument/completion",
            {"textDocument": {"uri": main_uri}, "position": {"line": 0, "character": 9}},
        )
        items = resp["result"]["items"] if isinstance(resp["result"], dict) else resp["result"]
        assert any(i["label"] == "MyProjHelper" for i in items)
    finally:
        if proc.poll() is None:
            proc.kill()
