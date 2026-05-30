"""Recall of the unknown-intrinsic check: are typo'd intrinsic calls reliably caught?

The differential test (diff_diagnostics.py) measures *precision* (we don't flag valid code). This
measures *recall*: for every call to a known intrinsic in the handbook examples, mutate the name
into a typo (adjacent-char swap / dropped char) and confirm the static check flags it. A miss means
the typo coincidentally collided with another known name or an in-scope binding — the only way a
genuinely-undefined call escapes detection.

Pure static (no Magma). Run: ``uv run python validation/recall.py``.
"""

from __future__ import annotations

from diff_diagnostics import extract_examples  # sibling script (validation/ is on sys.path[0])

from magma_lsp.analysis.scope import analyze
from magma_lsp.analysis.undefined import undefined_intrinsics
from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.store import newest_cached_db


def _typos(name: str) -> list[str]:
    out = []
    if len(name) >= 4:
        # swap two adjacent middle chars
        i = len(name) // 2
        out.append(name[:i] + name[i + 1] + name[i] + name[i + 2 :])
        # drop a middle char
        out.append(name[:i] + name[i + 1 :])
    return [t for t in out if t != name]


def main() -> int:
    names = frozenset(SignatureIndex.from_path(newest_cached_db()).db.intrinsics)
    examples = extract_examples()

    tested = 0
    caught = 0
    missed_examples: list[tuple[str, str]] = []
    for _id, code in examples:
        lines = code.splitlines(keepends=True)
        available, calls = analyze(code)
        for cs in calls:
            if cs.name not in names or cs.line >= len(lines):
                continue  # only mutate real intrinsic calls
            for typo in _typos(cs.name):
                if typo in names or typo in available:
                    continue  # mutation collided with a real/known name; not a clean typo
                # Mutate the exact call-site span (avoids substring collisions like BlockMatrix).
                ln = lines[cs.line]
                mutated_line = ln[: cs.col] + typo + ln[cs.end_col :]
                mutated = "".join([*lines[: cs.line], mutated_line, *lines[cs.line + 1 :]])
                lints = undefined_intrinsics(mutated, names)
                flagged = {lint.message.split("'")[1] for lint in lints}
                tested += 1
                if typo in flagged:
                    caught += 1
                elif len(missed_examples) < 15:
                    missed_examples.append((cs.name, typo))

    print(f"typo injections tested: {tested}")
    if tested:
        print(f"caught: {caught} ({100 * caught / tested:.2f}% recall)")
    print(f"missed: {tested - caught}")
    if missed_examples:
        print("--- sample misses (original -> typo) ---")
        for orig, typo in missed_examples:
            print(f"  {orig} -> {typo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
