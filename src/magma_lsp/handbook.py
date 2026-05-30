"""Handbook documentation lookup: map an intrinsic name to its prose description.

Builds a name -> (page, anchor) index from ``doc/html/ind-all`` (a flat ``<->``-delimited index;
CLAUDE.md §8), then extracts the ``<BLOCKQUOTE>`` description that follows the intrinsic's anchor
on its ``textNN.htm`` page. Used to enrich hover beyond the short ``{...}`` package doc string.

ind-all line: ``<level><->key<->textNN.htm#anchor<->display``. Level 5 == intrinsic signature.
The key may carry a ``canonical :: actual`` alias form; the intrinsic name is the leading
identifier of the last ``::`` segment.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field

IND_ALL = "ind-all"
_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)")
_ANCHOR_RE = re.compile(r"([\w.-]+\.htm)#(\w+)")
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCKQUOTE_RE = re.compile(r"<BLOCKQUOTE>(.*?)</BLOCKQUOTE>", re.S | re.I)


def _extract_name(key: str) -> str | None:
    segment = key.split("::")[-1].strip()
    m = _NAME_RE.match(segment)
    return m.group(1) if m else None


def _html_to_text(fragment: str) -> str:
    s = fragment
    s = re.sub(r"<P>", "\n\n", s, flags=re.I)
    s = re.sub(r"<BR\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</?(DT|DD|LI|DL|UL|OL)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<SUP>(.*?)</SUP>", r"^\1", s, flags=re.I | re.S)
    s = re.sub(r"<SUB>(.*?)</SUB>", r"_\1", s, flags=re.I | re.S)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


@dataclass
class HandbookIndex:
    html_dir: str
    entries: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (page, anchor)

    @classmethod
    def load(cls, html_dir: str) -> HandbookIndex:
        idx = cls(html_dir=html_dir)
        path = os.path.join(html_dir, IND_ALL)
        if not os.path.isfile(path):
            return idx
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split("<->")
                if len(parts) < 4 or parts[0].strip() != "5":
                    continue
                name = _extract_name(parts[1])
                if not name or name in idx.entries:
                    continue
                m = _ANCHOR_RE.search(parts[2])
                if m:
                    idx.entries[name] = (m.group(1), m.group(2))
        return idx

    def doc_markdown(self, name: str, *, max_chars: int = 1200) -> str | None:
        hit = self.entries.get(name)
        if hit is None:
            return None
        page, anchor = hit
        try:
            with open(os.path.join(self.html_dir, page), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return None
        pos = text.find(f'NAME = "{anchor}"')
        if pos < 0:
            pos = text.find(f'NAME="{anchor}"')
        if pos < 0:
            return None
        m = _BLOCKQUOTE_RE.search(text, pos)
        if m is None:
            return None
        prose = _html_to_text(m.group(1))
        if not prose:
            return None
        if len(prose) > max_chars:
            prose = prose[:max_chars].rsplit(" ", 1)[0] + " …"
        return prose
