"""Magma language server (pygls).

Wires the signature DB, the tree-sitter analysis, and the Magma-backed validation into the LSP
operations Claude Code uses (CLAUDE.md §9): pushed diagnostics after edits, plus on-demand hover,
completion, signature help, go-to-definition, and document symbols.

Diagnostics strategy:
- fast pass on every change: unused-variable lints (+ tree-sitter syntax errors when Magma is off).
- full pass on open/save: Magma syntax/binding check (authoritative) + lints.
"""

from __future__ import annotations

import logging
import os
import re

from lsprotocol import types as t
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path

from . import __version__
from .analysis.lints import unused_variables
from .analysis.symbols import Symbol, document_symbols
from .analysis.undefined import undefined_intrinsics
from .analysis.workspace import scan_workspace
from .db.index import SignatureIndex
from .db.model import Signature
from .db.store import newest_cached_db
from .magma.runner import find_magma
from .magma.validate import syntax_check
from .parsing import new_parser

logger = logging.getLogger("magma_lsp")

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class MagmaLanguageServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("magma-lsp", __version__)
        self.index: SignatureIndex | None = None
        self.magma_path: str | None = None
        self.magma_available: bool = False
        self.enable_magma_diagnostics: bool = True
        self.enable_lints: bool = True
        self.enable_unknown_intrinsics: bool = True
        self.magma_timeout: float = 10.0
        self.intrinsic_names: frozenset[str] = frozenset()
        # Names defined across the project's own .m files (sibling helpers); see analysis/workspace.
        self.enable_workspace_symbols: bool = True
        self.workspace_max_files: int = 2000
        self.workspace_roots: list[str] = []
        self.workspace_symbols: frozenset[str] = frozenset()

    def configure(self, init_options: dict | None) -> None:
        opts = init_options or {}
        self.magma_path = opts.get("magmaPath")
        self.enable_magma_diagnostics = opts.get("magmaDiagnostics", True)
        self.enable_lints = opts.get("lints", True)
        self.enable_unknown_intrinsics = opts.get("unknownIntrinsics", True)
        self.enable_workspace_symbols = opts.get("workspaceSymbols", True)
        self.workspace_max_files = int(opts.get("workspaceMaxFiles", 2000))
        self.magma_timeout = float(opts.get("magmaTimeout", 10.0))
        self.magma_available = find_magma(self.magma_path) is not None

        db_path = opts.get("dbPath") or newest_cached_db()
        if db_path:
            try:
                self.index = SignatureIndex.from_path(db_path)
                self.intrinsic_names = frozenset(self.index.db.intrinsics)
                logger.info("loaded signature DB %s (%d names)", db_path, len(self.intrinsic_names))
            except Exception as exc:
                logger.warning("failed to load signature DB %s: %s", db_path, exc)
        else:
            logger.warning(
                "no signature DB found; run `magma-lsp-build-db`. "
                "Hover/completion/definition will be limited until then."
            )

    def rescan_workspace(self) -> None:
        if not (self.enable_workspace_symbols and self.enable_unknown_intrinsics):
            return
        roots = list(self.workspace_roots)
        if not roots:
            return
        try:
            scan = scan_workspace(roots, max_files=self.workspace_max_files)
        except Exception as exc:  # never let a scan crash the server
            logger.warning("workspace scan failed: %s", exc)
            return
        self.workspace_symbols = scan.names
        if scan.truncated:
            logger.info(
                "workspace too large to scan (> %d .m files); skipping project-symbol scan",
                self.workspace_max_files,
            )
        else:
            logger.info(
                "workspace scan: %d names from %d files", len(scan.names), scan.files_scanned
            )

    def known_call_names(self) -> frozenset[str]:
        return self.intrinsic_names | self.workspace_symbols


server = MagmaLanguageServer()


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def _word_at(text: str, pos: t.Position) -> str | None:
    lines = text.splitlines()
    if pos.line >= len(lines):
        return None
    line = lines[pos.line]
    for m in WORD_RE.finditer(line):
        if m.start() <= pos.character <= m.end():
            return m.group(0)
    return None


def _prefix_at(text: str, pos: t.Position) -> str:
    lines = text.splitlines()
    if pos.line >= len(lines):
        return ""
    line = lines[pos.line][: pos.character]
    m = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", line)
    return m.group(0) if m else ""


def _enclosing_call_name(text: str, pos: t.Position) -> str | None:
    """The intrinsic name of the call the cursor is inside, via tree-sitter."""
    tree = new_parser().parse(text.encode("utf-8"))
    point = (pos.line, pos.character)
    node = tree.root_node.descendant_for_point_range(point, point)
    while node is not None:
        if node.type == "call":
            for c in node.children:
                if c.type == "identifier":
                    return c.text.decode("utf-8", "replace")
        node = node.parent
    return None


def _sig_label(sig: Signature) -> str:
    return sig.render()


# --------------------------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------------------------
@server.feature(t.INITIALIZE)
def on_initialize(ls: MagmaLanguageServer, params: t.InitializeParams):
    ls.configure(getattr(params, "initialization_options", None))
    ls.workspace_roots = _resolve_roots(params)


@server.feature(t.INITIALIZED)
def on_initialized(ls: MagmaLanguageServer, params: t.InitializedParams):
    # Scan the project for sibling-defined symbols *after* the handshake so it never delays it.
    ls.rescan_workspace()


@server.feature(t.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: MagmaLanguageServer, params: t.DidOpenTextDocumentParams):
    _publish(ls, params.text_document.uri, run_magma=True)


@server.feature(t.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: MagmaLanguageServer, params: t.DidChangeTextDocumentParams):
    _publish(ls, params.text_document.uri, run_magma=False)


@server.feature(t.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: MagmaLanguageServer, params: t.DidSaveTextDocumentParams):
    if params.text_document.uri.endswith((".m", ".magma")):
        ls.rescan_workspace()  # a saved definition may now satisfy sibling calls
    _publish(ls, params.text_document.uri, run_magma=True)


def _resolve_roots(params: t.InitializeParams) -> list[str]:
    roots: list[str] = []
    folders = getattr(params, "workspace_folders", None)
    if folders:
        for folder in folders:
            path = to_fs_path(folder.uri)
            if path:
                roots.append(path)
    root_uri = getattr(params, "root_uri", None)
    if root_uri:
        path = to_fs_path(root_uri)
        if path and path not in roots:
            roots.append(path)
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root and env_root not in roots:
        roots.append(env_root)
    return roots


@server.feature(t.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: MagmaLanguageServer, params: t.DidCloseTextDocumentParams):
    ls.text_document_publish_diagnostics(
        t.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[])
    )


# --------------------------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------------------------
def _publish(ls: MagmaLanguageServer, uri: str, *, run_magma: bool) -> None:
    try:
        doc = ls.workspace.get_text_document(uri)
        text = doc.source
    except Exception:
        return
    diagnostics = _compute_diagnostics(ls, text, run_magma=run_magma)
    ls.text_document_publish_diagnostics(
        t.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
    )


def _compute_diagnostics(
    ls: MagmaLanguageServer, text: str, *, run_magma: bool
) -> list[t.Diagnostic]:
    diags: list[t.Diagnostic] = []

    use_magma = run_magma and ls.enable_magma_diagnostics and ls.magma_available
    if use_magma:
        try:
            res = syntax_check(text, magma_path=ls.magma_path, timeout=ls.magma_timeout)
            for d in res.diagnostics:
                line0 = max(0, d.line - 1)
                col0 = max(0, d.col - 1)
                diags.append(
                    t.Diagnostic(
                        range=t.Range(
                            start=t.Position(line0, col0),
                            end=t.Position(line0, col0 + 1),
                        ),
                        message=d.message,
                        severity=t.DiagnosticSeverity.Error
                        if d.severity == "error"
                        else t.DiagnosticSeverity.Warning,
                        source="magma",
                    )
                )
        except Exception as exc:
            logger.warning("magma syntax check failed: %s", exc)
    else:
        diags.extend(_tree_sitter_syntax_errors(text))
        # Static undefined-intrinsic check: the fast/offline complement to Magma's binding pass.
        # Skipped when the Magma pass ran above (it is authoritative for undefined names).
        if ls.enable_unknown_intrinsics and ls.intrinsic_names:
            for lint in undefined_intrinsics(text, ls.known_call_names()):
                diags.append(_lint_diagnostic(lint))

    if ls.enable_lints:
        for lint in unused_variables(text):
            diags.append(_lint_diagnostic(lint))
    return diags


def _lint_diagnostic(lint) -> t.Diagnostic:
    return t.Diagnostic(
        range=t.Range(
            start=t.Position(lint.line, lint.col),
            end=t.Position(lint.end_line, lint.end_col),
        ),
        message=lint.message,
        severity=t.DiagnosticSeverity.Warning
        if lint.severity == "warning"
        else t.DiagnosticSeverity.Hint,
        source="magma-lsp",
        tags=[t.DiagnosticTag.Unnecessary] if lint.unnecessary else None,
    )


def _tree_sitter_syntax_errors(text: str) -> list[t.Diagnostic]:
    tree = new_parser().parse(text.encode("utf-8"))
    if not tree.root_node.has_error:
        return []
    out: list[t.Diagnostic] = []
    stack = [tree.root_node]
    while stack and len(out) < 50:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            sr, sc = node.start_point
            er, ec = node.end_point
            if (er, ec) == (sr, sc):
                ec = sc + 1
            out.append(
                t.Diagnostic(
                    range=t.Range(t.Position(sr, sc), t.Position(er, ec)),
                    message="Syntax error" if node.type == "ERROR" else "Missing syntax",
                    severity=t.DiagnosticSeverity.Error,
                    source="magma-lsp",
                )
            )
            continue
        stack.extend(node.children)
    return out


# --------------------------------------------------------------------------------------------
# on-demand intelligence
# --------------------------------------------------------------------------------------------
@server.feature(t.TEXT_DOCUMENT_HOVER)
def hover(ls: MagmaLanguageServer, params: t.HoverParams) -> t.Hover | None:
    if ls.index is None:
        return None
    text = ls.workspace.get_text_document(params.text_document.uri).source
    word = _word_at(text, params.position)
    if not word:
        return None
    md = ls.index.hover_markdown(word)
    if not md:
        return None
    return t.Hover(contents=t.MarkupContent(kind=t.MarkupKind.Markdown, value=md))


@server.feature(t.TEXT_DOCUMENT_COMPLETION, t.CompletionOptions(trigger_characters=["."]))
def completion(ls: MagmaLanguageServer, params: t.CompletionParams) -> t.CompletionList:
    if ls.index is None:
        return t.CompletionList(is_incomplete=False, items=[])
    text = ls.workspace.get_text_document(params.text_document.uri).source
    prefix = _prefix_at(text, params.position)
    items: list[t.CompletionItem] = []
    for name in ls.index.complete(prefix):
        intr = ls.index.lookup(name)
        detail = intr.signatures[0].render() if intr and intr.signatures else None
        items.append(
            t.CompletionItem(
                label=name,
                kind=t.CompletionItemKind.Function,
                detail=detail,
                documentation=(
                    t.MarkupContent(kind=t.MarkupKind.Markdown, value=intr.first_doc)
                    if intr and intr.first_doc
                    else None
                ),
            )
        )
    return t.CompletionList(is_incomplete=len(items) >= 200, items=items)


@server.feature(
    t.TEXT_DOCUMENT_SIGNATURE_HELP, t.SignatureHelpOptions(trigger_characters=["(", ","])
)
def signature_help(
    ls: MagmaLanguageServer, params: t.SignatureHelpParams
) -> t.SignatureHelp | None:
    if ls.index is None:
        return None
    text = ls.workspace.get_text_document(params.text_document.uri).source
    name = _enclosing_call_name(text, params.position)
    if not name:
        return None
    sigs = ls.index.signatures(name)
    if not sigs:
        return None
    infos = [
        t.SignatureInformation(
            label=_sig_label(s),
            documentation=(
                t.MarkupContent(kind=t.MarkupKind.Markdown, value=s.doc) if s.doc else None
            ),
            parameters=[
                t.ParameterInformation(label=f"{a.name}::{a.type}" if a.type else a.name)
                for a in s.args
            ],
        )
        for s in sigs[:25]
    ]
    return t.SignatureHelp(signatures=infos, active_signature=0, active_parameter=0)


@server.feature(t.TEXT_DOCUMENT_DEFINITION)
def definition(ls: MagmaLanguageServer, params: t.DefinitionParams) -> t.Location | None:
    if ls.index is None:
        return None
    text = ls.workspace.get_text_document(params.text_document.uri).source
    word = _word_at(text, params.position)
    if not word:
        return None
    loc = ls.index.definition(word)
    if loc is None or not loc.file:
        return None
    line0 = max(0, loc.line - 1)
    col0 = max(0, loc.col - 1)
    return t.Location(
        uri=from_fs_path(loc.file),
        range=t.Range(t.Position(line0, col0), t.Position(line0, col0 + len(word))),
    )


_SYMBOL_KIND = {
    "intrinsic": t.SymbolKind.Function,
    "function": t.SymbolKind.Function,
    "procedure": t.SymbolKind.Method,
    "type": t.SymbolKind.Struct,
}


@server.feature(t.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(
    ls: MagmaLanguageServer, params: t.DocumentSymbolParams
) -> list[t.DocumentSymbol]:
    text = ls.workspace.get_text_document(params.text_document.uri).source
    out: list[t.DocumentSymbol] = []
    for s in document_symbols(text):
        out.append(_to_document_symbol(s))
    return out


@server.feature(t.WORKSPACE_SYMBOL)
def workspace_symbol(
    ls: MagmaLanguageServer, params: t.WorkspaceSymbolParams
) -> list[t.WorkspaceSymbol]:
    if ls.index is None:
        return []
    out: list[t.WorkspaceSymbol] = []
    for name, loc in ls.index.search_symbols(params.query):
        line0 = max(0, loc.line - 1)
        col0 = max(0, loc.col - 1)
        out.append(
            t.WorkspaceSymbol(
                name=name,
                kind=t.SymbolKind.Function,
                location=t.Location(
                    uri=from_fs_path(loc.file),
                    range=t.Range(t.Position(line0, col0), t.Position(line0, col0 + len(name))),
                ),
            )
        )
    return out


def _to_document_symbol(s: Symbol) -> t.DocumentSymbol:
    rng = t.Range(t.Position(s.line, s.col), t.Position(s.end_line, s.end_col))
    name_rng = t.Range(t.Position(s.line, s.col), t.Position(s.line, s.col + len(s.name)))
    return t.DocumentSymbol(
        name=s.name,
        kind=_SYMBOL_KIND.get(s.kind, t.SymbolKind.Variable),
        range=rng,
        selection_range=name_rng,
        detail=s.detail,
    )


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="magma-lsp")
    ap.add_argument("--stdio", action="store_true", help="communicate over stdio (default)")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    server.start_io()


if __name__ == "__main__":
    main()
