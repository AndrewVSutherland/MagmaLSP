"""Magma language server (pygls).

Wires the signature DB, the tree-sitter analysis, and the Magma-backed validation into the LSP
operations Claude Code uses (CLAUDE.md §9): pushed diagnostics after edits, plus on-demand hover,
completion, signature help, go-to-definition, and document symbols.

Diagnostics strategy:
- static pass on every change (fast, no Magma process): pitfall lints (`=` vs `:=`, `==`,
  method-call syntax, ...), unknown-intrinsic check with did-you-mean suggestions, arity check,
  unused-variable lints.
- plus the Magma syntax/binding pass on open/save (authoritative for syntax; it picks a safe
  strategy per file shape — see magma/validate.py). Its undefined-name reports are filtered
  against workspace-defined symbols so multi-file projects don't see phantom errors.

Document handlers run in pygls' thread pool (never on the event loop), and a publish is skipped
if the document changed while its diagnostics were being computed.
"""

from __future__ import annotations

import logging
import os
import re

from lsprotocol import types as t
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path

from . import __version__
from .analysis.arity import arity_problems
from .analysis.lints import unused_variables
from .analysis.pitfalls import pitfall_lints
from .analysis.scope import load_defined_symbols
from .analysis.symbols import Symbol, document_symbols
from .analysis.undefined import undefined_intrinsics
from .analysis.workspace import ScanCache, scan_workspace
from .db.index import SignatureIndex
from .db.model import Signature
from .db.store import best_cached_db
from .frontend import installed_magma_version
from .handbook import HandbookIndex
from .magma.diagnostics import MagmaDiagnostic
from .magma.runner import find_magma
from .magma.validate import syntax_check
from .parsing import new_parser

logger = logging.getLogger("magma_lsp")

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IDENT_IN_MSG_RE = re.compile(r"Identifier '([A-Za-z_][A-Za-z0-9_]*)'")


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
        self.ref_arg_names: frozenset[str] = frozenset()
        # Names defined across the project's own .m files (sibling helpers); see analysis/workspace.
        self.enable_workspace_symbols: bool = True
        self.workspace_max_files: int = 2000
        self.workspace_roots: list[str] = []
        self.workspace_symbols: frozenset[str] = frozenset()
        self._scan_cache: ScanCache = {}
        self.enable_handbook: bool = True
        self.handbook: HandbookIndex | None = None
        self._suggest_cache: dict[str, list[str]] = {}

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
        self.enable_handbook = opts.get("handbook", True)
        if self.enable_handbook:
            hb_dir = opts.get("handbookDir") or _default_handbook_dir(self.magma_path)
            if hb_dir and os.path.isdir(hb_dir):
                try:
                    self.handbook = HandbookIndex.load(hb_dir)
                    logger.info("loaded handbook index (%d names)", len(self.handbook.entries))
                except Exception as exc:
                    logger.warning("failed to load handbook index: %s", exc)

        db_path = opts.get("dbPath") or best_cached_db(installed_magma_version())
        if db_path:
            try:
                self.index = SignatureIndex.from_path(db_path)
                self.intrinsic_names = frozenset(self.index.db.intrinsics)
                self.ref_arg_names = self.index.ref_arg_names
                logger.info("loaded signature DB %s (%d names)", db_path, len(self.intrinsic_names))
                installed = installed_magma_version()
                if installed and self.index.version not in ("unknown", installed):
                    logger.warning(
                        "signature DB is for Magma %s but installed Magma is %s; "
                        "run magma-lsp-build-db to rebuild",
                        self.index.version,
                        installed,
                    )
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
            scan = scan_workspace(roots, max_files=self.workspace_max_files, cache=self._scan_cache)
        except Exception as exc:  # never let a scan crash the server
            logger.warning("workspace scan failed: %s", exc)
            return
        if scan.truncated:
            # keep whatever we had rather than wiping a previously good symbol set
            logger.info(
                "workspace too large to scan (> %d .m files); keeping previous project symbols",
                self.workspace_max_files,
            )
        else:
            self.workspace_symbols = scan.names
            logger.info(
                "workspace scan: %d names from %d files", len(scan.names), scan.files_scanned
            )

    def known_call_names(self) -> frozenset[str]:
        return self.intrinsic_names | self.workspace_symbols

    def suggest(self, name: str) -> list[str]:
        """Cached near-miss suggestions (suggestion computation is ~50 ms; cache per name)."""
        if self.index is None:
            return []
        hit = self._suggest_cache.get(name)
        if hit is None:
            hit = self.index.suggest(name, limit=3)
            if len(self._suggest_cache) > 4096:
                self._suggest_cache.clear()
            self._suggest_cache[name] = hit
        return hit


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


def _enclosing_call(text: str, pos: t.Position):
    """The innermost ``call`` node containing the cursor, via tree-sitter."""
    tree = new_parser().parse(text.encode("utf-8"))
    point = (pos.line, pos.character)
    node = tree.root_node.descendant_for_point_range(point, point)
    while node is not None:
        if node.type == "call":
            return node
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
@server.thread()
def on_initialized(ls: MagmaLanguageServer, params: t.InitializedParams):
    # Scan the project for sibling-defined symbols *after* the handshake so it never delays it.
    ls.rescan_workspace()


@server.feature(t.TEXT_DOCUMENT_DID_OPEN)
@server.thread()
def did_open(ls: MagmaLanguageServer, params: t.DidOpenTextDocumentParams):
    _publish(ls, params.text_document.uri, run_magma=True)


@server.feature(t.TEXT_DOCUMENT_DID_CHANGE)
@server.thread()
def did_change(ls: MagmaLanguageServer, params: t.DidChangeTextDocumentParams):
    _publish(ls, params.text_document.uri, run_magma=False)


@server.feature(t.TEXT_DOCUMENT_DID_SAVE)
@server.thread()
def did_save(ls: MagmaLanguageServer, params: t.DidSaveTextDocumentParams):
    if params.text_document.uri.endswith((".m", ".magma")):
        ls.rescan_workspace()  # cached: only the saved file is re-parsed
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


def _default_handbook_dir(magma_path: str | None) -> str | None:
    """Derive ``<install>/doc/html`` from the Magma wrapper location, else the known path."""
    resolved = find_magma(magma_path)
    if resolved:
        install = os.path.dirname(os.path.realpath(resolved))
        cand = os.path.join(install, "doc", "html")
        if os.path.isdir(cand):
            return cand
    return "/opt/magma/doc/html"


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
        version = doc.version
    except Exception:
        return
    base_dir = os.path.dirname(to_fs_path(uri) or "") or None
    try:
        diagnostics = _compute_diagnostics(ls, text, run_magma=run_magma, base_dir=base_dir)
    except Exception as exc:  # a broken pass must not freeze stale squiggles in the editor
        logger.warning("diagnostics computation failed: %s", exc)
        diagnostics = []
    try:
        if ls.workspace.get_text_document(uri).version != version:
            return  # superseded: a newer didChange will publish fresher results
    except Exception:
        return
    ls.text_document_publish_diagnostics(
        t.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics, version=version)
    )


def _compute_diagnostics(
    ls: MagmaLanguageServer, text: str, *, run_magma: bool, base_dir: str | None = None
) -> list[t.Diagnostic]:
    diags: list[t.Diagnostic] = []

    # names defined by load-ed files count as known; unresolved loads disable name checking
    loaded_names: set[str] = set()
    loads_unresolved = 0
    if "load" in text:
        loaded_names, loads_unresolved = load_defined_symbols(text, base_dir)

    # ---- static passes: always run (fast, offline, and they see ALL problems at once) ----
    static_undef_idents: dict[str, list[t.Diagnostic]] = {}
    if ls.enable_unknown_intrinsics and ls.intrinsic_names and not loads_unresolved:
        known = ls.known_call_names() | loaded_names
        for lint in undefined_intrinsics(text, known, suggest=ls.suggest):
            d = _lint_diagnostic(lint)
            m = re.match(r"'([^']+)'", lint.message)
            if m:
                static_undef_idents.setdefault(m.group(1), []).append(d)
            diags.append(d)

    if ls.enable_lints:
        for lint in pitfall_lints(
            text, intrinsic_names=ls.intrinsic_names, ref_arg_intrinsics=ls.ref_arg_names
        ):
            diags.append(_lint_diagnostic(lint))
        if ls.index is not None and not loads_unresolved:
            # (unresolved load -> the target could redefine any intrinsic with any arity,
            # so the pass is skipped entirely — same guard as the undefined-name pass)
            for lint in arity_problems(text, ls.index.arities):
                m = re.search(r"'([^']+)'", lint.message)
                if m and (m.group(1) in ls.workspace_symbols or m.group(1) in loaded_names):
                    continue  # a sibling/loaded file redefines the name; its arity differs
                diags.append(_lint_diagnostic(lint))
        for lint in unused_variables(text):
            diags.append(_lint_diagnostic(lint))

    # ---- Magma pass on open/save: authoritative for syntax ----
    use_magma = run_magma and ls.enable_magma_diagnostics and ls.magma_available
    ran_magma = False
    if use_magma:
        try:
            res = syntax_check(text, magma_path=ls.magma_path, timeout=ls.magma_timeout)
            ran_magma = True
            if res.timed_out:
                diags.append(_positionless_warning("Magma check timed out; results incomplete"))
            for d in res.diagnostics:
                ident_m = _IDENT_IN_MSG_RE.search(d.message)
                if ident_m:
                    ident = ident_m.group(1)
                    if ident in loaded_names or ident in ls.intrinsic_names:
                        # proven reachable from this document: not an error in context
                        continue
                    if ident in static_undef_idents:
                        # the static diagnostic already covers it, with suggestions — and
                        # Magma's agreement makes it authoritative, so promote it to Error
                        for sd in static_undef_idents[ident]:
                            sd.severity = t.DiagnosticSeverity.Error
                        continue
                    if ident in ls.workspace_symbols:
                        # A workspace sibling defines the name, but nothing proves this
                        # document loads/attaches that file — Magma's error is real for a
                        # standalone run of this file, yet in spec/attach-style projects
                        # the sibling is available at runtime. Keep the signal, soften
                        # the severity.
                        w = _magma_diagnostic(d)
                        w.severity = t.DiagnosticSeverity.Warning
                        w.message += (
                            f" (a workspace file defines '{ident}' — make sure that file"
                            " is load-ed or attached when this file runs)"
                        )
                        diags.append(w)
                        continue
                diags.append(_magma_diagnostic(d))
        except FileNotFoundError:
            ls.magma_available = False
            logger.warning("magma executable disappeared; disabling Magma diagnostics")
        except Exception as exc:
            logger.warning("magma syntax check failed: %s", exc)

    if not ran_magma:
        # fast tree-sitter syntax errors when the Magma pass didn't run
        diags.extend(_tree_sitter_syntax_errors(text))
    return diags


def _magma_diagnostic(d: MagmaDiagnostic) -> t.Diagnostic:
    if getattr(d, "positionless", False):
        return _positionless_warning(d.message) if d.severity == "warning" else (
            t.Diagnostic(
                range=t.Range(start=t.Position(0, 0), end=t.Position(0, 1)),
                message=d.message,
                severity=t.DiagnosticSeverity.Error,
                source="magma",
            )
        )
    line0 = max(0, d.line - 1)
    col0 = max(0, d.col - 1)
    return t.Diagnostic(
        range=t.Range(start=t.Position(line0, col0), end=t.Position(line0, col0 + 1)),
        message=d.message,
        severity=t.DiagnosticSeverity.Error
        if d.severity == "error"
        else t.DiagnosticSeverity.Warning,
        source="magma",
    )


def _positionless_warning(message: str) -> t.Diagnostic:
    return t.Diagnostic(
        range=t.Range(start=t.Position(0, 0), end=t.Position(0, 1)),
        message=message,
        severity=t.DiagnosticSeverity.Warning,
        source="magma",
    )


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
                    message=f"Missing '{node.type}'" if node.is_missing else "Syntax error",
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
    if ls.handbook is not None:
        prose = ls.handbook.doc_markdown(word)
        if prose:
            md = f"{md}\n\n---\n\n{prose}"
    return t.Hover(contents=t.MarkupContent(kind=t.MarkupKind.Markdown, value=md))


@server.feature(t.TEXT_DOCUMENT_COMPLETION)
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
    call = _enclosing_call(text, params.position)
    if call is None or not call.children or call.children[0].type != "identifier":
        return None
    name = call.children[0].text.decode("utf-8", "replace")
    sigs = ls.index.signatures(name)
    if not sigs:
        return None
    shown = sigs[:25]
    active_sig, active_param = _select_signature(shown, _active_parameter(call, params.position))
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
        for s in shown
    ]
    return t.SignatureHelp(
        signatures=infos, active_signature=active_sig, active_parameter=active_param
    )


def _select_signature(sigs, n_commas: int) -> tuple[int, int]:
    """Pick the overload the cursor position fits (first with more args than commas typed;
    else the widest) and clamp the active parameter inside it — an index past the active
    signature's parameter list makes clients omit or mis-highlight the help."""
    idx = next((i for i, s in enumerate(sigs) if len(s.args) > n_commas), None)
    if idx is None:
        idx = max(range(len(sigs)), key=lambda i: len(sigs[i].args))
    return idx, min(n_commas, max(0, len(sigs[idx].args) - 1))


def _active_parameter(call_node, pos: t.Position) -> int:
    """Number of top-level ',' tokens in the argument list before the cursor."""
    n = 0
    for c in call_node.children:
        if c.type == "argument_list":
            for a in c.children:
                if a.type == "," and (
                    a.start_point[0] < pos.line
                    or (a.start_point[0] == pos.line and a.start_point[1] < pos.character)
                ):
                    n += 1
    return n


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
    nl, nc = s.name_pos()
    name_rng = t.Range(t.Position(nl, nc), t.Position(nl, nc + len(s.name)))
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
