"""Position-encoding correctness (issue #15, medium 3): tree-sitter byte columns vs Python
code points vs the client's negotiated units (UTF-16 by default).

The three units agree on ASCII; they diverge on any non-ASCII line (``é`` = 2 bytes / 1 code
point / 1 UTF-16 unit; ``😀`` = 4 bytes / 1 code point / 2 UTF-16 units).
"""

from __future__ import annotations

from lsprotocol import types as t
from pygls.workspace.position_codec import PositionCodec

import magma_lsp.server as srv
from magma_lsp.positions import byte_col_to_point, point_col_to_byte

EMOJI_LINE = 'print "😀"; x := 1;'  # x: byte col 14, code point 11, UTF-16 unit 12
ACCENT_LINE = 's := "é"; y := 2;'  # y: byte col 11, code point 10, UTF-16 unit 10


def test_byte_col_to_point_ascii_identity():
    assert byte_col_to_point("abc def", 4) == 4
    assert byte_col_to_point("", 0) == 0
    assert byte_col_to_point("abc", 99) == 3  # clamps to line length


def test_byte_col_to_point_multibyte():
    assert byte_col_to_point(EMOJI_LINE, 14) == 11  # after the 4-byte emoji
    assert byte_col_to_point(ACCENT_LINE, 11) == 10  # after the 2-byte é
    # an offset inside the emoji floors to the character's start
    assert byte_col_to_point(EMOJI_LINE, 9) == 7


def test_point_col_to_byte_roundtrip():
    for line in (EMOJI_LINE, ACCENT_LINE, "plain ascii"):
        for pt in range(len(line) + 1):
            assert byte_col_to_point(line, point_col_to_byte(line, pt)) == pt
    assert point_col_to_byte(EMOJI_LINE, 99) == len(EMOJI_LINE.encode())  # clamps


def test_lint_diagnostic_converts_byte_columns():
    # a pitfall lint on the `x = 1` after an astral char: tree-sitter reports byte columns
    text = 'print "😀"; x = 1;\n'
    ls = srv.MagmaLanguageServer()
    ls.enable_lints = True
    ls.magma_available = False
    diags = srv._compute_diagnostics(ls, text, run_magma=False)
    d = next(d for d in diags if ":=" in d.message)
    # internal positions are CODE POINTS: the flagged `=` is code point 13 (byte 16)
    assert d.range.start.character == 13
    # and the client-unit encoding of that position is 14 UTF-16 units (the emoji counts 2)
    codec = PositionCodec()  # utf-16 default
    client = codec.range_to_client_units([text.splitlines()[0]], d.range)
    assert client.start.character == 14


def test_ts_point_maps_code_points_to_bytes():
    text = EMOJI_LINE + "\n"
    # cursor on the `x` (code point 11) must become byte column 14 for tree-sitter
    assert srv._ts_point(text, t.Position(0, 11)) == (0, 14)


def test_word_at_uses_code_points():
    text = 'print "😀"; xyz := 1;\n'
    assert srv._word_at(text, t.Position(0, 12)) == "xyz"


def test_active_parameter_on_non_ascii_line():
    # the emoji sits inside the FIRST argument; cursor after the comma => parameter 1
    text = 'Foo("😀x", b);\n'
    tree = srv.new_parser().parse(text.encode())
    call = None
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "call":
            call = n
        stack.extend(n.children)
    assert call is not None
    # code point of `b` is 10; naive byte comparison would also need 13 — both checked
    assert srv._active_parameter(call, t.Position(0, 10), text) == 1
    assert srv._active_parameter(call, t.Position(0, 3), text) == 0


def test_document_symbol_conversion_on_non_ascii_line():
    text = '// é😀 comment\nf := function(n) return n; end function;\n'
    lines = text.splitlines()
    syms = srv.document_symbols(text)
    assert syms
    out = srv._to_document_symbol(syms[0], lines)
    assert out.name == "f"
    assert out.selection_range.start.character == 0
