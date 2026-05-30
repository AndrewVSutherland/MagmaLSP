"""Spec-tree resolution: which package .m files a Magma .spec attaches."""

from __future__ import annotations

import os

from magma_lsp.db.spec import attached_files


def test_resolves_dirs_includes_and_subdir_includes(tmp_path):
    # Build a synthetic spec tree exercising: NAME { }, bare { }, +include, and +subdir/include.
    (tmp_path / "spec").write_text("Area { +Area.spec }\n")
    area = tmp_path / "Area"
    area.mkdir()
    (area / "Area.spec").write_text("{\n  top.m\n  Sub { inner.m }\n}\n+deep/Deep.spec\n")
    (area / "top.m").write_text("intrinsic A() {} return 0; end intrinsic;\n")
    (area / "Sub").mkdir()
    (area / "Sub" / "inner.m").write_text("// inner\n")
    deep = area / "deep"
    deep.mkdir()
    (deep / "Deep.spec").write_text("{ d.m }\n")
    (deep / "d.m").write_text("// deep file relative to the included spec's own dir\n")

    got = {os.path.relpath(f, tmp_path) for f in attached_files(str(tmp_path / "spec"))}
    assert got == {
        os.path.join("Area", "top.m"),
        os.path.join("Area", "Sub", "inner.m"),
        os.path.join("Area", "deep", "d.m"),
    }


def test_excludes_unlisted_files(tmp_path):
    (tmp_path / "spec").write_text("{ listed.m }\n")
    (tmp_path / "listed.m").write_text("// x\n")
    (tmp_path / "unlisted.m").write_text("// y\n")
    got = attached_files(str(tmp_path / "spec"))
    assert any(f.endswith("listed.m") for f in got)
    assert not any(f.endswith("unlisted.m") for f in got)


def test_comments_are_ignored(tmp_path):
    (tmp_path / "spec").write_text("# a comment\n{ a.m # trailing comment\n}\n")
    (tmp_path / "a.m").write_text("// x\n")
    got = attached_files(str(tmp_path / "spec"))
    assert len(got) == 1 and next(iter(got)).endswith("a.m")
