"""End-to-end LSP handshake over stdio: initialize -> didOpen -> pushed diagnostics.

Spawns the server as a subprocess and speaks raw JSON-RPC, proving the Claude Code <-> server
channel and the auto-pushed diagnostics path. Magma diagnostics are disabled so the test is
hermetic (only the static unused-variable lint runs).
"""

from __future__ import annotations

import json
import subprocess
import sys


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
