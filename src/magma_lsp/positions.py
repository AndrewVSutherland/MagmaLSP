"""Column-coordinate conversions between the three systems the server touches.

Three different units meet at the LSP boundary and agree only on pure-ASCII lines:

- **tree-sitter** points carry UTF-8 *byte* offsets within the line;
- **Python** string indexing (everything in ``analysis/`` and the Magma column mapping in
  ``magma/validate``) counts *code points*;
- **LSP** positions count units of the negotiated position encoding — UTF-16 by default
  (an astral character like ``😀`` or blackboard-bold F is TWO units; even ``é`` differs
  from bytes).

The server's convention: *code points* are the internal canonical unit. Incoming client
positions are decoded via pygls' :class:`~pygls.workspace.position_codec.PositionCodec`
(``position_from_client_units``), outgoing positions are encoded with it
(``position_to_client_units`` / ``range_to_client_units``), and tree-sitter byte columns are
first mapped to code points with :func:`byte_col_to_point` below.
"""

from __future__ import annotations


def byte_col_to_point(line: str, byte_col: int) -> int:
    """Map a UTF-8 byte offset within ``line`` to a 0-based code-point index.

    Out-of-range offsets clamp to the line length; an offset landing inside a multi-byte
    character floors to that character's start (``errors="ignore"`` drops the partial
    sequence). Fast path: an all-ASCII prefix needs no decode.
    """
    if byte_col <= 0:
        return 0
    raw = line.encode("utf-8")
    if byte_col >= len(raw):
        return len(line)
    prefix = raw[:byte_col]
    if prefix.isascii():
        return byte_col
    return len(prefix.decode("utf-8", "ignore"))


def point_col_to_byte(line: str, point_col: int) -> int:
    """Map a 0-based code-point index within ``line`` to a UTF-8 byte offset (for building
    tree-sitter points from internal positions). Out-of-range indexes clamp."""
    if point_col <= 0:
        return 0
    if point_col >= len(line):
        return len(line.encode("utf-8"))
    prefix = line[:point_col]
    if prefix.isascii():
        return point_col
    return len(prefix.encode("utf-8"))
